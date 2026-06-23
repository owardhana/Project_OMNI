"""ETL 05 — Mint the full proteome as Protein nodes + wire them to genomics.

Runs after 01-04. Steps:

1. Mint a (:Protein {uniprot_id}) for EVERY HGNC gene that resolves to a UniProt
   accession (the ~20k human proteome), entity_kind='protein'. (ADR-0010 collapsed
   the former TF-only slice and the separate 05b full-proteome step into this one
   script — see "History" below.)
2. Tie each protein down to its molecule, so a protein is never orphaned:
     - (:Transcript)-[:TRANSLATES_TO]->(:Protein)  primary, from the GENCODE
       SwissProt metadata (ENST -> UniProt), for transcripts present in the graph.
     - (:Gene)-[:ENCODES]->(:Protein)              fallback, only when a protein got
       no transcript link.
3. Tag the transcription-factor subtype. The TFs are the proteins whose symbol
   sources a REGULATES edge (DoRothEA, wired by 04). `subtype='transcription_factor'`
   is what the frontend colours amber and what `is_tf` keys off — every OTHER protein
   stays generic (violet). This MUST happen after 04 (it reads REGULATES).
4. Migrate REGULATES from gene-sourced to protein-sourced, PRESERVING edge
   properties (incl. citation work): for each (g:Gene)-[r:REGULATES]->(target), where
   g's protein p exists (matched by hgnc_symbol), MERGE (p)-[:REGULATES]->(target)
   copying r's props, then delete r.

Idempotent: MERGE on uniprot_id makes re-mints no-ops; TF symbols are read from
REGULATES regardless of whether the source is still a Gene (pre-migration) or already
a Protein (post-migration); the migration only ever finds Gene-sourced edges.

History: ADR-0004 originally scoped this to the TF slice (~117 proteins) and listed
"full proteome now" as deferred; ADR-0010 loaded the full proteome (initially in a
separate 05b script) to connect the Phase-6 metabolite layer (CATALYSES). With the
full proteome now minted here, 07_string builds PPI over the whole proteome, so its
STRING_MIN_CONFIDENCE is raised (0.95) to keep INTERACTS_WITH at a sane volume.

See docs/adr/0004-transcription-factors-as-proteins.md and docs/adr/0010-full-proteome.md.

    etl/.venv/bin/python etl/05_proteins.py
"""

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.id_mapper import strip_version  # noqa: E402
from utils.neo4j_client import close_driver, get_session  # noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = _PROJECT_ROOT / "data" / "raw"
SWISSPROT_FILE = RAW_DIR / "gencode.v46.metadata.SwissProt.gz"
HGNC_FILE = RAW_DIR / "hgnc_complete_set.txt"
BATCH_SIZE = 2000


def load_swissprot_map() -> dict[str, list[str]]:
    """uniprot accession -> list of unversioned ENST ids (from GENCODE metadata)."""
    if not SWISSPROT_FILE.exists():
        raise FileNotFoundError(
            f"{SWISSPROT_FILE} not found. Run etl/00_download.sh first."
        )
    # 3 columns: versioned ENST, UniProt accession, versioned UniProt.
    df = pd.read_csv(
        SWISSPROT_FILE, sep="\t", header=None,
        names=["enst", "uniprot", "uniprot_v"], dtype=str, compression="gzip",
    )
    out: dict[str, list[str]] = {}
    for enst, uniprot in zip(df["enst"], df["uniprot"]):
        if not isinstance(enst, str) or not isinstance(uniprot, str):
            continue
        out.setdefault(uniprot.strip(), []).append(strip_version(enst.strip()))
    return out


def load_proteome_rows() -> list[dict]:
    """Every HGNC gene with both a UniProt accession and an Ensembl gene id.

    Returns rows {symbol, uniprot, ensembl}. uniprot_ids is pipe-separated in HGNC;
    we take the first (canonical) accession, matching the SwissProt key.
    """
    if not HGNC_FILE.exists():
        raise FileNotFoundError(
            f"{HGNC_FILE} not found. Run etl/00_download.sh first."
        )
    df = pd.read_csv(
        HGNC_FILE, sep="\t", dtype=str,
        usecols=lambda c: c in ("symbol", "ensembl_gene_id", "uniprot_ids"),
    )
    rows: list[dict] = []
    seen: set[str] = set()
    for sym, ens, uni in zip(
        df.get("symbol", []), df.get("ensembl_gene_id", []), df.get("uniprot_ids", [])
    ):
        if not (isinstance(sym, str) and isinstance(ens, str) and isinstance(uni, str)):
            continue
        uni = uni.split("|")[0].strip()
        if not uni or not ens.strip():
            continue
        if uni in seen:  # one Protein per accession (first gene wins)
            continue
        seen.add(uni)
        rows.append({"symbol": sym.strip(), "uniprot": uni, "ensembl": ens.strip()})
    return rows


def main() -> None:
    start = time.time()

    rows = load_proteome_rows()
    swissprot = load_swissprot_map()
    print(f"HGNC genes resolving to a UniProt accession: {len(rows)}")

    with get_session() as session:
        before = session.run("MATCH (p:Protein) RETURN count(p) AS c").single()["c"]

        # 1. Mint Protein nodes (whole proteome). ON CREATE SET so a re-run never
        #    clobbers a node already tagged with its TF subtype.
        for i in range(0, len(rows), BATCH_SIZE):
            session.run(
                """
                UNWIND $rows AS row
                MERGE (p:Protein {uniprot_id: row.uniprot})
                ON CREATE SET p.hgnc_symbol = row.symbol,
                              p.entity_kind = 'protein',
                              p.source_db = 'HGNC'
                """,
                rows=rows[i : i + BATCH_SIZE],
            ).consume()

        # 2a. TRANSLATES_TO from transcripts present in the graph.
        translates_links = [
            {"uniprot": r["uniprot"], "enst": enst}
            for r in rows
            for enst in swissprot.get(r["uniprot"], [])
        ]
        translates_created = 0
        for i in range(0, len(translates_links), BATCH_SIZE):
            rec = session.run(
                """
                UNWIND $links AS link
                MATCH (p:Protein {uniprot_id: link.uniprot})
                MATCH (t:Transcript {ensembl_tx_id: link.enst})
                MERGE (t)-[rel:TRANSLATES_TO]->(p)
                ON CREATE SET rel.source_db = 'GENCODE_SwissProt'
                RETURN count(rel) AS c
                """,
                links=translates_links[i : i + BATCH_SIZE],
            ).single()
            translates_created += rec["c"] if rec else 0

        # 2b. ENCODES fallback for proteins with NO transcript link.
        encodes_created = 0
        for i in range(0, len(rows), BATCH_SIZE):
            rec = session.run(
                """
                UNWIND $rows AS row
                MATCH (p:Protein {uniprot_id: row.uniprot})
                WHERE NOT ( (:Transcript)-[:TRANSLATES_TO]->(p) )
                MATCH (g:Gene {ensembl_id: row.ensembl})
                MERGE (g)-[rel:ENCODES]->(p)
                ON CREATE SET rel.source_db = 'HGNC'
                RETURN count(rel) AS c
                """,
                rows=rows[i : i + BATCH_SIZE],
            ).single()
            encodes_created += rec["c"] if rec else 0

        # 3. Tag the TF subtype: proteins whose symbol sources a REGULATES edge.
        #    (s is a Gene pre-migration or a Protein post-migration; both carry
        #    hgnc_symbol.) This is the ONLY thing that makes a protein "amber"/is_tf.
        tf_rec = session.run(
            """
            MATCH (s)-[:REGULATES]->() WHERE s.hgnc_symbol IS NOT NULL
            WITH collect(DISTINCT s.hgnc_symbol) AS tf_syms
            MATCH (p:Protein) WHERE p.hgnc_symbol IN tf_syms
            SET p.subtype = 'transcription_factor'
            RETURN count(p) AS c
            """
        ).single()
        tf_tagged = tf_rec["c"] if tf_rec else 0

        # 4. Migrate REGULATES: Gene-sourced -> Protein-sourced, preserving props.
        migrate_rec = session.run(
            """
            MATCH (g:Gene)-[r:REGULATES]->(target:Gene)
            MATCH (p:Protein {hgnc_symbol: g.hgnc_symbol})
            MERGE (p)-[r2:REGULATES]->(target)
            SET r2 += properties(r)
            DELETE r
            RETURN count(r2) AS migrated
            """
        ).single()
        migrated = migrate_rec["migrated"] if migrate_rec else 0

        after = session.run("MATCH (p:Protein) RETURN count(p) AS c").single()["c"]
        wired = session.run(
            "MATCH (p:Protein) WHERE (:Transcript)-[:TRANSLATES_TO]->(p) "
            "OR (:Gene)-[:ENCODES]->(p) RETURN count(DISTINCT p) AS c"
        ).single()["c"]
        leftover = session.run(
            "MATCH (:Gene)-[r:REGULATES]->(:Gene) RETURN count(r) AS c"
        ).single()["c"]

    elapsed = time.time() - start
    print(f"Protein nodes: {before} -> {after} (+{after - before} minted)")
    print(f"TRANSLATES_TO edges created: {translates_created}")
    print(f"ENCODES (fallback) edges created: {encodes_created}")
    print(f"Proteins with a molecule link (TRANSLATES_TO or ENCODES): {wired}")
    print(f"TF proteins tagged subtype='transcription_factor': {tf_tagged}")
    print(f"REGULATES migrated to Protein source: {migrated}")
    print(f"REGULATES still Gene->Gene (TF lacked a protein): {leftover}")
    print(f"Time elapsed: {elapsed:.1f}s")
    close_driver()


if __name__ == "__main__":
    main()
