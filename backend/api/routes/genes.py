"""Gene endpoints: node lookup and neighborhood/subgraph."""

from fastapi import APIRouter, HTTPException

from backend.api import models
from backend.config import settings
from backend.db.queries import diseases as disease_queries
from backend.db.queries import genes as gene_queries
from backend.db.queries.traversal import signal_decay_subgraph

router = APIRouter(prefix="/api", tags=["genes"])


@router.get("/gene/{hgnc_symbol}", response_model=models.GeneNode)
async def get_gene(hgnc_symbol: str):
    record = await gene_queries.get_gene_by_symbol(hgnc_symbol)
    if not record:
        raise HTTPException(status_code=404, detail=f"Gene '{hgnc_symbol}' not found")
    return models.gene_node_from_props(record["props"], record["is_tf"])


@router.get("/gene/{hgnc_symbol}/graph", response_model=models.GraphResponse)
async def get_gene_graph(
    hgnc_symbol: str,
    tissue: str = "all",
    min_signal: float | None = None,
    decay: float | None = None,
    max_nodes: int | None = None,
):
    """Signal-decay subgraph around a gene (ADR-0005). All traversal params are
    optional and fall back to configured defaults, so a paramless call works.
    `tissue` is accepted for API compatibility but does not gate the graph
    (ADR-0006) — the frontend uses tw_* for opacity."""
    record = await gene_queries.get_gene_by_symbol(hgnc_symbol)
    if not record:
        raise HTTPException(status_code=404, detail=f"Gene '{hgnc_symbol}' not found")
    ensembl_id = record["props"]["ensembl_id"]
    try:
        raw = await gene_queries.get_gene_neighborhood(
            ensembl_id, tissue, decay=decay, min_signal=min_signal, max_nodes=max_nodes
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return models.graph_response_from_raw(raw, settings.tissues)


@router.get("/disease/{ontology_id}/graph", response_model=models.GraphResponse)
async def get_disease_graph(
    ontology_id: str,
    min_signal: float | None = None,
    decay: float | None = None,
    max_nodes: int | None = None,
):
    """Signal-decay subgraph seeded at a Disease node (ADR-0007): the signal
    flows Disease -> Variant (ASSOCIATED_WITH) -> Gene (IN_GENE) -> Protein /
    Transcript, same algorithm as the gene seed, starting at signal 1.0."""
    record = await disease_queries.get_disease_by_ontology_id(ontology_id)
    if not record:
        raise HTTPException(
            status_code=404, detail=f"Disease '{ontology_id}' not found"
        )
    raw = await signal_decay_subgraph(
        [ontology_id], decay=decay, min_signal=min_signal, max_nodes=max_nodes
    )
    return models.graph_response_from_raw(raw, settings.tissues)
