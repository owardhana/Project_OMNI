"""ETL 02 — Load GENCODE transcripts + PRODUCES edges.

Streams gencode.v46.annotation.gtf.gz (never loads the whole file into memory)
and, for every ``transcript`` feature whose parent Gene already exists (loaded
by 01_hgnc.py), MERGEs a (:Transcript) node and a (:Gene)-[:PRODUCES]->(:Transcript)
edge. Transcripts whose gene is not in the graph are skipped (per MVP spec).

Tissue weights are NOT written here: Neo4j rejects map-valued properties, so the
PRODUCES edge starts without them and 03_gtex.py SETs flat ``tw_<tissue>`` floats
(see docs/adr/0001-tissue-weights-flat-properties.md).

    etl/.venv/bin/python etl/02_gencode.py
"""

import gzip
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.id_mapper import strip_version  # noqa: E402
from utils.neo4j_client import close_driver, get_session  # noqa: E402

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
GTF_FILE = RAW_DIR / "gencode.v46.annotation.gtf.gz"
BATCH_SIZE = 5000

_ATTR_RE = re.compile(r'(\w+) "([^"]*)"')

MERGE_QUERY = """
UNWIND $rows AS row
MATCH (g:Gene {ensembl_id: row.gene_id})
MERGE (t:Transcript {ensembl_tx_id: row.tx_id})
SET t.hgnc_symbol = row.tx_name,
    t.biotype = row.biotype,
    t.length_bp = row.length_bp
MERGE (g)-[r:PRODUCES]->(t)
ON CREATE SET r.source_db = 'GENCODE',
              r.gencode_version = 'v46',
              r.pmids = [],
              r.citation_attempted = false
"""


def parse_attributes(attr_str: str) -> dict[str, str]:
    return {k: v for k, v in _ATTR_RE.findall(attr_str)}


def iter_transcript_rows():
    if not GTF_FILE.exists():
        raise FileNotFoundError(
            f"{GTF_FILE} not found. Run etl/00_download.sh first."
        )
    with gzip.open(GTF_FILE, "rt") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 9 or parts[2] != "transcript":
                continue
            try:
                start, end = int(parts[3]), int(parts[4])
            except ValueError:
                continue
            attrs = parse_attributes(parts[8])
            gene_id = strip_version(attrs.get("gene_id", ""))
            tx_id = strip_version(attrs.get("transcript_id", ""))
            if not gene_id or not tx_id:
                continue
            yield {
                "gene_id": gene_id,
                "tx_id": tx_id,
                "tx_name": attrs.get("transcript_name"),
                "biotype": attrs.get("transcript_type"),
                "length_bp": end - start + 1,
            }


def main() -> None:
    start = time.time()
    transcripts_created = 0
    produces_created = 0
    processed = 0

    with get_session() as session:
        batch: list[dict] = []
        for row in iter_transcript_rows():
            batch.append(row)
            if len(batch) >= BATCH_SIZE:
                summary = session.run(MERGE_QUERY, rows=batch).consume()
                transcripts_created += summary.counters.nodes_created
                produces_created += summary.counters.relationships_created
                processed += len(batch)
                batch = []
        if batch:
            summary = session.run(MERGE_QUERY, rows=batch).consume()
            transcripts_created += summary.counters.nodes_created
            produces_created += summary.counters.relationships_created
            processed += len(batch)

    elapsed = time.time() - start
    print(f"Transcript features processed: {processed}")
    print(f"Transcript nodes created: {transcripts_created}")
    print(f"PRODUCES edges created: {produces_created}")
    print(f"Time elapsed: {elapsed:.1f}s")
    close_driver()


if __name__ == "__main__":
    main()
