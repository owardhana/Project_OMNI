"""ETL 07 — Load STRING protein-protein INTERACTS_WITH edges.

Topology from a bulk file (06_data_vision.md Pattern 1). Reads the STRING v12
human links file, keeps interactions at/above the confidence threshold whose BOTH
endpoints already exist as Protein nodes, and MERGEs an INTERACTS_WITH edge
between them. STRING uses Ensembl protein ids (``9606.ENSP...``); we map them to
UniProt via ``IdMapper.ensp_to_uniprot`` and skip (never guess) any id that does
not map.

Threshold from the STRING_MIN_CONFIDENCE env var (default 0.9 -> >=900 on STRING's
0-1000 integer scale). ETL reads the env var directly rather than importing the
backend settings (03_structure.md module rules).

Note: with only the TF protein slice in the graph, an interaction is kept only if
BOTH partners are TF proteins, so the edge count is far below the full-proteome
~50k figure — that figure assumes the ~20k proteome is loaded.

    etl/.venv/bin/python etl/07_string.py
"""

import os
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.id_mapper import IdMapper  # noqa: E402
from utils.neo4j_client import close_driver, get_session  # noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
STRING_FILE = (
    _PROJECT_ROOT / "data" / "raw" / "9606.protein.links.detailed.v12.0.txt.gz"
)
CHUNK_SIZE = 500_000
WRITE_BATCH_SIZE = 2000

MERGE_QUERY = """
UNWIND $rows AS row
MATCH (a:Protein {uniprot_id: row.uniprot_a})
MATCH (b:Protein {uniprot_id: row.uniprot_b})
MERGE (a)-[r:INTERACTS_WITH {source_db: 'STRING'}]->(b)
SET r.combined_score = row.combined_score,
    r.experimental_score = row.experimental_score,
    r.coexpression_score = row.coexpression_score,
    r.source_version = 'v12.0'
RETURN count(r) AS touched
"""


def main() -> None:
    start = time.time()
    if not STRING_FILE.exists():
        raise FileNotFoundError(
            f"{STRING_FILE} not found. Run etl/00_download.sh first."
        )

    # STRING stores scores as 0-1000 integers; STRING_MIN_CONFIDENCE is 0-1.
    threshold = int(float(os.getenv("STRING_MIN_CONFIDENCE", "0.9")) * 1000)
    print(f"STRING combined_score threshold: >= {threshold}")

    mapper = IdMapper()

    with get_session() as session:
        my_uniprots = {
            r["u"]
            for r in session.run(
                "MATCH (p:Protein) WHERE p.uniprot_id IS NOT NULL "
                "RETURN p.uniprot_id AS u"
            ).data()
        }
        print(f"Existing Protein nodes (UniProt-keyed): {len(my_uniprots)}")

        above_threshold = 0
        skipped_no_map = 0
        skipped_not_in_graph = 0
        edges_touched = 0
        edges_created = 0
        rows: list[dict] = []

        def flush(batch: list[dict]) -> None:
            nonlocal edges_touched, edges_created
            if not batch:
                return
            result = session.run(MERGE_QUERY, rows=batch)
            records = list(result)  # read RETURN before consuming
            summary = result.consume()
            edges_touched += records[0]["touched"] if records else 0
            edges_created += summary.counters.relationships_created

        reader = pd.read_csv(
            STRING_FILE, sep=" ", compression="gzip", chunksize=CHUNK_SIZE,
            usecols=[
                "protein1", "protein2",
                "experimental", "coexpression", "combined_score",
            ],
        )
        for chunk in reader:
            chunk = chunk[chunk["combined_score"] >= threshold]
            above_threshold += len(chunk)
            for p1, p2, exp, coexp, score in zip(
                chunk["protein1"], chunk["protein2"],
                chunk["experimental"], chunk["coexpression"], chunk["combined_score"],
            ):
                ua = mapper.ensp_to_uniprot(p1)
                ub = mapper.ensp_to_uniprot(p2)
                if ua is None or ub is None:
                    skipped_no_map += 1
                    continue
                if ua == ub or ua not in my_uniprots or ub not in my_uniprots:
                    skipped_not_in_graph += 1
                    continue
                rows.append(
                    {
                        "uniprot_a": ua,
                        "uniprot_b": ub,
                        "combined_score": score / 1000.0,
                        "experimental_score": exp / 1000.0,
                        "coexpression_score": coexp / 1000.0,
                    }
                )
                if len(rows) >= WRITE_BATCH_SIZE:
                    flush(rows)
                    rows = []
        flush(rows)

    edges_merged = edges_touched - edges_created
    no_map_pct = (100.0 * skipped_no_map / above_threshold) if above_threshold else 0.0
    elapsed = time.time() - start
    print(f"STRING rows >= threshold: {above_threshold}")
    print(f"INTERACTS_WITH edges created: {edges_created}")
    print(f"INTERACTS_WITH edges merged (existing, updated): {edges_merged}")
    print(
        f"Pairs skipped (ENSP->UniProt unmapped): {skipped_no_map} "
        f"({no_map_pct:.1f}% — download UniProt idmapping if >5%)"
    )
    print(f"Pairs skipped (protein not in graph / self-loop): {skipped_not_in_graph}")
    print(f"Time elapsed: {elapsed:.1f}s")
    close_driver()


if __name__ == "__main__":
    main()
