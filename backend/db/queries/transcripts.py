"""Cypher queries for transcript endpoints. Returns plain dicts."""

from backend.db.neo4j_client import get_session


async def get_transcript_by_id(ensembl_tx_id: str) -> dict | None:
    """Return the transcript's properties dict, or None if absent."""
    query = """
    MATCH (t:Transcript {ensembl_tx_id: $tx_id})
    RETURN properties(t) AS props
    LIMIT 1
    """
    async with get_session() as session:
        rows = await (await session.run(query, tx_id=ensembl_tx_id)).data()
    return rows[0]["props"] if rows else None


async def get_transcript_neighborhood(ensembl_tx_id: str) -> dict:
    """Transcript + its parent gene (the PRODUCES edge), as a raw subgraph dict."""
    query = """
    MATCH (g:Gene)-[prod:PRODUCES]->(t:Transcript {ensembl_tx_id: $tx_id})
    OPTIONAL MATCH (g)-[out:REGULATES]->(:Gene)
    WITH g, t, prod, count(out) > 0 AS is_tf
    RETURN properties(g) AS gene_props,
           is_tf,
           properties(t) AS tx_props,
           properties(prod) AS prod_props,
           g.ensembl_id AS gene_id
    """
    async with get_session() as session:
        rows = await (await session.run(query, tx_id=ensembl_tx_id)).data()

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_genes: set[str] = set()
    tx_added = False
    for r in rows:
        if r["gene_id"] not in seen_genes:
            nodes.append({"kind": "gene", "props": r["gene_props"], "is_tf": r["is_tf"]})
            seen_genes.add(r["gene_id"])
        if not tx_added:
            nodes.append({"kind": "transcript", "props": r["tx_props"]})
            tx_added = True
        edges.append(
            {
                "rel_type": "PRODUCES",
                "source": r["gene_id"],
                "target": ensembl_tx_id,
                "props": r["prod_props"],
            }
        )
    return {"nodes": nodes, "edges": edges}
