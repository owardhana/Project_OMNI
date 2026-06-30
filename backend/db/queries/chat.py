"""Chat session persistence (Feature 1 conversational memory).

Stored in Neo4j — no second datastore — as (:ChatSession {id})-[:HAS_TURN]->(:ChatTurn
{role, content, ts, seq}). We persist only user+assistant *text* turns (tool calls are
ephemeral and re-run on demand), which is exactly what the agent needs to replay context
on a follow-up. These are operational nodes, never biological topology.
"""

from backend.db.neo4j_client import get_session

_SAVE_TURN = """
MERGE (s:ChatSession {id: $session_id})
  ON CREATE SET s.created_at = timestamp()
SET s.updated_at = timestamp()
WITH s
OPTIONAL MATCH (s)-[:HAS_TURN]->(t:ChatTurn)
WITH s, coalesce(max(t.seq), -1) + 1 AS next_seq
CREATE (s)-[:HAS_TURN]->(:ChatTurn {
    role: $role, content: $content, seq: next_seq, ts: timestamp()
})
"""

_LOAD_HISTORY = """
MATCH (:ChatSession {id: $session_id})-[:HAS_TURN]->(t:ChatTurn)
RETURN t.role AS role, t.content AS content
ORDER BY t.seq ASC
LIMIT $limit
"""


async def save_turn(session_id: str, role: str, content: str) -> None:
    if not content:
        return
    async with get_session() as session:
        await (
            await session.run(
                _SAVE_TURN, session_id=session_id, role=role, content=content
            )
        ).consume()


async def load_history(session_id: str, limit: int = 40) -> list[dict]:
    """Prior user/assistant turns for a session, oldest first."""
    async with get_session() as session:
        return await (
            await session.run(_LOAD_HISTORY, session_id=session_id, limit=limit)
        ).data()
