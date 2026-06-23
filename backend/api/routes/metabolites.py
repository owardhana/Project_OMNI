"""Metabolite endpoints: node lookup and signal-decay subgraph (Phase 3, ADR-0009)."""

from fastapi import APIRouter, HTTPException

from backend.api import models
from backend.config import settings
from backend.db.queries import metabolites as metabolite_queries

router = APIRouter(prefix="/api", tags=["metabolites"])


@router.get("/metabolite/{metabolite_id}", response_model=models.MetaboliteNode)
async def get_metabolite(metabolite_id: str):
    """Look up a metabolite by HMDB id (primary) or ChEBI id (fallback)."""
    record = await metabolite_queries.get_metabolite_by_id(metabolite_id)
    if not record:
        raise HTTPException(
            status_code=404, detail=f"Metabolite '{metabolite_id}' not found"
        )
    return models.metabolite_node_from_props(record["props"])


@router.get("/metabolite/{metabolite_id}/graph", response_model=models.GraphResponse)
async def get_metabolite_graph(
    metabolite_id: str,
    min_signal: float | None = None,
    decay: float | None = None,
    max_nodes: int | None = None,
):
    """Signal-decay subgraph seeded at a Metabolite node (ADR-0009): the signal
    flows Metabolite -> Protein (CATALYSES) -> the protein's molecular chain, same
    algorithm as a gene or disease seed. Metabolite is a first-class seed."""
    record = await metabolite_queries.get_metabolite_by_id(metabolite_id)
    if not record:
        raise HTTPException(
            status_code=404, detail=f"Metabolite '{metabolite_id}' not found"
        )
    # Seed on the metabolite's canonical key (hmdb_id primary, chebi_id fallback).
    props = record["props"]
    seed_key = props.get("hmdb_id") or props.get("chebi_id") or metabolite_id
    raw = await metabolite_queries.get_metabolite_neighborhood(
        seed_key, decay=decay, min_signal=min_signal, max_nodes=max_nodes
    )
    return models.graph_response_from_raw(raw, settings.tissues)
