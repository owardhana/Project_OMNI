"""OmicGraph MCP server — public, read-only graph access (Pillar 2, ADR-0017).

Thin MCP wrapper over the ChatAgent's existing read-only tools
(``backend/agents/tools.py``) — no new capability, just MCP transport for external
agents/clients. Exposes: search_graph · semantic_search · get_subgraph ·
shortest_path · export_subgraph (bounded).

**run_cypher is deliberately NOT exposed.** An arbitrary Cypher endpoint is a DoS
surface even when read-only (ADR-0017); the public surface is the bounded, typed tools
only. Query cost is further capped by the 60s Neo4j transaction timeout and the
traversal ``max_nodes`` guardrail. Whole-graph extraction is a separate pre-baked,
versioned dump — never a live tool.

YAGNI (ADR-0017): no API-key / per-key quota layer here yet — get the transport working
first; key issuance + rate limiting arrive with the landing page's key flow.

Run:
  - stdio (local MCP clients):  ``python -m backend.mcp_server``
  - remote HTTP: the ASGI app is mounted on FastAPI at ``/mcp`` (behind Caddy) —
    see ``backend/main.py``.
"""

import json

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from backend.agents.tools import (
    _get_subgraph,
    _search_graph,
    _semantic_search,
    _shortest_path,
)
from backend.db.queries.traversal import signal_decay_subgraph

# This server is a DELIBERATELY PUBLIC, read-only surface reached over HTTP/SSE behind
# Caddy (ADR-0017). The MCP SDK enables DNS-rebinding protection by default and only
# accepts localhost Host headers, so a proxied request (Host: the public domain/IP) is
# rejected with 421 "Request validation failed". That protection guards *private/localhost*
# servers from browser DNS-rebinding attacks; it does not apply to an intentionally-public,
# unauthenticated, read-only endpoint (Caddy is the sole ingress). Disable it so remote
# clients can connect; keep it enabled if this ever binds directly to localhost for a
# private client.
mcp = FastMCP(
    "OmicGraph",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


@mcp.tool()
async def search_graph(query: str, types: list[str] | None = None) -> dict:
    """Find entities by name / HGNC symbol / rsid / description. Returns canonical ids
    (ensembl_id / uniprot_id / rsid / ontology_id / hmdb_id) to seed the other tools.
    Optional ``types`` filter: gene/protein/transcript/variant/disease/metabolite."""
    return await _search_graph(query, types)


@mcp.tool()
async def semantic_search(query: str, kinds: list[str] | None = None) -> dict:
    """Find Gene/Protein/Disease by MEANING (embedding similarity) — for concept queries
    like 'enzymes in glucose metabolism' rather than an exact name."""
    return await _semantic_search(query, kinds)


@mcp.tool()
async def get_subgraph(seed_ids: list[str], compartment_filter: bool = False) -> dict:
    """Signal-decay neighbourhood around one or more seed ids — what an entity connects
    to. Bounded by the traversal max_nodes cap; returns compact node/edge summaries.
    ``compartment_filter=true`` keeps only protein-protein interactions whose partners
    share a subcellular compartment (ADR-0015)."""
    return await _get_subgraph(seed_ids, compartment_filter)


@mcp.tool()
async def shortest_path(
    from_id: str, from_type: str, to_id: str, to_type: str
) -> dict:
    """Shortest path (<=6 hops) between two entities — explains HOW they are connected.
    Types are gene/protein/transcript/variant/disease/metabolite."""
    return await _shortest_path(from_id, from_type, to_id, to_type)


@mcp.tool()
async def export_subgraph(seed_ids: list[str], fmt: str = "json") -> str:
    """Bounded export of a seed neighbourhood for download. ``fmt`` = 'json' (full
    nodes + edges) or 'csv' (edge list: source,rel_type,target). Bounded by the
    traversal max_nodes cap — there is no whole-graph dump here (that is a separate
    pre-baked release, ADR-0017)."""
    raw = await signal_decay_subgraph(seed_ids)
    if fmt == "csv":
        lines = ["source,rel_type,target"]
        lines.extend(
            f'{e["source"]},{e["rel_type"]},{e["target"]}' for e in raw.get("edges", [])
        )
        return "\n".join(lines)
    return json.dumps(raw, default=str)


if __name__ == "__main__":
    mcp.run()  # stdio transport
