"""ETL 12 — COSMIC Cancer Gene Census: flag cancer genes on existing Gene nodes.

Bulk-file enrichment (06_data_vision.md Pattern 1 / 09_data_catalog.md row 11).
Reads the COSMIC Cancer Gene Census CSV and SETs ``cancer_gene = true`` plus the
``cosmic_tier`` ("1" or "2") on Gene nodes matched by HGNC symbol. This populates
the ``cancer_gene`` bool that has always been on the Gene model but was never
sourced (it stays null for genes not in the Census — null = "not checked", never
False).

Enrichment, not topology: this script only ever MATCHes existing Gene nodes and
SETs properties on them — it never CREATEs a Gene (symbols absent from the graph
are skipped and counted), matching the discipline of 11_gnomad.py. Provenance
(source_db = COSMIC_CGC, source_version = v99) is recorded on the DataSource node,
not clobbered onto the multi-sourced Gene node.

Format discipline (ADR-0003): required columns are checked against the header and
the script aborts with the columns it DID find rather than silently mis-parsing.

    etl/.venv/bin/python etl/12_cosmic.py
"""

import gzip
import io
import sys
import tarfile
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.neo4j_client import close_driver, get_session  # noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = _PROJECT_ROOT / "data" / "raw"
# The COSMIC CGC download comes in several shapes depending on version/path:
#   - v104 tar:   Cosmic_CancerGeneCensus_Tsv_*_*.tar  (contains a *.tsv.gz)
#   - plain gz:   *CancerGeneCensus*.tsv.gz
#   - legacy csv: cosmic_cancer_gene_census.csv
# Resolve whichever is present (newest by name); the table is read uniformly below.
COSMIC_CANDIDATES = [
    "Cosmic_CancerGeneCensus_Tsv_*.tar",
    "*CancerGeneCensus*.tsv.gz",
    "cosmic_cancer_gene_census.csv",
]
# Column names differ across releases (v104 uppercases them).
SYMBOL_COLS = ["GENE_SYMBOL", "Gene Symbol", "Gene symbol"]
TIER_COLS = ["TIER", "Tier"]
WRITE_BATCH = 5000
SOURCE_DB = "COSMIC_CGC"
SOURCE_VERSION = "v104"

# MATCH only — never CREATE. Returns the count actually matched so we can report
# how many Census symbols were absent from the graph.
SET_QUERY = """
UNWIND $rows AS r
MATCH (g:Gene {hgnc_symbol: r.symbol})
SET g.cancer_gene = true, g.cosmic_tier = r.tier
RETURN count(g) AS c
"""


def _resolve_cosmic_file() -> Path:
    for pattern in COSMIC_CANDIDATES:
        hits = sorted(RAW_DIR.glob(pattern))
        if hits:
            return hits[-1]
    raise FileNotFoundError(
        f"No COSMIC CGC file in {RAW_DIR} (looked for {COSMIC_CANDIDATES}). "
        "COSMIC requires a free account — download the Cancer Gene Census manually."
    )


def _first_col(columns, candidates):
    for c in candidates:
        if c in columns:
            return c
    return None


def _read_cosmic_df(path: Path) -> pd.DataFrame:
    """Read the CGC table from a v104 .tar (inner *.tsv.gz), a plain .tsv.gz, or a
    legacy .csv — guarding the .csv path against the HTML login page Sanger serves
    to unauthenticated clients (ADR-0003 discipline)."""
    name = path.name.lower()
    if name.endswith(".tar"):
        with tarfile.open(path) as tar:
            member = next(
                (m for m in tar.getmembers()
                 if "cancergenecensus" in m.name.lower()
                 and m.name.lower().endswith((".tsv.gz", ".csv.gz", ".tsv", ".csv"))),
                None,
            )
            if member is None:
                print(f"ABORT: no Cancer Gene Census table inside {path.name}; "
                      f"members: {[m.name for m in tar.getmembers()]}")
                sys.exit(1)
            raw = tar.extractfile(member).read()
            mname = member.name.lower()
            data = gzip.decompress(raw) if mname.endswith(".gz") else raw
            sep = "\t" if ".tsv" in mname else ","
            return pd.read_csv(io.BytesIO(data), sep=sep, dtype=str, low_memory=False)
    if name.endswith((".tsv.gz", ".tsv")):
        return pd.read_csv(path, sep="\t", dtype=str, compression="infer", low_memory=False)
    # legacy .csv — detect the HTML login page before pandas mis-parses it.
    with open(path, "rb") as fh:
        head = fh.read(64).lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        print(f"ABORT: {path.name} is an HTML page (a COSMIC login page), not data. "
              "COSMIC requires a free account — download the Cancer Gene Census "
              "manually into data/raw/ (see 00_download.sh).")
        sys.exit(1)
    return pd.read_csv(path, dtype=str, low_memory=False)


def main() -> None:
    start = time.time()
    cosmic_file = _resolve_cosmic_file()
    print(f"COSMIC CGC file: {cosmic_file.name}")
    df = _read_cosmic_df(cosmic_file)

    sym_col = _first_col(df.columns, SYMBOL_COLS)
    tier_col = _first_col(df.columns, TIER_COLS)
    if sym_col is None or tier_col is None:
        print(f"ABORT: COSMIC CGC missing symbol/tier columns "
              f"(symbol->{sym_col}, tier->{tier_col}).")
        print(f"Columns present: {list(df.columns)}")
        sys.exit(1)

    # One row per gene; dedup on symbol, keeping the strongest (lowest) tier.
    gene_to_tier: dict[str, str] = {}
    for symbol, tier in zip(df[sym_col], df[tier_col]):
        if not isinstance(symbol, str) or not symbol.strip():
            continue
        symbol = symbol.strip()
        tier = tier.strip() if isinstance(tier, str) and tier.strip() else "1"
        prev = gene_to_tier.get(symbol)
        if prev is None or tier < prev:  # "1" < "2": prefer tier 1
            gene_to_tier[symbol] = tier
    print(f"Cancer Gene Census symbols parsed: {len(gene_to_tier)}")

    rows = [{"symbol": s, "tier": t} for s, t in gene_to_tier.items()]
    flagged = 0
    with get_session() as session:
        for i in range(0, len(rows), WRITE_BATCH):
            rec = session.run(SET_QUERY, rows=rows[i : i + WRITE_BATCH]).single()
            flagged += rec["c"] if rec else 0
        # Provenance + run summary on the DataSource node (no Gene clobbering).
        session.run(
            "MERGE (d:DataSource {name: $name}) "
            "SET d.loaded_at = datetime(), d.source_db = $source_db, "
            "    d.source_version = $source_version, "
            "    d.census_symbols = $census, d.genes_flagged = $flagged",
            name="12_cosmic", source_db=SOURCE_DB, source_version=SOURCE_VERSION,
            census=len(gene_to_tier), flagged=flagged,
        ).consume()

    skipped = len(gene_to_tier) - flagged
    elapsed = time.time() - start
    print(f"{flagged} genes flagged as cancer genes from COSMIC CGC.")
    print(f"Census symbols not present in graph (skipped): {skipped}")
    print(f"Time elapsed: {elapsed:.1f}s")
    close_driver()


if __name__ == "__main__":
    main()
