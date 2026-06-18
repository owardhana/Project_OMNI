"""CitationAgent safety tests: it enriches edges but never alters topology.

These run the real agent on a small batch (live Neo4j + NCBI + LLM).
"""

from backend.agents.citation_agent import citation_agent
from backend.agents.embedding_agent import embedding_agent
from backend.db.neo4j_client import get_session

_BATCH = 5
_EMBED_BATCH = 2  # keep the live OpenRouter embedding cost minimal
_EMBED_DIM = 1536


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


# --- EmbeddingAgent: enriches with embeddings, never alters topology --------

async def _embeddable_node_count() -> int:
    async with get_session() as session:
        rec = await (
            await session.run(
                "MATCH (n) WHERE n:Gene OR n:Protein OR n:Disease "
                "OR n:Transcript OR n:Variant RETURN count(n) AS c"
            )
        ).single()
    return rec["c"]


async def _topology_edge_count() -> int:
    async with get_session() as session:
        rec = await (
            await session.run(
                "MATCH ()-[r]->() "
                "WHERE r:REGULATES OR r:PRODUCES OR r:TRANSLATES_TO OR r:ENCODES "
                "OR r:INTERACTS_WITH OR r:ASSOCIATED_WITH OR r:IN_GENE "
                "OR r:IMPLICATED_IN RETURN count(r) AS c"
            )
        ).single()
    return rec["c"]


async def test_embedding_agent_no_new_nodes():
    before = await _embeddable_node_count()
    await embedding_agent.run(batch_size=_EMBED_BATCH)
    after = await _embeddable_node_count()
    assert after == before  # only an EmbeddingRun log node may be added, not bio nodes


async def test_embedding_agent_no_new_edges():
    before = await _topology_edge_count()
    await embedding_agent.run(batch_size=_EMBED_BATCH)
    after = await _topology_edge_count()
    assert after == before


async def test_embedding_agent_sets_embedding():
    summary = await embedding_agent.run(batch_size=_EMBED_BATCH)
    # The live graph has thousands of un-embedded nodes with summary_text, so a
    # real run must embed at least one (proves the agent actually wrote, not that
    # a pre-existing embedding happens to exist).
    assert summary["nodes_embedded"] >= 1
    assert summary["nodes_failed"] == 0
    # Every embedding the agent writes is a 1536-dim vector (ADR-0008).
    async with get_session() as session:
        rows = await (
            await session.run(
                "MATCH (n) WHERE n.embedding IS NOT NULL "
                "AND n.source_agent = $agent "
                "RETURN n.embedding AS e LIMIT 5",
                agent=embedding_agent.agent_name,
            )
        ).data()
    assert rows, "agent-written embeddings must be present"
    for row in rows:
        assert len(row["e"]) == _EMBED_DIM
