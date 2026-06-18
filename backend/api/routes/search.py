"""Search endpoints: full-text autocomplete (/api/search) + filtered, paginated
entity browser search (/api/entities)."""

from fastapi import APIRouter, Query

from backend.api import models
from backend.db.queries import graph as graph_queries

router = APIRouter(prefix="/api", tags=["search"])


@router.get("/search", response_model=list[models.SearchResult])
async def search(q: str = Query(..., min_length=1), limit: int = 10):
    rows = await graph_queries.search_nodes(q, limit)
    return [models.SearchResult(**row) for row in rows]


@router.get("/entities")
async def entities(
    q: str = "",
    types: str = "",  # comma-separated node types (Gene/Protein/Variant/Disease/...)
    chromosome: str | None = None,
    biotype: str | None = None,
    clinical: str | None = None,
    pli_min: float | None = None,
    limit: int = 50,
    offset: int = 0,
    page: int | None = None,  # 1-based alternative to offset
):
    limit = max(1, min(limit, 200))
    if page is not None:
        offset = max(0, (page - 1) * limit)
    type_list = [t.strip().lower() for t in types.split(",") if t.strip()]
    all_items = await graph_queries.search_entities(
        q, type_list, chromosome=chromosome, biotype=biotype,
        clinical=clinical, pli_min=pli_min,
    )
    total = len(all_items)
    page_items = all_items[offset : offset + limit]
    return {
        "items": page_items,
        "results": page_items,  # alias for the frontend browser
        "total": total,
        "has_more": offset + limit < total,
    }
