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


async def search_nodes(query: str, limit: int = 10) -> list[dict]:
    """Full-text search across Gene, Transcript, Protein, Disease (node_search).

    Returns discriminated dicts: each carries ``node_type`` plus a per-kind id and
    display fields, ranked by full-text score. Disease is a first-class search
    entry point alongside genes (ADR-0007).
    """
    lucene = _build_lucene_query(query)
    if lucene is None:
        return []
    # is_tf (genes only) re-routed through the protein (ADR-0004): a gene "is a TF"
    # iff it encodes one of our TF proteins, via its transcript or ENCODES.
    cypher = """
    CALL db.index.fulltext.queryNodes("node_search", $q) YIELD node, score
    WITH node, score, labels(node) AS lbls
    WITH node, score,
         CASE
           WHEN 'Gene' IN lbls THEN 'gene'
           WHEN 'Disease' IN lbls THEN 'disease'
           WHEN 'Protein' IN lbls THEN 'protein'
           WHEN 'Transcript' IN lbls THEN 'transcript'
           ELSE 'unknown'
         END AS node_type
    WITH node, score, node_type,
         CASE WHEN node_type = 'gene'
              THEN (EXISTS { (node)-[:ENCODES]->(:Protein) }
                    OR EXISTS { (node)-[:PRODUCES]->(:Transcript)-[:TRANSLATES_TO]->(:Protein) })
              ELSE false END AS is_tf
    RETURN node_type,
           coalesce(node.ensembl_id, node.uniprot_id, node.ontology_id, node.ensembl_tx_id) AS id,
           node.ensembl_id AS ensembl_id,
           node.hgnc_symbol AS hgnc_symbol,
           node.name AS name,
           node.description AS description,
           is_tf,
           // An exact gene/protein symbol match outranks a fulltext hit on a
           // disease description that merely mentions the symbol (e.g. "TP53").
           CASE WHEN toUpper(coalesce(node.hgnc_symbol, '')) = toUpper($exact)
                THEN 1 ELSE 0 END AS exact_boost,
           score
    ORDER BY exact_boost DESC, score DESC
    LIMIT $limit
    """
    async with get_session() as session:
        rows = await (
            await session.run(cypher, q=lucene, exact=query.strip(), limit=limit)
        ).data()
    return rows


# Backwards-compatible alias — the MVP route/tests referenced search_genes; the
# fulltext index now covers more labels. Filter on node_type == 'gene' for genes.
search_genes = search_nodes


_ENTITIES_FULLTEXT = """
CALL db.index.fulltext.queryNodes("node_search", $q) YIELD node, score
WITH node, score, labels(node) AS lbls
WITH node, score,
     CASE
       WHEN 'Gene' IN lbls THEN 'gene'
       WHEN 'Disease' IN lbls THEN 'disease'
       WHEN 'Protein' IN lbls THEN 'protein'
       WHEN 'Transcript' IN lbls THEN 'transcript'
       ELSE 'other'
     END AS node_type
WHERE (size($types) = 0 OR node_type IN $types)
  AND ($chrom IS NULL OR node.chromosome = $chrom)
  AND ($biotype IS NULL OR node.biotype = $biotype)
  AND ($pli IS NULL OR (node_type = 'gene' AND node.pli_score >= $pli))
RETURN node_type,
       coalesce(node.ensembl_id, node.uniprot_id, node.ontology_id, node.ensembl_tx_id) AS id,
       coalesce(node.hgnc_symbol, node.name, node.ensembl_id, node.uniprot_id, node.ontology_id) AS display_name,
       node.description AS description,
       CASE WHEN toUpper(coalesce(node.hgnc_symbol, '')) = toUpper($exact)
            THEN 1 ELSE 0 END AS exact_boost,
       score
ORDER BY exact_boost DESC, score DESC LIMIT 300
"""

_ENTITIES_VARIANT = """
MATCH (v:Variant)
WHERE toLower(v.rsid) STARTS WITH toLower($q)
  AND ($clinical IS NULL OR v.clinical_significance = $clinical)
  AND ($chrom IS NULL OR v.chromosome = $chrom)
RETURN 'variant' AS node_type, v.rsid AS id, v.rsid AS display_name,
       v.clinical_significance AS description, 1.0 AS score
LIMIT 100
"""


async def search_entities(
    q: str,
    types: list[str],
    chromosome: str | None = None,
    biotype: str | None = None,
    clinical: str | None = None,
    pli_min: float | None = None,
) -> list[dict]:
    """Filtered entity search for the browser. Variants are matched on rsid
    (they aren't in the fulltext index); all other labels via node_search."""
    text = (q or "").strip()
    want = lambda t: not types or t in types  # noqa: E731
    fulltext_types = {"gene", "protein", "disease", "transcript"}
    items: list[dict] = []
    async with get_session() as session:
        lucene = _build_lucene_query(text) if text else None
        if lucene and (not types or any(t in fulltext_types for t in types)):
            rows = await (
                await session.run(
                    _ENTITIES_FULLTEXT, q=lucene, exact=text, types=types,
                    chrom=chromosome, biotype=biotype, pli=pli_min,
                )
            ).data()
            items.extend({**r, "is_tf": False} for r in rows)
        if want("variant") and text:
            rows = await (
                await session.run(
                    _ENTITIES_VARIANT, q=text, clinical=clinical, chrom=chromosome
                )
            ).data()
            items.extend({**r, "is_tf": False} for r in rows)
    items.sort(key=lambda x: -(x.get("score") or 0))
    return items


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
