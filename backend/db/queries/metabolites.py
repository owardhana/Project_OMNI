"""Cypher queries for metabolite endpoints (Phase 3, ADR-0009).

Returns plain dicts (the DB layer must not import the API layer). Metabolite is a
first-class traversable node: its subgraph reuses the same signal-decay traversal
as Gene/Disease seeds, keyed on hmdb_id (primary) or chebi_id (fallback).
"""

from backend.db.neo4j_client import get_session
from backend.db.queries.traversal import signal_decay_subgraph


async def get_metabolite_by_id(metabolite_id: str) -> dict | None:
    """Return {'props': {...}} for a metabolite by hmdb_id or chebi_id, else None."""
    query = """
    MATCH (m:Metabolite)
    WHERE m.hmdb_id = $id OR m.chebi_id = $id
    RETURN properties(m) AS props
    LIMIT 1
    """
    async with get_session() as session:
        rows = await (await session.run(query, id=metabolite_id)).data()
    return rows[0] if rows else None


async def get_metabolite_neighborhood(
    metabolite_id: str,
    decay: float | None = None,
    min_signal: float | None = None,
    max_nodes: int | None = None,
) -> dict:
    """Signal-decay subgraph seeded at one metabolite (ADR-0009): signal flows
    Metabolite -> Protein (CATALYSES) -> the protein's chain, same algorithm as a
    gene seed."""
    return await signal_decay_subgraph(
        [metabolite_id], decay=decay, min_signal=min_signal, max_nodes=max_nodes
    )
