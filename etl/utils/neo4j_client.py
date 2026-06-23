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
