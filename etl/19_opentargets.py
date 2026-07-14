"""ETL 19 — Curated gene-disease associations from Open Targets (Pillar 1c, ADR-0016).

A third, *orthogonal* gene→disease evidence class alongside `IMPLICATED_IN` (GWAS
statistical) and `DIFFERENTIALLY_EXPRESSED` (TCGA expression): **expert/literature
curation** of gene-disease causality — the mendelian / rare-disease knowledge neither
common-variant GWAS nor cancer expression captures.

Source: **Open Targets Platform 26.06** per-datasource *evidence* parquet, restricted to
the four **curated** datasources (ADR-0016) — never the aggregate `overall` score, which
blends the GWAS/ClinVar/COSMIC/expression already in the graph (double-count guard):
  ot_evidence_clingen · ot_evidence_gene2phenotype · ot_evidence_genomics_england ·
  ot_evidence_orphanet

Open Targets is **EFO/MONDO-native**, so disease reconciliation is a direct id match to
existing EFO-keyed Disease nodes — no lossy CUI crosswalk, no yield spike (ADR-0016).
Associations whose gene or disease is not already in the graph are dropped (EFO-only rule;
Disease stays EFO-keyed). Edge: `(:Gene)-[:GENE_DISEASE_ASSOC {gda_score, ot_datasources,
n_evidence, source_db}]->(:Disease)`. Enrichment on existing nodes — never mints Gene/Disease.

    etl/.venv/bin/python etl/19_opentargets.py
"""

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.neo4j_client import close_driver, get_session  # noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = _PROJECT_ROOT / "data" / "raw"
CURATED_FILES = [
    "ot_evidence_clingen.parquet",
    "ot_evidence_gene2phenotype.parquet",
    "ot_evidence_genomics_england.parquet",
    "ot_evidence_orphanet.parquet",
]
# OT harmonized evidence schema: targetId (ENSG), diseaseId (EFO/MONDO), datasourceId, score.
NEEDED = ["targetId", "diseaseId", "datasourceId", "score"]
WRITE_BATCH = 2000
SOURCE_DB = "OpenTargets"
SOURCE_VERSION = "26.06"

# Both endpoints must already exist (EFO-only reconciliation, ADR-0016): a curated
# association to a gene or disease absent from the graph is dropped, not created.
MERGE_QUERY = """
UNWIND $rows AS r
MATCH (g:Gene {ensembl_id: r.gene})
MATCH (d:Disease {ontology_id: r.disease})
MERGE (g)-[e:GENE_DISEASE_ASSOC]->(d)
SET e.gda_score = r.score,
    e.ot_datasources = r.datasources,
    e.n_evidence = r.n_evidence,
    e.source_db = $source_db,
    e.source_version = $source_version
RETURN count(e) AS c
"""


def main() -> None:
    start = time.time()
    frames = []
    for fname in CURATED_FILES:
        path = RAW_DIR / fname
        if not path.exists():
            print(f"ABORT: {fname} not in {RAW_DIR} (Open Targets curated evidence).")
            sys.exit(1)
        df = pd.read_parquet(path, columns=NEEDED)
        missing = [c for c in NEEDED if c not in df.columns]
        if missing:
            print(f"ABORT: {fname} missing columns {missing}. Present: {list(df.columns)}")
            sys.exit(1)
        print(f"  {fname}: {len(df)} evidence rows")
        frames.append(df)

    ev = pd.concat(frames, ignore_index=True)
    ev = ev.dropna(subset=["targetId", "diseaseId", "score"])
    print(f"Total curated evidence rows: {len(ev)}")

    # Aggregate to one edge per (gene, disease): max score + contributing datasources.
    agg = (
        ev.groupby(["targetId", "diseaseId"])
        .agg(
            score=("score", "max"),
            n_evidence=("score", "size"),
            datasources=("datasourceId", lambda s: sorted(set(s))),
        )
        .reset_index()
    )
    print(f"Distinct curated gene-disease pairs: {len(agg)}")

    rows = [
        {
            "gene": t, "disease": d,
            "score": round(float(sc), 4), "n_evidence": int(n), "datasources": ds,
        }
        for t, d, sc, n, ds in zip(
            agg["targetId"], agg["diseaseId"], agg["score"],
            agg["n_evidence"], agg["datasources"],
        )
    ]

    created = 0
    with get_session() as session:
        for i in range(0, len(rows), WRITE_BATCH):
            rec = session.run(
                MERGE_QUERY, rows=rows[i : i + WRITE_BATCH],
                source_db=SOURCE_DB, source_version=SOURCE_VERSION,
            ).single()
            created += rec["c"] if rec else 0
        session.run(
            "MERGE (s:DataSource {name: $name}) "
            "SET s.loaded_at = datetime(), s.source_db = $source_db, "
            "    s.source_version = $source_version, "
            "    s.curated_pairs = $pairs, s.edges_created = $created",
            name="19_opentargets", source_db=SOURCE_DB, source_version=SOURCE_VERSION,
            pairs=len(rows), created=created,
        ).consume()

    dropped = len(rows) - created
    elapsed = time.time() - start
    print(f"GENE_DISEASE_ASSOC edges created: {created}")
    print(f"Pairs dropped (gene/disease not in graph — EFO-only): {dropped}")
    print(f"Time elapsed: {elapsed:.1f}s")
    close_driver()


if __name__ == "__main__":
    main()
