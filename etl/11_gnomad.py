"""ETL 11 — Enrich Gene nodes with gnomAD loss-of-function intolerance (pLI).

Bulk-file enrichment (06_data_vision.md Pattern 1). Reads the gnomAD v4 gene
constraint metrics and SETs ``pli_score`` on Gene nodes (matched by hgnc_symbol).
The constraint file has one row per transcript; we prefer the MANE Select
transcript's ``lof.pLI`` for the gene-level score, falling back to the first
non-MANE row if a gene has no MANE Select transcript.

Format discipline (ADR-0003): required columns are checked against the header
before the full read.

    etl/.venv/bin/python etl/11_gnomad.py
"""

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.neo4j_client import close_driver, get_session  # noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
GNOMAD_FILE = _PROJECT_ROOT / "data" / "raw" / "gnomad_v4_constraint.tsv"
REQUIRED_COLUMNS = ["gene", "lof.pLI"]
WRITE_BATCH = 5000

SET_QUERY = """
UNWIND $rows AS r
MATCH (g:Gene {hgnc_symbol: r.gene})
SET g.pli_score = r.pli
RETURN count(g) AS c
"""


def main() -> None:
    start = time.time()
    if not GNOMAD_FILE.exists():
        raise FileNotFoundError(
            f"{GNOMAD_FILE} not found. Run etl/00_download.sh first."
        )

    header = pd.read_csv(GNOMAD_FILE, sep="\t", nrows=0)
    missing = [c for c in REQUIRED_COLUMNS if c not in header.columns]
    if missing:
        print(f"ABORT: gnomAD constraint missing required columns: {missing}")
        print(f"Columns present: {list(header.columns)}")
        sys.exit(1)
    has_mane = "mane_select" in header.columns
    use_cols = REQUIRED_COLUMNS + (["mane_select"] if has_mane else [])

    df = pd.read_csv(GNOMAD_FILE, sep="\t", dtype=str, usecols=use_cols)

    gene_to_pli: dict[str, float] = {}
    mane_genes: set[str] = set()
    manes = df["mane_select"] if has_mane else [None] * len(df)
    for gene, pli_raw, mane in zip(df["gene"], df["lof.pLI"], manes):
        if not isinstance(gene, str) or not gene.strip():
            continue
        if not isinstance(pli_raw, str) or pli_raw.strip() in ("", "NA"):
            continue
        try:
            pli = float(pli_raw)
        except ValueError:
            continue
        gene = gene.strip()
        is_mane = isinstance(mane, str) and mane.strip().lower() == "true"
        if is_mane:
            gene_to_pli[gene] = pli
            mane_genes.add(gene)
        elif gene not in mane_genes and gene not in gene_to_pli:
            gene_to_pli[gene] = pli
    print(f"Genes with a pLI value: {len(gene_to_pli)}")

    rows = [{"gene": g, "pli": v} for g, v in gene_to_pli.items()]
    enriched = 0
    with get_session() as session:
        for i in range(0, len(rows), WRITE_BATCH):
            rec = session.run(SET_QUERY, rows=rows[i : i + WRITE_BATCH]).single()
            enriched += rec["c"] if rec else 0

    elapsed = time.time() - start
    print(f"Gene nodes enriched with pli_score: {enriched}")
    print(f"Time elapsed: {elapsed:.1f}s")
    close_driver()


if __name__ == "__main__":
    main()
