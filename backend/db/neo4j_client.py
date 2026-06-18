"""Async Neo4j driver + session management for the backend service.

A single module-level driver holds the connection pool for the process. Use
``get_session()`` as an async context manager for each unit of work.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncSession

from backend.config import settings

# Index DDL run once on application startup. Neo4j 5 native syntax (no APOC).
# The fulltext index powers /api/search; the b-tree indexes keep node lookups
# fast once the graph grows to hundreds of thousands of nodes; the vector indexes
# (Neo4j 5.11+ native, ADR-0008) power semantic search over node embeddings.
INDEX_STATEMENTS: list[str] = [
    # Phase 2: the search index was renamed gene_search -> node_search and widened
    # to cover Protein and Disease (ADR-0007). Drop the old one for a clean rename.
    "DROP INDEX gene_search IF EXISTS",
    """
    CREATE FULLTEXT INDEX node_search IF NOT EXISTS
    FOR (n:Gene|Transcript|Protein|Disease)
    ON EACH [n.hgnc_symbol, n.description, n.summary_text, n.name]
    """,
    # B-tree indexes — existing Gene/Transcript plus Phase 2 node types.
    "CREATE INDEX gene_ensembl_idx IF NOT EXISTS FOR (n:Gene) ON (n.ensembl_id)",
    "CREATE INDEX gene_symbol_idx IF NOT EXISTS FOR (n:Gene) ON (n.hgnc_symbol)",
    "CREATE INDEX transcript_id_idx IF NOT EXISTS FOR (n:Transcript) ON (n.ensembl_tx_id)",
    "CREATE INDEX protein_uniprot_idx IF NOT EXISTS FOR (n:Protein) ON (n.uniprot_id)",
    "CREATE INDEX protein_symbol_idx IF NOT EXISTS FOR (n:Protein) ON (n.hgnc_symbol)",
    "CREATE INDEX variant_rsid_idx IF NOT EXISTS FOR (n:Variant) ON (n.rsid)",
    "CREATE INDEX disease_ontology_idx IF NOT EXISTS FOR (n:Disease) ON (n.ontology_id)",
    # Vector indexes (Neo4j 5.11+ native syntax). 1536-dim cosine, per ADR-0008.
    """
    CREATE VECTOR INDEX gene_embeddings IF NOT EXISTS
    FOR (n:Gene) ON (n.embedding)
    OPTIONS {indexConfig: {`vector.dimensions`: 1536, `vector.similarity_function`: 'cosine'}}
    """,
    """
    CREATE VECTOR INDEX protein_embeddings IF NOT EXISTS
    FOR (n:Protein) ON (n.embedding)
    OPTIONS {indexConfig: {`vector.dimensions`: 1536, `vector.similarity_function`: 'cosine'}}
    """,
    """
    CREATE VECTOR INDEX disease_embeddings IF NOT EXISTS
    FOR (n:Disease) ON (n.embedding)
    OPTIONS {indexConfig: {`vector.dimensions`: 1536, `vector.similarity_function`: 'cosine'}}
    """,
]

_driver: AsyncDriver | None = None


def get_driver() -> AsyncDriver:
    """Return the shared async driver, creating it on first use."""
    global _driver
    if _driver is None:
        _driver = AsyncGraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
            max_connection_pool_size=50,
        )
    return _driver


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield an async Neo4j session, closing it on exit."""
    driver = get_driver()
    session = driver.session()
    try:
        yield session
    finally:
        await session.close()


async def create_indexes() -> None:
    """Create all required indexes (idempotent — IF NOT EXISTS)."""
    async with get_session() as session:
        for statement in INDEX_STATEMENTS:
            await session.run(statement)


async def close_driver() -> None:
    """Close the shared driver (call on application shutdown)."""
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None
