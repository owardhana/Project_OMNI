"""Cypher queries for gene endpoints.

Returns plain dicts (the DB layer must not import the API layer). The API layer
converts these into typed models and reconstructs tissue_weights.

Conventions enforced here:
  - REGULATES edges are filtered to confidence_tier IN settings.dorothea_min_confidence.
  - Tissue filtering uses flat ``tw_<tissue>`` float properties (ADR-0001); the
    tissue is resolved to a validated key before being embedded in Cypher, so the
    interpolated property name cannot be attacker-controlled.
  - is_tf is computed as count(outgoing REGULATES) > 0 — NOT count(*), which is
    always true under an OPTIONAL MATCH.
"""

from backend.config import settings
from backend.db.neo4j_client import get_session

# Friendly UI aliases -> canonical tissue keys.
_TISSUE_ALIASES = {
    "blood": "whole_blood",
    "brain": "brain_prefrontal_cortex",
}

# Top-N gene neighbours pulled in for a 2-hop subgraph (keeps it bounded for
# hub genes like TP53 which regulate hundreds of targets).
_SUBGRAPH_NEIGHBOR_LIMIT = 50


def resolve_tissue_key(tissue: str | None) -> str | None:
    """Map a tissue param to a validated tissue key, or None for 'all'.

    Raises ValueError for an unknown tissue (prevents Cypher property injection).
    """
    if not tissue or tissue.strip().lower() == "all":
        return None
    key = tissue.strip().lower()
    key = _TISSUE_ALIASES.get(key, key)
    if key not in settings.tissues:
        raise ValueError(
            f"Unknown tissue '{tissue}'. Valid: all, {', '.join(settings.tissues)}"
        )
    return key


async def get_gene_by_symbol(symbol: str) -> dict | None:
    """Return {'props': {...}, 'is_tf': bool} for a gene, or None if absent."""
    query = """
    MATCH (g:Gene {hgnc_symbol: $symbol})
    OPTIONAL MATCH (g)-[out:REGULATES]->(:Gene)
    WITH g, count(out) > 0 AS is_tf
    RETURN properties(g) AS props, is_tf
    LIMIT 1
    """
    async with get_session() as session:
        result = await session.run(query, symbol=symbol)
        rows = await result.data()
    return rows[0] if rows else None


async def _fetch_subgraph(
    seed_ensembl_ids: list[str], tissue: str | None
) -> dict:
    """Fetch all REGULATES + PRODUCES edges touching the seed genes, plus the
    nodes they connect, as raw dicts. ``tissue`` is an already-resolved key."""
    tiers = settings.dorothea_min_confidence

    reg_query = """
    UNWIND $seeds AS sid
    MATCH (s:Gene {ensembl_id: sid})-[reg:REGULATES]-(o:Gene)
    WHERE reg.confidence_tier IN $tiers
    RETURN DISTINCT startNode(reg).ensembl_id AS source,
                    endNode(reg).ensembl_id AS target,
                    properties(reg) AS props
    """

    tissue_filter = "" if tissue is None else f"AND prod.tw_{tissue} > $threshold"
    prod_query = f"""
    UNWIND $seeds AS sid
    MATCH (s:Gene {{ensembl_id: sid}})-[prod:PRODUCES]->(tx:Transcript)
    WHERE true {tissue_filter}
    RETURN DISTINCT s.ensembl_id AS source,
                    tx.ensembl_tx_id AS target,
                    properties(prod) AS props
    """

    async with get_session() as session:
        reg_rows = await (
            await session.run(reg_query, seeds=seed_ensembl_ids, tiers=tiers)
        ).data()
        prod_rows = await (
            await session.run(
                prod_query,
                seeds=seed_ensembl_ids,
                threshold=settings.TISSUE_WEIGHT_THRESHOLD,
            )
        ).data()

        edges: list[dict] = []
        gene_ids: set[str] = set(seed_ensembl_ids)
        tx_ids: set[str] = set()
        for r in reg_rows:
            edges.append({"rel_type": "REGULATES", **r})
            gene_ids.add(r["source"])
            gene_ids.add(r["target"])
        for r in prod_rows:
            edges.append({"rel_type": "PRODUCES", **r})
            gene_ids.add(r["source"])
            tx_ids.add(r["target"])

        gene_rows = await (
            await session.run(
                """
                MATCH (g:Gene) WHERE g.ensembl_id IN $ids
                OPTIONAL MATCH (g)-[out:REGULATES]->(:Gene)
                WITH g, count(out) > 0 AS is_tf
                RETURN properties(g) AS props, is_tf
                """,
                ids=list(gene_ids),
            )
        ).data()
        tx_rows = (
            await (
                await session.run(
                    "MATCH (t:Transcript) WHERE t.ensembl_tx_id IN $ids "
                    "RETURN properties(t) AS props",
                    ids=list(tx_ids),
                )
            ).data()
            if tx_ids
            else []
        )

    nodes = [{"kind": "gene", "props": g["props"], "is_tf": g["is_tf"]} for g in gene_rows]
    nodes += [{"kind": "transcript", "props": t["props"]} for t in tx_rows]
    return {"nodes": nodes, "edges": edges}


async def get_gene_neighborhood(
    ensembl_id: str, tissue: str = "all", max_hops: int = 1
) -> dict:
    """1-hop neighborhood (REGULATES both directions + PRODUCES) around a gene."""
    tissue_key = resolve_tissue_key(tissue)
    return await _fetch_subgraph([ensembl_id], tissue_key)


async def get_gene_subgraph(
    ensembl_id: str, tissue: str = "all", max_hops: int = 2
) -> dict:
    """2-hop subgraph: center + its top REGULATES neighbours, expanded once more.

    Neighbour expansion is capped (_SUBGRAPH_NEIGHBOR_LIMIT) so hub genes do not
    return tens of thousands of nodes.
    """
    tissue_key = resolve_tissue_key(tissue)
    tiers = settings.dorothea_min_confidence
    async with get_session() as session:
        rows = await (
            await session.run(
                """
                MATCH (center:Gene {ensembl_id: $id})-[reg:REGULATES]-(g:Gene)
                WHERE reg.confidence_tier IN $tiers
                RETURN DISTINCT g.ensembl_id AS id, reg.confidence AS conf
                ORDER BY conf DESC
                LIMIT $limit
                """,
                id=ensembl_id,
                tiers=tiers,
                limit=_SUBGRAPH_NEIGHBOR_LIMIT,
            )
        ).data()
    seeds = [ensembl_id] + [r["id"] for r in rows]
    return await _fetch_subgraph(seeds, tissue_key)
