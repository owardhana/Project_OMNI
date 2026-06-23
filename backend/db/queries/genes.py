"""Cypher queries for gene endpoints.

Returns plain dicts (the DB layer must not import the API layer). The API layer
converts these into typed models.

Post-ADR-0004/0005/0006:
  - A transcription factor is a (:Protein); REGULATES is (:Protein)->(:Gene).
  - ``is_tf`` on a gene means "encodes a TF protein" — reachable via the
    transcript (PRODUCES -> TRANSLATES_TO -> Protein) or directly (ENCODES).
  - Neighborhood/subgraph use signal-decay traversal (see traversal.py); tissue
    no longer gates the graph (ADR-0006), so it is validated but not used to
    filter. tw_* are still returned on PRODUCES edges for frontend opacity.
"""

from backend.config import settings
from backend.db.neo4j_client import get_session
from backend.db.queries.traversal import signal_decay_subgraph

# Friendly UI aliases -> canonical tissue keys.
_TISSUE_ALIASES = {
    "blood": "whole_blood",
    "brain": "brain_prefrontal_cortex",
}

# A gene "is a TF" iff it encodes a TF *protein*, via the transcript
# (PRODUCES -> TRANSLATES_TO) or the ENCODES fallback. The subtype filter is
# REQUIRED post-ADR-0010: the full proteome means ~20k genes now reach *some*
# Protein, so an unfiltered "has a protein" clause flags every protein-coding
# gene as a TF (ADR-0004's "is_tf breaks SILENTLY"). Only subtype matters.
_GENE_IS_TF_CLAUSE = (
    "(EXISTS { (g)-[:ENCODES]->(:Protein {subtype: 'transcription_factor'}) } "
    "OR EXISTS { (g)-[:PRODUCES]->(:Transcript)"
    "-[:TRANSLATES_TO]->(:Protein {subtype: 'transcription_factor'}) })"
)


def resolve_tissue_key(tissue: str | None) -> str | None:
    """Map a tissue param to a validated tissue key, or None for 'all'.

    Tissue no longer filters the graph (ADR-0006); this remains only to reject
    obviously bad input early.
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
    query = f"""
    MATCH (g:Gene {{hgnc_symbol: $symbol}})
    RETURN properties(g) AS props, {_GENE_IS_TF_CLAUSE} AS is_tf
    LIMIT 1
    """
    async with get_session() as session:
        result = await session.run(query, symbol=symbol)
        rows = await result.data()
    return rows[0] if rows else None


async def get_gene_neighborhood(
    ensembl_id: str,
    tissue: str = "all",
    decay: float | None = None,
    min_signal: float | None = None,
    max_nodes: int | None = None,
) -> dict:
    """Signal-decay subgraph seeded at one gene (ADR-0005)."""
    resolve_tissue_key(tissue)  # validate only; tissue does not gate traversal
    return await signal_decay_subgraph(
        [ensembl_id], decay=decay, min_signal=min_signal, max_nodes=max_nodes
    )


# For each tumor type the seed gene is differentially expressed in, report the
# broader DE landscape of that (tumor_type, disease): how many genes are up vs
# down and the top-5 by |log2fc| in each direction. Phase 3 (08_phase3...).
_GENE_CANCER = """
MATCH (g:Gene {hgnc_symbol: $symbol})-[seed:DIFFERENTIALLY_EXPRESSED]->(d:Disease)
WITH DISTINCT d, seed.tumor_type AS tumor_type
CALL {
  WITH d, tumor_type
  MATCH (:Gene)-[r:DIFFERENTIALLY_EXPRESSED {tumor_type: tumor_type}]->(d)
  RETURN sum(CASE WHEN r.direction = 'up' THEN 1 ELSE 0 END) AS up_count,
         sum(CASE WHEN r.direction = 'down' THEN 1 ELSE 0 END) AS down_count
}
CALL {
  WITH d, tumor_type
  MATCH (og:Gene)-[r:DIFFERENTIALLY_EXPRESSED {tumor_type: tumor_type}]->(d)
  WHERE r.direction = 'up'
  WITH og, r ORDER BY r.log2fc DESC LIMIT 5
  RETURN collect(og.hgnc_symbol) AS top_up_genes
}
CALL {
  WITH d, tumor_type
  MATCH (og:Gene)-[r:DIFFERENTIALLY_EXPRESSED {tumor_type: tumor_type}]->(d)
  WHERE r.direction = 'down'
  WITH og, r ORDER BY r.log2fc ASC LIMIT 5
  RETURN collect(og.hgnc_symbol) AS top_down_genes
}
RETURN tumor_type,
       d.ontology_id AS efo_id,
       d.name AS disease_name,
       up_count, down_count, top_up_genes, top_down_genes
ORDER BY (up_count + down_count) DESC
"""


async def get_gene_cancer_associations(symbol: str) -> list[dict]:
    """Per-tumor-type differential-expression summary for a gene (Phase 3).

    Returns one row per tumor type the gene is DIFFERENTIALLY_EXPRESSED in, each
    with the tumor type's overall up/down gene counts and top-5 genes by log2fc.
    Empty list when the gene has no DIFFERENTIALLY_EXPRESSED edges (e.g. before
    13_tcga has run)."""
    async with get_session() as session:
        rows = await (await session.run(_GENE_CANCER, symbol=symbol)).data()
    return rows


# Backwards-compatible alias: callers that asked for a 2-hop subgraph now get the
# same signal-decay expansion (depth is governed by decay/min_signal, not hops).
async def get_gene_subgraph(
    ensembl_id: str,
    tissue: str = "all",
    decay: float | None = None,
    min_signal: float | None = None,
    max_nodes: int | None = None,
) -> dict:
    return await get_gene_neighborhood(
        ensembl_id, tissue, decay=decay, min_signal=min_signal, max_nodes=max_nodes
    )
