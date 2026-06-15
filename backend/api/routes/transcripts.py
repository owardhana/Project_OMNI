"""Transcript endpoints."""

from fastapi import APIRouter, HTTPException

from backend.api import models
from backend.db.queries import transcripts as tx_queries

router = APIRouter(prefix="/api", tags=["transcripts"])


@router.get("/transcript/{ensembl_tx_id}", response_model=models.TranscriptNode)
async def get_transcript(ensembl_tx_id: str):
    props = await tx_queries.get_transcript_by_id(ensembl_tx_id)
    if not props:
        raise HTTPException(
            status_code=404, detail=f"Transcript '{ensembl_tx_id}' not found"
        )
    return models.transcript_node_from_props(props)
