"""ETL 03 — Write GTEx tissue weights onto PRODUCES edges.

Reads the GTEx v10 gene median-TPM matrix, normalises each tissue column to a
0-1 scale (divide by the tissue's 99th percentile, clip to 1.0), and SETs flat
``tw_<tissue>`` float properties on every PRODUCES edge of each matched gene.

Tissue weights are gene-level in GTEx, so every transcript of a gene receives
its gene's tissue weights. Flat properties are used because Neo4j rejects
map-valued properties (docs/adr/0001-tissue-weights-flat-properties.md).

    etl/.venv/bin/python etl/03_gtex.py
"""

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.id_mapper import strip_version  # noqa: E402
from utils.neo4j_client import close_driver, get_session  # noqa: E402

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
GTEX_FILE = RAW_DIR / "GTEx_Analysis_v10_RNASeQCv2.4.2_gene_median_tpm.gct.gz"
BATCH_SIZE = 2000

# Exact GTEx v10 column name -> internal tissue key (case-sensitive).
# NOTE: the v10 median-TPM GCT uses underscored column names, not the pretty
# labels in the spec ("Whole Blood" / "Brain - Frontal Cortex (BA9)"). Verified
# against the real file header (docs/adr/0003-data-source-urls.md).
GTEX_COLUMN_MAP = {
    "Whole_Blood": "whole_blood",
    "Liver": "liver",
    "Brain_Frontal_Cortex_BA9": "brain_prefrontal_cortex",
}


def load_normalized() -> tuple[pd.DataFrame, list[str]]:
    if not GTEX_FILE.exists():
        raise FileNotFoundError(
            f"{GTEX_FILE} not found. Run etl/00_download.sh first."
        )
    # GCT: line 1 = version, line 2 = dims, line 3 = header.
    df = pd.read_csv(GTEX_FILE, sep="\t", skiprows=2, compression="gzip")

    missing = [c for c in GTEX_COLUMN_MAP if c not in df.columns]
    if missing:
        print("ERROR: expected GTEx tissue column(s) not found:", missing)
        print("Available columns:")
        for c in df.columns:
            print("  -", c)
        sys.exit(1)

    df["ensembl_id"] = df["Name"].map(lambda x: strip_version(str(x)))
    # GTEx ships chrY pseudo-autosomal duplicates (e.g. ..._PAR_Y) that strip to
    # the same Ensembl ID as their chrX entry. Keep one to avoid redundant edge
    # writes and double-counted coverage metrics.
    df = df.drop_duplicates(subset="ensembl_id", keep="first")

    tissue_keys = list(GTEX_COLUMN_MAP.values())
    for gtex_col, key in GTEX_COLUMN_MAP.items():
        values = pd.to_numeric(df[gtex_col], errors="coerce").fillna(0.0)
        p99 = values.quantile(0.99)
        if p99 and p99 > 0:
            df[f"tw_{key}"] = (values / p99).clip(lower=0.0, upper=1.0)
        else:
            df[f"tw_{key}"] = 0.0

    cols = ["ensembl_id"] + [f"tw_{k}" for k in tissue_keys]
    return df[cols], tissue_keys


def count_genes_in_graph(session, ensembl_ids: list[str]) -> int:
    found = 0
    for i in range(0, len(ensembl_ids), 5000):
        chunk = ensembl_ids[i : i + 5000]
        rec = session.run(
            "MATCH (g:Gene) WHERE g.ensembl_id IN $ids RETURN count(g) AS c",
            ids=chunk,
        ).single()
        found += rec["c"]
    return found


def main() -> None:
    start = time.time()
    df, tissue_keys = load_normalized()
    total_genes = len(df)
    print(f"Parsed {total_genes} GTEx genes across tissues: {tissue_keys}")

    set_clause = ", ".join(f"r.tw_{k} = row.tw_{k}" for k in tissue_keys)
    update_query = (
        "UNWIND $rows AS row "
        "MATCH (g:Gene {ensembl_id: row.ensembl_id})-[r:PRODUCES]->() "
        f"SET {set_clause} "
        "RETURN count(r) AS cnt"
    )

    rows = df.to_dict("records")
    edges_updated = 0
    with get_session() as session:
        found_genes = count_genes_in_graph(
            session, [r["ensembl_id"] for r in rows]
        )
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            rec = session.run(update_query, rows=batch).single()
            edges_updated += rec["cnt"]

    matched_pct = (found_genes / total_genes * 100) if total_genes else 0.0
    elapsed = time.time() - start
    print(f"PRODUCES edges updated with tissue weights: {edges_updated}")
    # The meaningful coverage metric is graph-side (how many of OUR PRODUCES
    # edges got weights), reported above. GTEx (~59k genes) is a superset of the
    # HGNC-derived graph (~42k genes), so a large fraction of GTEx genes having
    # no node is expected — they are pseudogenes/novel loci absent from HGNC,
    # and they carry no edges anyway. This is NOT an error.
    print(
        f"GTEx genes matched to a graph node: {found_genes}/{total_genes} "
        f"({matched_pct:.1f}%); the rest are GTEx-only genes with no graph node."
    )
    print(f"Time elapsed: {elapsed:.1f}s")
    close_driver()


if __name__ == "__main__":
    main()
