"""Cypher queries for disease endpoints. Returns plain dicts.

Disease is a first-class traversable node (ADR-0007) and a valid signal-decay
seed alongside Gene; the traversal itself lives in traversal.py.
"""

from backend.db.neo4j_client import get_session


async def get_disease_by_ontology_id(ontology_id: str) -> dict | None:
    """Return the disease's properties dict, or None if absent."""
    query = """
    MATCH (d:Disease {ontology_id: $oid})
    RETURN properties(d) AS props
    LIMIT 1
    """
    async with get_session() as session:
        rows = await (await session.run(query, oid=ontology_id)).data()
    return rows[0]["props"] if rows else None
