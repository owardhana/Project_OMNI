"""Gene endpoints: node lookup and neighborhood/subgraph."""

from fastapi import APIRouter, HTTPException

from backend.api import models
from backend.config import settings
from backend.db.queries import genes as gene_queries

router = APIRouter(prefix="/api", tags=["genes"])


@router.get("/gene/{hgnc_symbol}", response_model=models.GeneNode)
async def get_gene(hgnc_symbol: str):
    record = await gene_queries.get_gene_by_symbol(hgnc_symbol)
    if not record:
        raise HTTPException(status_code=404, detail=f"Gene '{hgnc_symbol}' not found")
    return models.gene_node_from_props(record["props"], record["is_tf"])


@router.get("/gene/{hgnc_symbol}/graph", response_model=models.GraphResponse)
async def get_gene_graph(hgnc_symbol: str, tissue: str = "all", hops: int = 1):
    record = await gene_queries.get_gene_by_symbol(hgnc_symbol)
    if not record:
        raise HTTPException(status_code=404, detail=f"Gene '{hgnc_symbol}' not found")
    ensembl_id = record["props"]["ensembl_id"]
    try:
        if hops >= 2:
            raw = await gene_queries.get_gene_subgraph(ensembl_id, tissue, max_hops=2)
        else:
            raw = await gene_queries.get_gene_neighborhood(ensembl_id, tissue, max_hops=1)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return models.graph_response_from_raw(raw, settings.tissues)
