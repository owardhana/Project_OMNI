"""Synchronous Neo4j driver for ETL scripts.

Kept deliberately separate from the async backend client (``backend/db``): ETL
scripts are one-shot batch jobs run directly with Python and must not import
backend modules (see README.md module dependency rules).
"""

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv
from neo4j import Driver, GraphDatabase, Session

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "changeme")

_driver: Driver | None = None


def get_driver() -> Driver:
    """Return a shared synchronous driver, creating it on first use."""
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
        )
    return _driver


@contextmanager
def get_session() -> Iterator[Session]:
    """Yield a synchronous Neo4j session, closing it on exit."""
    session = get_driver().session()
    try:
        yield session
    finally:
        session.close()


def close_driver() -> None:
    """Close the shared driver."""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


# B-tree indexes on the MERGE keys. The backend creates these on startup
# (backend/db/neo4j_client.py), but a standalone ETL rebuild (no backend running)
# starts with an index-free graph, where every MERGE does a full label scan —
# O(batch x nodes), which degrades to a hang once a label has 100k+ nodes (e.g.
# 02_gencode's Transcript MERGE). Creating them FIRST keeps the rebuild fast and
# self-sufficient. Idempotent (IF NOT EXISTS). ETL stays backend-independent, so the
# DDL is duplicated here intentionally (the fulltext/vector search indexes are a
# backend/search concern and are left to the backend).
_INDEX_STATEMENTS = [
    "CREATE INDEX gene_ensembl_idx IF NOT EXISTS FOR (n:Gene) ON (n.ensembl_id)",
    "CREATE INDEX gene_symbol_idx IF NOT EXISTS FOR (n:Gene) ON (n.hgnc_symbol)",
    "CREATE INDEX transcript_id_idx IF NOT EXISTS FOR (n:Transcript) ON (n.ensembl_tx_id)",
    "CREATE INDEX protein_uniprot_idx IF NOT EXISTS FOR (n:Protein) ON (n.uniprot_id)",
    "CREATE INDEX protein_symbol_idx IF NOT EXISTS FOR (n:Protein) ON (n.hgnc_symbol)",
    "CREATE INDEX variant_rsid_idx IF NOT EXISTS FOR (n:Variant) ON (n.rsid)",
    "CREATE INDEX disease_ontology_idx IF NOT EXISTS FOR (n:Disease) ON (n.ontology_id)",
    "CREATE INDEX metabolite_hmdb_idx IF NOT EXISTS FOR (n:Metabolite) ON (n.hmdb_id)",
    "CREATE INDEX metabolite_chebi_idx IF NOT EXISTS FOR (n:Metabolite) ON (n.chebi_id)",
]


def ensure_indexes() -> None:
    """Create the MERGE-key B-tree indexes if absent (idempotent). Call before any
    bulk load so MERGE stays index-backed, not a full scan."""
    with get_session() as session:
        for statement in _INDEX_STATEMENTS:
            session.run(statement).consume()
