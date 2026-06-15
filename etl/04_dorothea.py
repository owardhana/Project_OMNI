"""ETL 04 — Load DoRothEA TF -> target REGULATES edges.

Reads DoRothEA regulons and MERGEs a (:Gene)-[:REGULATES]->(:Gene) edge for each
TF/target pair in the configured confidence tiers, when BOTH genes already exist
as nodes (matched by hgnc_symbol). Idempotent; re-runs update edge attributes but
preserve any citation work done by the CitationAgent.

Data source reality (see docs/adr/0003-data-source-urls.md):
  - DoRothEA no longer ships a CSV; we read data/raw/dorothea_hs.rda via pyreadr.
  - Real columns are tf / confidence / target / mor (NO 'likelihood' column).
  - High-confidence tiers A+B total ~6.4k edges — the build prompt's ">30k" is a
    miscalibrated figure; A+B is the principled, spec-mandated set (04_decisions).

Confidence tiers come from DOROTHEA_MIN_CONFIDENCE (default 'A,B').

    etl/.venv/bin/python etl/04_dorothea.py
"""

import os
import sys
import time
from pathlib import Path

import pandas as pd
import pyreadr
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.neo4j_client import close_driver, get_session  # noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

RAW_DIR = _PROJECT_ROOT / "data" / "raw"
DOROTHEA_RDA = RAW_DIR / "dorothea_hs.rda"
DOROTHEA_CSV = RAW_DIR / "dorothea_hs.csv"
BATCH_SIZE = 2000

# Numeric confidence per tier (A/B are the spec-defined values; C-E provided so
# expanding DOROTHEA_MIN_CONFIDENCE still produces sane numbers).
CONFIDENCE_VALUES = {"A": 1.0, "B": 0.85, "C": 0.7, "D": 0.5, "E": 0.25}

MERGE_QUERY = """
UNWIND $rows AS row
MATCH (s:Gene {hgnc_symbol: row.tf})
MATCH (t:Gene {hgnc_symbol: row.target})
MERGE (s)-[r:REGULATES]->(t)
ON CREATE SET r.mode = row.mode,
              r.confidence = row.confidence,
              r.confidence_tier = row.tier,
              r.source_db = 'DoRothEA',
              r.source_version = 'v1.0',
              r.pmids = [],
              r.citation_attempted = false
ON MATCH SET r.mode = row.mode,
             r.confidence = row.confidence,
             r.confidence_tier = row.tier,
             r.source_db = 'DoRothEA',
             r.source_version = 'v1.0'
RETURN count(r) AS touched
"""


def mode_from_mor(mor: float) -> str:
    if mor > 0:
        return "activator"
    if mor < 0:
        return "repressor"
    return "unknown"


def load_dorothea() -> pd.DataFrame:
    if DOROTHEA_CSV.exists():
        return pd.read_csv(DOROTHEA_CSV)
    if DOROTHEA_RDA.exists():
        result = pyreadr.read_r(str(DOROTHEA_RDA))
        # The .rda holds a single object named 'dorothea_hs'.
        return next(iter(result.values()))
    raise FileNotFoundError(
        f"Neither {DOROTHEA_CSV} nor {DOROTHEA_RDA} found. Run etl/00_download.sh."
    )


def main() -> None:
    start = time.time()
    tiers = [
        t.strip()
        for t in os.getenv("DOROTHEA_MIN_CONFIDENCE", "A,B").split(",")
        if t.strip()
    ]
    df = load_dorothea()
    df = df.dropna(subset=["tf", "target", "confidence", "mor"])
    df = df[df["confidence"].isin(tiers)].copy()
    df["mor"] = pd.to_numeric(df["mor"], errors="coerce").fillna(0.0)

    rows = [
        {
            "tf": str(r["tf"]).strip(),
            "target": str(r["target"]).strip(),
            "mode": mode_from_mor(float(r["mor"])),
            "confidence": CONFIDENCE_VALUES.get(str(r["confidence"]).strip(), 0.5),
            "tier": str(r["confidence"]).strip(),
        }
        for _, r in df.iterrows()
    ]
    total = len(rows)
    print(f"DoRothEA rows in tiers {tiers}: {total}")

    created = 0
    touched = 0
    with get_session() as session:
        # Distinct symbols not present as Gene nodes (reported, not loaded).
        symbols = sorted({r["tf"] for r in rows} | {r["target"] for r in rows})
        present: set[str] = set()
        for i in range(0, len(symbols), 5000):
            chunk = symbols[i : i + 5000]
            rec = session.run(
                "MATCH (g:Gene) WHERE g.hgnc_symbol IN $s RETURN collect(g.hgnc_symbol) AS f",
                s=chunk,
            ).single()
            present.update(rec["f"])
        symbols_not_found = len(symbols) - len(present)

        for i in range(0, total, BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            result = session.run(MERGE_QUERY, rows=batch)
            records = list(result)  # read the RETURN row before consuming
            summary = result.consume()
            touched += records[0]["touched"] if records else 0
            created += summary.counters.relationships_created

    merged = touched - created
    skipped = total - touched
    elapsed = time.time() - start
    print(f"REGULATES edges created: {created}")
    print(f"REGULATES edges merged (existing, updated): {merged}")
    print(f"Rows skipped (a TF/target gene missing): {skipped}")
    print(
        f"Distinct symbols not found as Gene nodes: {symbols_not_found}/{len(symbols)}"
    )
    print(f"Time elapsed: {elapsed:.1f}s")
    close_driver()


if __name__ == "__main__":
    main()
