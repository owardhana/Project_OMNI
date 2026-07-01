"""OmniGraph FastAPI application.

Startup creates the Neo4j indexes (idempotent) and starts the APScheduler
nightly CitationAgent cron. All routers (genes, transcripts, search, chat,
admin) are registered here.

Run locally:
    PYTHONPATH=. uvicorn backend.main:app --reload
"""

from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.agents.citation_agent import citation_agent
from backend.agents.embedding_agent import embedding_agent
from backend.api.routes import (
    admin, chat, genes, graph, metabolites, search, transcripts,
)
from backend.config import settings
from backend.db.neo4j_client import close_driver, create_indexes

CORS_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_indexes()
    scheduler.add_job(
        citation_agent.run,
        CronTrigger(hour=settings.CITATION_AGENT_CRON_HOUR),
        id="citation_nightly",
        replace_existing=True,
    )
    scheduler.add_job(
        embedding_agent.run,
        CronTrigger(hour=settings.EMBEDDING_AGENT_CRON_HOUR),
        id="embedding_nightly",
        replace_existing=True,
    )
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)
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
app.include_router(graph.router)
app.include_router(metabolites.router)
app.include_router(transcripts.router)
app.include_router(search.router)
app.include_router(chat.router)
app.include_router(admin.router)


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}
