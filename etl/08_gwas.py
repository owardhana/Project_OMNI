"""ETL 08 — GWAS Catalog: Variant + Disease nodes and their associations.

Topology from a bulk file (docs/data-architecture.md Pattern 1). Reads the GWAS Catalog
ontology-annotated full associations (a zip downloaded by 00_download.sh), keeps
genome-wide-significant hits, and MERGEs:
  - (:Disease {ontology_id})           from MAPPED_TRAIT_URI / MAPPED_TRAIT
  - (:Variant {rsid})                  from SNPS (fallback chr:pos:NA:NA)
  - (:Variant)-[:ASSOCIATED_WITH]->(:Disease)   p_value, source_db, pmids
  - (:Variant)-[:IN_GENE]->(:Gene)              via MAPPED_GENE (existing genes)
  - (:Gene)-[:IMPLICATED_IN]->(:Disease)        rolled up from the two above

Significance threshold from GWAS_MIN_SIGNIFICANCE env var (default 5e-8). ETL
reads the env var directly rather than importing backend settings.

Format discipline (ADR-0003): the GWAS Catalog TSV layout has changed between
releases; if any required column is missing we print the columns we DID find and
abort rather than silently mis-parsing.

    etl/.venv/bin/python etl/08_gwas.py
"""

import os
import re
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.neo4j_client import close_driver, get_session  # noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
GWAS_ZIP = (
    _PROJECT_ROOT / "data" / "raw"
    / "gwas-catalog-associations_ontology-annotated-full.zip"
)
NODE_BATCH = 5000
EDGE_BATCH = 2000

REQUIRED_COLUMNS = [
    "SNPS", "P-VALUE", "CHR_ID", "CHR_POS",
    "MAPPED_GENE", "MAPPED_TRAIT", "MAPPED_TRAIT_URI", "PUBMEDID",
]

_RSID_RE = re.compile(r"rs\d+")


def _first_val(raw) -> str | None:
    """First ';'-separated token, or None for blanks/NaN."""
    s = str(raw).split(";")[0].strip()
    return s if s and s.lower() != "nan" else None


def _to_int(raw) -> int | None:
    s = _first_val(raw)
    if s is None:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def variant_key(snps, chr_id, chr_pos) -> str | None:
    """rsid (first in SNPS) or the chr:pos:NA:NA fallback, else None."""
    m = _RSID_RE.search(str(snps))
    if m:
        return m.group(0)
    c, p = _first_val(chr_id), _first_val(chr_pos)
    return f"chr{c}:{p}:NA:NA" if (c and p) else None


def parse_traits(uri_field, name_field) -> list[tuple[str, str]]:
    """(ontology_id, name) pairs from MAPPED_TRAIT_URI / MAPPED_TRAIT.

    ontology_id is the last URL path segment (EFO_0001360, Orphanet_..., MONDO_...).

    The GWAS Catalog separates multiple mapped traits with ", " (comma-SPACE). Trait
    names can contain BARE commas (chemical names, e.g.
    "1,4-dihydro-1-Methyl-4-oxo-3-pyridinecarboxamide measurement"), so splitting on a
    bare "," fragments those names and misaligns them with their URIs — producing junk
    Disease.name values like "1" or "4-androsten-3alpha". Splitting on ", " keeps such
    names intact and stays index-aligned with the URIs (URLs never contain ", ").
    """
    uris = [u.strip() for u in str(uri_field).split(", ") if u.strip()]
    names = [n.strip() for n in str(name_field).split(", ")]
    out: list[tuple[str, str]] = []
    for i, uri in enumerate(uris):
        if uri.lower() == "nan":
            continue
        oid = uri.rstrip("/").split("/")[-1]
        if not oid or oid.lower() == "nan":
            continue
        name = names[i] if i < len(names) else ""
        # A purely-numeric or empty name is a parse artifact -> fall back to the id.
        if not name or name.isdigit():
            name = oid
        out.append((oid, name))
    return out


def parse_genes(field) -> list[str]:
    s = str(field)
    if not s or s.lower() == "nan":
        return []
    parts = re.split(r"[;,]| - ", s)
    return [p.strip() for p in parts if p.strip() and p.strip().lower() != "nan"]


def _batched(session, query: str, rows: list[dict], size: int) -> None:
    for i in range(0, len(rows), size):
        session.run(query, rows=rows[i : i + size]).consume()


def main() -> None:
    start = time.time()
    if not GWAS_ZIP.exists():
        raise FileNotFoundError(f"{GWAS_ZIP} not found. Run etl/00_download.sh first.")

    threshold = float(os.getenv("GWAS_MIN_SIGNIFICANCE", "5e-8"))
    print(f"GWAS p-value threshold: <= {threshold}")

    df = pd.read_csv(
        GWAS_ZIP, sep="\t", compression="zip", dtype=str, low_memory=False,
    )
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        print(f"ABORT: GWAS TSV is missing required columns: {missing}")
        print(f"Columns present: {list(df.columns)}")
        sys.exit(1)

    pval = pd.to_numeric(df["P-VALUE"], errors="coerce")
    mask = pval.notna() & (pval <= threshold)
    sub = df[mask]
    print(f"Associations at genome-wide significance: {len(sub)}")

    # Extract needed columns as plain lists (avoids itertuples renaming columns
    # whose names aren't valid Python identifiers, e.g. "P-VALUE").
    col = {c: sub[c].tolist() for c in REQUIRED_COLUMNS}
    pvals = pval[mask].tolist()

    diseases: dict[str, str] = {}
    variants: dict[str, dict] = {}
    assoc_best: dict[tuple[str, str], dict] = {}
    in_gene: set[tuple[str, str]] = set()
    rsid_to_genes: dict[str, set[str]] = {}
    rsid_to_diseases: dict[str, set[str]] = {}

    for snp, cid, cpos, mgene, mtrait, muri, pmid_raw, pval_row in zip(
        col["SNPS"], col["CHR_ID"], col["CHR_POS"], col["MAPPED_GENE"],
        col["MAPPED_TRAIT"], col["MAPPED_TRAIT_URI"], col["PUBMEDID"], pvals,
    ):
        vkey = variant_key(snp, cid, cpos)
        if vkey is None:
            continue
        traits = parse_traits(muri, mtrait)
        if not traits:
            continue

        variants.setdefault(
            vkey, {"rsid": vkey, "chr": _first_val(cid), "pos": _to_int(cpos)}
        )
        pmid = _first_val(pmid_raw)

        for oid, name in traits:
            diseases[oid] = name
            key = (vkey, oid)
            prev = assoc_best.get(key)
            if prev is None or pval_row < prev["p_value"]:
                assoc_best[key] = {
                    "rsid": vkey, "ontology_id": oid,
                    "p_value": float(pval_row), "pmids": [pmid] if pmid else [],
                }
            rsid_to_diseases.setdefault(vkey, set()).add(oid)

        for gene in parse_genes(mgene):
            in_gene.add((vkey, gene))
            rsid_to_genes.setdefault(vkey, set()).add(gene)

    implicated = {
        (g, oid)
        for rsid, genes in rsid_to_genes.items()
        for g in genes
        for oid in rsid_to_diseases.get(rsid, ())
    }

    print(
        f"Parsed: {len(diseases)} diseases, {len(variants)} variants, "
        f"{len(assoc_best)} associations, {len(in_gene)} variant-gene links, "
        f"{len(implicated)} gene-disease rollups"
    )

    with get_session() as session:
        _batched(
            session,
            "UNWIND $rows AS d MERGE (dis:Disease {ontology_id: d.ontology_id}) "
            "SET dis.name = d.name, dis.description = d.name",
            [{"ontology_id": k, "name": v} for k, v in diseases.items()],
            NODE_BATCH,
        )
        _batched(
            session,
            "UNWIND $rows AS v MERGE (var:Variant {rsid: v.rsid}) "
            "SET var.chromosome = v.chr, var.position_grch38 = v.pos, "
            "    var.consequence_type = 'intergenic'",
            list(variants.values()),
            NODE_BATCH,
        )
        _batched(
            session,
            "UNWIND $rows AS a MATCH (var:Variant {rsid: a.rsid}) "
            "MATCH (dis:Disease {ontology_id: a.ontology_id}) "
            "MERGE (var)-[r:ASSOCIATED_WITH]->(dis) "
            "SET r.p_value = a.p_value, r.source_db = 'GWAS_Catalog', r.pmids = a.pmids",
            list(assoc_best.values()),
            EDGE_BATCH,
        )
        _batched(
            session,
            "UNWIND $rows AS a MATCH (var:Variant {rsid: a.rsid}) "
            "MATCH (g:Gene {hgnc_symbol: a.mapped_gene}) "
            "MERGE (var)-[:IN_GENE {consequence_type: 'intergenic', "
            "    source_db: 'GWAS_Catalog'}]->(g)",
            [{"rsid": r, "mapped_gene": g} for r, g in in_gene],
            EDGE_BATCH,
        )
        _batched(
            session,
            "UNWIND $rows AS row MATCH (g:Gene {hgnc_symbol: row.gene}) "
            "MATCH (dis:Disease {ontology_id: row.ontology_id}) "
            "MERGE (g)-[:IMPLICATED_IN]->(dis)",
            [{"gene": g, "ontology_id": oid} for g, oid in implicated],
            EDGE_BATCH,
        )

    elapsed = time.time() - start
    print(f"Disease nodes merged: {len(diseases)}")
    print(f"Variant nodes merged: {len(variants)}")
    print(f"ASSOCIATED_WITH edges merged: {len(assoc_best)}")
    print(f"IN_GENE edges merged (only where gene exists): {len(in_gene)}")
    print(f"Time elapsed: {elapsed:.1f}s")
    close_driver()


if __name__ == "__main__":
    main()
