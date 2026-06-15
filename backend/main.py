"""OmniGraph FastAPI application.

Startup creates the Neo4j indexes (idempotent). The APScheduler citation cron,
the /api/query route, and the /admin routes are wired in Phase 4 once the
QueryAgent and CitationAgent exist — they are intentionally not imported here so
the backend is runnable and testable after Phase 3.

Run locally:
    PYTHONPATH=. uvicorn backend.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.db.neo4j_client import close_driver, create_indexes
from backend.api.routes import genes, search, transcripts

CORS_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_indexes()
    yield
    await close_driver()


app = FastAPI(title="OmniGraph API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(genes.router)
app.include_router(transcripts.router)
app.include_router(search.router)


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}
