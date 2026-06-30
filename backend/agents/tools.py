"""Read-only graph tools for the ChatAgent (Feature 1).

The agentic chatbot is given these typed functions instead of only raw Cypher: the
LLM picks a tool, we execute it against the live graph, and feed the (compact) result
back. Every tool is READ-ONLY — same safety posture as QueryAgent (no writes, ever).
``run_cypher`` keeps the Text2Cypher escape hatch but routes through validate_cypher.

Tool results are deliberately COMPACT (trimmed node fields, capped lists) to keep the
agent's context — and token cost — bounded.
"""

import asyncio
import json
import logging

from backend.db.neo4j_client import get_session
from backend.db.queries.graph import search_entities
from backend.db.queries.traversal import signal_decay_subgraph
from backend.llm.validators import validate_cypher

logger = logging.getLogger(__name__)

_CYPHER_TIMEOUT_S = 10
_MAX_ROWS = 30
_MAX_NODES_RETURNED = 60


def _display(kind: str, props: dict) -> str:
    return (
        props.get("hgnc_symbol")
        or props.get("name")
        or props.get("rsid")
        or props.get("uniprot_id")
        or props.get("ensembl_tx_id")
        or props.get("ensembl_id")
        or props.get("ontology_id")
        or "?"
    )


def _node_id(props: dict) -> str | None:
    for k in ("ensembl_id", "uniprot_id", "rsid", "ontology_id", "hmdb_id",
              "chebi_id", "ensembl_tx_id"):
        if props.get(k):
            return props[k]
    return None


def _compact_graph(raw: dict) -> dict:
    """Subgraph -> {counts, nodes[:N], edges[:N]} with only display fields."""
    nodes = raw.get("nodes", [])
    edges = raw.get("edges", [])
    by_kind: dict[str, int] = {}
    for n in nodes:
        by_kind[n["kind"]] = by_kind.get(n["kind"], 0) + 1
    by_rel: dict[str, int] = {}
    for e in edges:
        by_rel[e["rel_type"]] = by_rel.get(e["rel_type"], 0) + 1
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes_by_kind": by_kind,
        "edges_by_type": by_rel,
        "nodes": [
            {"kind": n["kind"], "id": _node_id(n["props"]),
             "name": _display(n["kind"], n["props"])}
            for n in nodes[:_MAX_NODES_RETURNED]
        ],
        "edges": [
            {"source": e["source"], "rel": e["rel_type"], "target": e["target"]}
            for e in edges[:_MAX_NODES_RETURNED]
        ],
    }


# --- the tool implementations ------------------------------------------------

async def _search_graph(query: str, types: list[str] | None = None) -> dict:
    rows = await search_entities(query, types or [])
    return {
        "results": [
            {"kind": r.get("node_type"), "id": r.get("id"),
             "name": r.get("display_name")}
            for r in rows[:20]
        ]
    }


async def _get_subgraph(seed_ids: list[str]) -> dict:
    raw = await signal_decay_subgraph(seed_ids)
    if not raw["nodes"]:
        return {"error": f"no entity resolved for seeds {seed_ids}"}
    return _compact_graph(raw)


async def _shortest_path(from_id: str, from_type: str, to_id: str,
                         to_type: str) -> dict:
    # Imported here to avoid a route<->agent import cycle at module load.
    from backend.api.routes.graph import graph_path
    resp = await graph_path(
        from_id=from_id, type_a=from_type, to_id=to_id, type_b=to_type, max_hops=6
    )
    # PathResponse nodes are typed models (GeneNode/ProteinNode/...) with `node_type`,
    # not generic {kind, props} — dump to a dict and reuse the display helper.
    return {
        "path_found": resp.path_found,
        "quality": resp.path_quality,
        "hops": resp.hop_count,
        "nodes": [
            {"kind": n.node_type, "name": _display(n.node_type, n.model_dump())}
            for n in resp.nodes
        ],
        "warning": resp.warning,
    }


async def _run_cypher(cypher: str) -> dict:
    if not await validate_cypher(cypher):
        return {"error": "query rejected — must be a single read-only (MATCH) query"}

    async def _inner() -> list[dict]:
        async with get_session() as session:
            return await (await session.run(cypher)).data()

    try:
        rows = await asyncio.wait_for(_inner(), timeout=_CYPHER_TIMEOUT_S)
    except asyncio.TimeoutError:
        return {"error": "query timed out (>10s)"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"execution failed: {exc}"}
    return {"row_count": len(rows), "rows": rows[:_MAX_ROWS]}


# --- OpenAI tool schemas (advertised to the model) ---------------------------

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_graph",
            "description": "Find entities by name/symbol/description. Returns canonical "
                           "ids to use as seeds for other tools.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "name, HGNC symbol, rsid, or text"},
                    "types": {
                        "type": "array", "items": {"type": "string"},
                        "description": "optional filter: gene/protein/transcript/variant/disease/metabolite",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_subgraph",
            "description": "Signal-decay neighbourhood around one or more seed ids "
                           "(ensembl_id/uniprot_id/rsid/ontology_id/hmdb_id). Use to "
                           "explore what an entity connects to.",
            "parameters": {
                "type": "object",
                "properties": {
                    "seed_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["seed_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shortest_path",
            "description": "Shortest path (<=6 hops) between two entities — use to "
                           "explain HOW two things are connected.",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_id": {"type": "string"},
                    "from_type": {"type": "string"},
                    "to_id": {"type": "string"},
                    "to_type": {"type": "string"},
                },
                "required": ["from_id", "from_type", "to_id", "to_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_cypher",
            "description": "Run a READ-ONLY Cypher query for aggregations/counts the "
                           "other tools can't express. Single MATCH query, no writes.",
            "parameters": {
                "type": "object",
                "properties": {"cypher": {"type": "string"}},
                "required": ["cypher"],
            },
        },
    },
]

_DISPATCH = {
    "search_graph": _search_graph,
    "get_subgraph": _get_subgraph,
    "shortest_path": _shortest_path,
    "run_cypher": _run_cypher,
}


async def dispatch_tool(name: str, arguments: str) -> str:
    """Execute a tool call by name with JSON-string arguments; return a JSON string
    result (always — errors become {"error": ...} so the agent can recover)."""
    fn = _DISPATCH.get(name)
    if fn is None:
        return json.dumps({"error": f"unknown tool {name}"})
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return json.dumps({"error": "arguments were not valid JSON"})
    try:
        result = await fn(**args)
    except TypeError as exc:
        return json.dumps({"error": f"bad arguments for {name}: {exc}"})
    except Exception as exc:  # noqa: BLE001 — one bad tool call must not kill the chat
        logger.warning("tool %s failed: %s", name, exc)
        return json.dumps({"error": f"{name} failed: {exc}"})
    return json.dumps(result, default=str)
