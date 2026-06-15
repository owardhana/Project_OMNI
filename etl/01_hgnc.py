"""ETL 01 — Load HGNC genes as (:Gene) nodes.

Parses hgnc_complete_set.txt and MERGEs a Gene node per row that has a valid
Ensembl gene ID. Idempotent (MERGE on ensembl_id). Run after 00_download.sh:

    etl/.venv/bin/python etl/01_hgnc.py
"""

import re
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.id_mapper import strip_version  # noqa: E402
from utils.neo4j_client import close_driver, get_session  # noqa: E402

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
HGNC_FILE = RAW_DIR / "hgnc_complete_set.txt"
BATCH_SIZE = 2000

MERGE_QUERY = """
UNWIND $rows AS row
MERGE (g:Gene {ensembl_id: row.ensembl_id})
SET g.hgnc_symbol = row.hgnc_symbol,
    g.hgnc_id = row.hgnc_id,
    g.description = row.description,
    g.chromosome = row.chromosome,
    g.biotype = row.biotype
"""


def parse_chromosome(location: str | None) -> str | None:
    """Derive a chromosome label from an HGNC cytogenetic location.

    '17p13.1' -> '17', 'Xq28' -> 'X', 'mitochondria' -> 'MT'. Returns None when
    the location is empty or unplaced.
    """
    if not location or not isinstance(location, str):
        return None
    loc = location.strip()
    if not loc:
        return None
    if loc.lower().startswith("mitochond"):
        return "MT"
    m = re.match(r"^(\d{1,2}|X|Y|MT)", loc)
    return m.group(1) if m else None


def load_rows() -> list[dict]:
    if not HGNC_FILE.exists():
        raise FileNotFoundError(
            f"{HGNC_FILE} not found. Run etl/00_download.sh first."
        )
    df = pd.read_csv(HGNC_FILE, sep="\t", dtype=str, low_memory=False)

    if "ensembl_gene_id" not in df.columns or "symbol" not in df.columns:
        raise KeyError(
            "Expected columns 'ensembl_gene_id' and 'symbol' in HGNC file; "
            f"found: {list(df.columns)[:20]}..."
        )

    # Only rows with a usable Ensembl gene ID.
    df = df[df["ensembl_gene_id"].notna()]
    df = df[df["ensembl_gene_id"].str.strip() != ""]

    # Chromosome: prefer an explicit column, else parse the cytogenetic location.
    chrom_source = "chromosome" if "chromosome" in df.columns else "location"
    name_col = "name" if "name" in df.columns else "symbol"

    rows: list[dict] = []
    for _, r in df.iterrows():
        ensembl = strip_version(str(r["ensembl_gene_id"]).strip())
        if not ensembl:
            continue
        chrom_raw = r.get(chrom_source)
        chromosome = (
            str(chrom_raw).strip()
            if chrom_source == "chromosome" and pd.notna(chrom_raw)
            else parse_chromosome(chrom_raw if pd.notna(chrom_raw) else None)
        )
        rows.append(
            {
                "ensembl_id": ensembl,
                "hgnc_symbol": str(r["symbol"]).strip(),
                "hgnc_id": str(r.get("hgnc_id") or "").strip() or None,
                "description": str(r.get(name_col) or "").strip() or None,
                "chromosome": chromosome,
                "biotype": "protein_coding",
            }
        )
    return rows


def main() -> None:
    start = time.time()
    rows = load_rows()
    total = len(rows)
    print(f"Parsed {total} HGNC rows with valid Ensembl IDs.")

    created = 0
    with get_session() as session:
        for i in range(0, total, BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            summary = session.run(MERGE_QUERY, rows=batch).consume()
            created += summary.counters.nodes_created

    merged = total - created
    elapsed = time.time() - start
    print(
        f"Gene nodes created: {created}, merged (existing): {merged}, "
        f"total processed: {total}"
    )
    print(f"Time elapsed: {elapsed:.1f}s")
    close_driver()


if __name__ == "__main__":
    main()
