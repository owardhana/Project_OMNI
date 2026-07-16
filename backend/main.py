"""OmicGraph FastAPI application.

Startup creates the Neo4j indexes (idempotent) and starts the APScheduler
nightly CitationAgent cron. All routers (genes, transcripts, search, chat,
admin) are registered here.

Run locally:
    PYTHONPATH=. uvicorn backend.main:app --reload
"""

import logging
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
from backend.mcp_server import mcp as mcp_server

CORS_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_indexes()
    # The /admin router promotes trusted topology; on a host where the extractor is
    # live it must not be left open. Warn loudly if the ADMIN_TOKEN gate is unset
    # (Caddy basic-auth alone is easy to forget) — see ADR-0014 §3.
    if settings.EXTRACTION_AGENT_ENABLED and not settings.ADMIN_TOKEN:
        logging.getLogger(__name__).warning(
            "EXTRACTION_AGENT_ENABLED but ADMIN_TOKEN is empty — /admin write routes are "
            "UNGATED at the app layer. Set ADMIN_TOKEN on any shared/public host."
        )
    scheduler.add_job(
        citation_agent.run,
        CronTrigger(hour=settings.CITATION_AGENT_CRON_HOUR),
        id="citation_nightly",
        replace_existing=True,
    )
    # Embedding crawl spends on the OpenRouter API, so its nightly cron is opt-in
    # (EMBEDDING_AGENT_CRON_ENABLED, default off). Populate on demand via
    # POST /admin/agents/embedding/run; the semantic_search tool reads existing vectors.
    if settings.EMBEDDING_AGENT_CRON_ENABLED:
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


app = FastAPI(title="OmicGraph API", version="0.1.0", lifespan=lifespan)

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

# Read-only MCP server (ADR-0017, Pillar 2): remote agents/clients reach the graph at
# /mcp (behind Caddy). SSE transport; exposes only the bounded typed tools — never
# run_cypher. YAGNI: no API-key / quota layer yet (arrives with the landing key flow).
# mount_path="/mcp" makes the SSE stream advertise the POST endpoint as /mcp/messages/
# (matching where the app is mounted); without it clients are told to POST to /messages/
# and the handshake breaks behind the /mcp Caddy route.
app.mount("/mcp", mcp_server.sse_app(mount_path="/mcp"))


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}
