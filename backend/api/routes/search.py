"""Search endpoint: full-text gene search for autocomplete."""

from fastapi import APIRouter, Query

from backend.api import models
from backend.db.queries import graph as graph_queries

router = APIRouter(prefix="/api", tags=["search"])


@router.get("/search", response_model=list[models.SearchResult])
async def search(q: str = Query(..., min_length=1), limit: int = 10):
    rows = await graph_queries.search_genes(q, limit)
    return [models.SearchResult(**row) for row in rows]
