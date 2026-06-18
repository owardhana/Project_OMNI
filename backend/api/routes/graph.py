"""Multi-seed merge + shortest-path endpoints (06_data_vision.md Phase 2).

POST /api/graph/multi — run signal-decay traversal from several seeds in parallel,
merge into one graph, flag disconnected clusters.
GET  /api/graph/path  — shortestPath between two entities, hard-capped at 6 hops.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Query

from backend.api import models
from backend.config import settings
from backend.db.queries.genes import get_gene_by_symbol
from backend.db.neo4j_client import get_session
from backend.db.queries.traversal import signal_decay_subgraph

router = APIRouter(prefix="/api", tags=["graph"])

# type -> (label, canonical id field). Also an allowlist (interpolated into Cypher).
_LABEL_FIELD: dict[str, tuple[str, str]] = {
    "gene": ("Gene", "ensembl_id"),
    "transcript": ("Transcript", "ensembl_tx_id"),
    "protein": ("Protein", "uniprot_id"),
    "variant": ("Variant", "rsid"),
    "disease": ("Disease", "ontology_id"),
}
_KIND = {
    "Gene": "gene", "Transcript": "transcript", "Protein": "protein",
    "Variant": "variant", "Disease": "disease",
}
_MAX_HOPS = 6  # hard cap (biologically meaningless beyond this)


def _node_key(node: dict) -> str | None:
    p = node["props"]
    return (
        p.get("ensembl_id") or p.get("uniprot_id") or p.get("rsid")
        or p.get("ontology_id") or p.get("ensembl_tx_id")
    )


async def _resolve_seed_key(seed_id: str, seed_type: str) -> str | None:
    """Map a UI seed (gene symbol or machine id) to a traversal seed key."""
    if seed_type == "gene" and not seed_id.startswith("ENSG"):
        record = await get_gene_by_symbol(seed_id)
        return record["props"]["ensembl_id"] if record else None
    return seed_id  # protein/variant/disease ids resolve directly in the traversal


def _merge_raw(raws: list[dict]) -> dict:
    nodes: dict[str, dict] = {}
    edges: dict[tuple, dict] = {}
    for raw in raws:
        for n in raw["nodes"]:
            key = _node_key(n)
            if key is not None:
                nodes.setdefault(key, n)
        for e in raw["edges"]:
            edges.setdefault((e["source"], e["rel_type"], e["target"]), e)
    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


def _component_count(node_keys: set[str], edges: list[dict]) -> int:
    adj: dict[str, set[str]] = {k: set() for k in node_keys}
    for e in edges:
        s, t = e["source"], e["target"]
        if s in adj and t in adj:
            adj[s].add(t)
            adj[t].add(s)
    seen: set[str] = set()
    components = 0
    for start in node_keys:
        if start in seen:
            continue
        components += 1
        stack = [start]
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            stack.extend(y for y in adj[x] if y not in seen)
    return components


@router.post("/graph/multi", response_model=models.GraphResponse)
async def graph_multi(req: models.MultiGraphRequest):
    if len(req.seed_ids) != len(req.seed_types):
        raise HTTPException(400, "seed_ids and seed_types must be parallel lists")
    keys = [
        k
        for sid, stype in zip(req.seed_ids, req.seed_types)
        if (k := await _resolve_seed_key(sid, stype)) is not None
    ]
    if not keys:
        return models.GraphResponse(nodes=[], edges=[])

    raws = await asyncio.gather(*(signal_decay_subgraph([k]) for k in keys))
    merged = _merge_raw(raws)
    node_keys = {k for n in merged["nodes"] if (k := _node_key(n)) is not None}
    components = _component_count(node_keys, merged["edges"])

    resp = models.graph_response_from_raw(merged, settings.tissues)
    resp.metadata = {
        "connected": components <= 1,
        "component_count": components,
        "seeds": req.seed_ids,
    }
    if components > 1:
        resp.warnings = [
            models.GraphWarning(
                type="disconnected",
                component_count=components,
                message=(
                    f"{components} of {len(keys)} selected entities form separate "
                    "clusters — they may not be directly connected at this signal "
                    "threshold."
                ),
            )
        ]
    return resp


def _path_quality(hops: int) -> str:
    if hops <= 2:
        return "direct"
    if hops <= 4:
        return "moderate"
    return "weak"


@router.get("/graph/path", response_model=models.PathResponse)
async def graph_path(
    from_id: str = Query(...),
    type_a: str = Query(...),
    to_id: str = Query(...),
    type_b: str = Query(...),
    max_hops: int = Query(_MAX_HOPS),
):
    la, lb = _LABEL_FIELD.get(type_a), _LABEL_FIELD.get(type_b)
    if not la or not lb:
        raise HTTPException(400, f"unknown entity type: {type_a} / {type_b}")
    # Clamp the caller's request to [1, 6] and use it in the Cypher literal below
    # (an int — injection-safe). 6 is the hard biological cap.
    hops = max(1, min(max_hops, _MAX_HOPS))

    a_key = await _resolve_seed_key(from_id, type_a)
    b_key = await _resolve_seed_key(to_id, type_b)
    no_path_msg = (
        "No path found within 6 hops. These entities may not be directly "
        "connected at current data resolution."
    )
    if not a_key or not b_key:
        return models.PathResponse(
            path_found=False, path_quality="no_path", warning=no_path_msg
        )

    cypher = f"""
    MATCH (a:{la[0]} {{{la[1]}: $a}}), (b:{lb[0]} {{{lb[1]}: $b}})
    MATCH p = shortestPath((a)-[*..{hops}]-(b))
    RETURN [n IN nodes(p) | {{label: labels(n)[0], props: properties(n)}}] AS ns,
           [r IN relationships(p) | {{
               rel_type: type(r), props: properties(r),
               source: coalesce(startNode(r).ensembl_id, startNode(r).ensembl_tx_id,
                   startNode(r).uniprot_id, startNode(r).rsid, startNode(r).ontology_id),
               target: coalesce(endNode(r).ensembl_id, endNode(r).ensembl_tx_id,
                   endNode(r).uniprot_id, endNode(r).rsid, endNode(r).ontology_id)
           }}] AS es
    """
    async with get_session() as session:
        rows = await (await session.run(cypher, a=a_key, b=b_key)).data()
    if not rows or not rows[0]["ns"]:
        return models.PathResponse(
            path_found=False, path_quality="no_path", warning=no_path_msg
        )

    raw_nodes = [{"kind": _KIND.get(n["label"], n["label"].lower()), "props": n["props"]}
                 for n in rows[0]["ns"]]
    raw = {"nodes": raw_nodes, "edges": rows[0]["es"]}
    typed = models.graph_response_from_raw(raw, settings.tissues)
    hops = len(rows[0]["es"])
    quality = _path_quality(hops)
    warning = (
        f"This path spans {hops} hops and may not represent a direct biological "
        "relationship." if quality == "weak" else None
    )
    return models.PathResponse(
        path_found=True,
        hop_count=hops,
        path_quality=quality,
        nodes=typed.nodes,
        edges=typed.edges,
        warning=warning,
    )
