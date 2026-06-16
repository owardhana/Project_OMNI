"""CitationAgent safety tests: it enriches edges but never alters topology.

These run the real agent on a small batch (live Neo4j + NCBI + LLM).
"""

from backend.agents.citation_agent import citation_agent
from backend.db.neo4j_client import get_session

_BATCH = 5


async def _bio_node_count() -> int:
    async with get_session() as session:
        rec = await (
            await session.run(
                "MATCH (n) WHERE n:Gene OR n:Transcript OR n:Protein RETURN count(n) AS c"
            )
        ).single()
    return rec["c"]


async def _edge_count() -> int:
    async with get_session() as session:
        rec = await (
            await session.run(
                "MATCH ()-[r]->() "
                "WHERE r:REGULATES OR r:PRODUCES OR r:TRANSLATES_TO OR r:ENCODES "
                "RETURN count(r) AS c"
            )
        ).single()
    return rec["c"]


async def test_citation_agent_no_new_nodes():
    before = await _bio_node_count()
    await citation_agent.run(batch_size=_BATCH)
    after = await _bio_node_count()
    assert after == before  # no new Gene/Transcript nodes


async def test_citation_agent_no_new_edges():
    before = await _edge_count()
    await citation_agent.run(batch_size=_BATCH)
    after = await _edge_count()
    assert after == before  # no new REGULATES/PRODUCES edges


async def test_citation_agent_sets_attempted():
    await citation_agent.run(batch_size=_BATCH)
    # After a run there must exist REGULATES edges marked citation_attempted.
    async with get_session() as session:
        rec = await (
            await session.run(
                "MATCH ()-[r:REGULATES]->() WHERE r.citation_attempted = true "
                "RETURN count(r) AS c"
            )
        ).single()
    assert rec["c"] > 0


async def test_citation_agent_pmids_are_strings():
    await citation_agent.run(batch_size=_BATCH)
    async with get_session() as session:
        rows = await (
            await session.run(
                "MATCH ()-[r:REGULATES]->() WHERE size(coalesce(r.pmids, [])) > 0 "
                "RETURN r.pmids AS pmids LIMIT 50"
            )
        ).data()
    for row in rows:
        for pmid in row["pmids"]:
            assert isinstance(pmid, str)
