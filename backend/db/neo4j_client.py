"""Async Neo4j driver + session management for the backend service.

A single module-level driver holds the connection pool for the process. Use
``get_session()`` as an async context manager for each unit of work.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncSession

from backend.config import settings

# Index DDL run once on application startup. Neo4j 5 native syntax (no APOC).
# The fulltext index powers /api/search; the b-tree indexes keep gene/transcript
# lookups fast once the graph grows to hundreds of thousands of nodes.
INDEX_STATEMENTS: list[str] = [
    """
    CREATE FULLTEXT INDEX gene_search IF NOT EXISTS
    FOR (n:Gene|Transcript) ON EACH [n.hgnc_symbol, n.description]
    """,
    "CREATE INDEX gene_ensembl_idx IF NOT EXISTS FOR (n:Gene) ON (n.ensembl_id)",
    "CREATE INDEX gene_symbol_idx IF NOT EXISTS FOR (n:Gene) ON (n.hgnc_symbol)",
    "CREATE INDEX transcript_id_idx IF NOT EXISTS FOR (n:Transcript) ON (n.ensembl_tx_id)",
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
