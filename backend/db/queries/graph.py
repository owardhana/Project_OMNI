"""Cypher queries for search and edge detail. Returns plain dicts."""

import re

from backend.db.neo4j_client import get_session

# Lucene query-syntax metacharacters to strip from raw user input before it is
# handed to the full-text index, so a stray character can't throw a parse error.
_LUCENE_SPECIAL = re.compile(r'(\+|-|&&|\|\||!|\(|\)|\{|\}|\[|\]|\^|"|~|\*|\?|:|\\|/)')


def _build_lucene_query(raw: str) -> str | None:
    """Sanitize user input into a full-text query supporting exact + prefix match.

    Each token becomes ``(token^4 OR token*)`` so an exact symbol match (e.g.
    'TP53') outranks prefix-only matches (e.g. 'TP53TG3'), while partial input
    ('TP5') still matches via the wildcard for autocomplete.
    """
    cleaned = _LUCENE_SPECIAL.sub(" ", raw).strip()
    if not cleaned:
        return None
    return " ".join(f"({token}^4 OR {token}*)" for token in cleaned.split())


async def search_genes(query: str, limit: int = 10) -> list[dict]:
    """Full-text + prefix search over gene symbol/description.

    Returns dicts with ensembl_id, hgnc_symbol, description, is_tf, score.
    """
    lucene = _build_lucene_query(query)
    if lucene is None:
        return []
    cypher = """
    CALL db.index.fulltext.queryNodes("gene_search", $q) YIELD node, score
    WHERE node:Gene
    OPTIONAL MATCH (node)-[out:REGULATES]->(:Gene)
    WITH node, score, count(out) > 0 AS is_tf
    RETURN node.ensembl_id AS ensembl_id,
           node.hgnc_symbol AS hgnc_symbol,
           node.description AS description,
           is_tf,
           score
    ORDER BY score DESC
    LIMIT $limit
    """
    async with get_session() as session:
        rows = await (await session.run(cypher, q=lucene, limit=limit)).data()
    return rows


async def get_edge_detail(
    source_id: str, target_id: str, rel_type: str
) -> dict | None:
    """Return a single edge's raw detail dict, or None if not found.

    Node ids may be Ensembl gene IDs or transcript IDs; matched on either key.
    rel_type is validated against the known relationship types.
    """
    if rel_type not in ("REGULATES", "PRODUCES"):
        raise ValueError(f"Unknown rel_type '{rel_type}'")
    cypher = f"""
    MATCH (a)-[r:{rel_type}]->(b)
    WHERE (a.ensembl_id = $source OR a.ensembl_tx_id = $source)
      AND (b.ensembl_id = $target OR b.ensembl_tx_id = $target)
    RETURN type(r) AS rel_type,
           coalesce(a.ensembl_id, a.ensembl_tx_id) AS source,
           coalesce(b.ensembl_id, b.ensembl_tx_id) AS target,
           properties(r) AS props
    LIMIT 1
    """
    async with get_session() as session:
        rows = await (
            await session.run(cypher, source=source_id, target=target_id)
        ).data()
    return rows[0] if rows else None
