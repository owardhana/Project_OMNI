"""ETL 09 — Enrich existing Variant nodes with ClinVar clinical significance.

Bulk-file enrichment (docs/data-architecture.md Pattern 1). Reads the ClinVar variant
summary, keeps GRCh38 rows, and SETs ``clinical_significance`` on Variant nodes
that already exist in the graph (matched by rsid). It never mints Variant nodes —
ClinVar entries with no matching graph variant are skipped and counted.

Format discipline (ADR-0003): required columns are checked against the header
before the full read; a missing column prints the columns found and aborts.

    etl/.venv/bin/python etl/09_clinvar.py
"""

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.neo4j_client import close_driver, get_session  # noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLINVAR_FILE = _PROJECT_ROOT / "data" / "raw" / "ClinVarVariantSummary.txt.gz"
REQUIRED_COLUMNS = ["RS# (dbSNP)", "ClinicalSignificance", "Assembly"]
CHUNK_SIZE = 200_000
WRITE_BATCH = 5000

SET_QUERY = """
UNWIND $rows AS r
MATCH (v:Variant {rsid: r.rsid})
SET v.clinical_significance = r.sig
RETURN count(v) AS c
"""


def main() -> None:
    start = time.time()
    if not CLINVAR_FILE.exists():
        raise FileNotFoundError(
            f"{CLINVAR_FILE} not found. Run etl/00_download.sh first."
        )

    header = pd.read_csv(CLINVAR_FILE, sep="\t", nrows=0)
    missing = [c for c in REQUIRED_COLUMNS if c not in header.columns]
    if missing:
        print(f"ABORT: ClinVar summary missing required columns: {missing}")
        print(f"Columns present: {list(header.columns)}")
        sys.exit(1)

    with get_session() as session:
        existing = {
            r["rsid"]
            for r in session.run(
                "MATCH (v:Variant) WHERE v.rsid STARTS WITH 'rs' RETURN v.rsid AS rsid"
            ).data()
        }
        print(f"Existing rs-variants in graph: {len(existing)}")

        rsid_to_sig: dict[str, str] = {}
        grch38_rows = 0
        reader = pd.read_csv(
            CLINVAR_FILE, sep="\t", dtype=str, usecols=REQUIRED_COLUMNS,
            chunksize=CHUNK_SIZE, low_memory=False,
        )
        for chunk in reader:
            g38 = chunk[chunk["Assembly"] == "GRCh38"]
            grch38_rows += len(g38)
            for rs, sig in zip(g38["RS# (dbSNP)"], g38["ClinicalSignificance"]):
                if not isinstance(rs, str) or not rs.isdigit() or rs == "0":
                    continue  # ClinVar uses -1 / blank for "no rsid"
                rsid = "rs" + rs
                if (
                    rsid in existing
                    and rsid not in rsid_to_sig
                    and isinstance(sig, str)
                    and sig.strip()
                ):
                    rsid_to_sig[rsid] = sig.strip()
        print(
            f"ClinVar GRCh38 rows: {grch38_rows}; "
            f"matched to graph variants: {len(rsid_to_sig)}"
        )

        rows = [{"rsid": k, "sig": v} for k, v in rsid_to_sig.items()]
        enriched = 0
        for i in range(0, len(rows), WRITE_BATCH):
            rec = session.run(SET_QUERY, rows=rows[i : i + WRITE_BATCH]).single()
            enriched += rec["c"] if rec else 0

    elapsed = time.time() - start
    print(f"Variants enriched with clinical_significance: {enriched}")
    print(f"Graph variants not found in ClinVar GRCh38: {len(existing) - len(rsid_to_sig)}")
    print(f"Time elapsed: {elapsed:.1f}s")
    close_driver()


if __name__ == "__main__":
    main()
