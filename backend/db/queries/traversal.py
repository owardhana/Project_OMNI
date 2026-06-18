"""Signal-decay graph traversal (ADR-0005).

Confidence-gated spreading activation: a signal starts at 1.0 on the seed and
attenuates as `signal_next = signal_cur * decay * conductance(edge)`. Expansion is
**ring-batched** — one Cypher per hop (UNWIND the whole frontier), each node
expanded once (BFS) — then the accumulated visited set is trimmed to `max_nodes`
by `(signal desc, key)`. This replaces the old fixed hop-count traversal.

Conductance (ADR-0005 / ADR-0006):
  - REGULATES      -> edge confidence (0-1)
  - PRODUCES       -> structural constant (tissue is NOT a traversal input; it is a
                      frontend opacity channel only — ADR-0006)
  - TRANSLATES_TO  -> structural constant (~1.0)
  - ENCODES        -> structural constant (~1.0)

Returns the same raw dict shape as the old gene queries
({"nodes": [...], "edges": [...]}) so the API model builders are unchanged.
"""

import math

from backend.config import settings
from backend.db.neo4j_client import get_session

_TRAVERSAL_REL_TYPES = [
    "REGULATES", "PRODUCES", "TRANSLATES_TO", "ENCODES",
    "INTERACTS_WITH", "ASSOCIATED_WITH", "IN_GENE", "IMPLICATED_IN",
]

# Depth guard: signal decays by at most `decay` per hop, so it crosses any
# positive floor within a bounded number of hops; this is a hard safety cap.
_MAX_DEPTH = 8


def _conductance(rel_type: str, rel_props: dict) -> float:
    """Per-edge conductance (ADR-0005, extended for Phase 2 — 06_data_vision.md)."""
    if rel_type == "REGULATES":
        c = rel_props.get("confidence")
        return float(c) if c is not None else 0.5
    if rel_type == "PRODUCES":
        return settings.PRODUCES_CONDUCTANCE
    if rel_type in ("TRANSLATES_TO", "ENCODES", "IN_GENE"):
        return settings.STRUCTURAL_CONDUCTANCE  # ~1.0, structural
    if rel_type == "INTERACTS_WITH":
        c = rel_props.get("combined_score")
        return float(c) if c is not None else 0.5
    if rel_type == "ASSOCIATED_WITH":
        p = rel_props.get("p_value")
        if p and p > 0:
            return min(1.0, -math.log10(p) / 30.0)  # p=5e-8->~0.4, p=1e-30->1.0
        return 0.4
    if rel_type == "IMPLICATED_IN":
        return 0.5  # gene-disease rollup, lower weight
    return settings.STRUCTURAL_CONDUCTANCE


# High-degree Phase-2 edge types capped per node per frontier ring. The prompt
# specifies the cap for INTERACTS_WITH (hub proteins); the same hub explosion
# applies to a gene's variants (IN_GENE) and diseases (IMPLICATED_IN) and a
# common disease's variants (ASSOCIATED_WITH). Capping all four lets a hub seed
# reach MULTIPLE hops (e.g. gene -> protein -> INTERACTS_WITH) within max_nodes
# instead of one edge type flooding ring 1. Structural (PRODUCES/TRANSLATES_TO/
# ENCODES) and REGULATES edges stay uncapped — they are the molecular backbone.
_DENSE_CAPPED = {"INTERACTS_WITH", "ASSOCIATED_WITH", "IN_GENE", "IMPLICATED_IN"}


def _edge_rank(rel_type: str, rel_props: dict) -> float:
    """Higher = kept first when capping a node's expansion of a dense edge type."""
    props = rel_props or {}
    if rel_type == "INTERACTS_WITH":
        return props.get("combined_score") or 0.0
    if rel_type == "ASSOCIATED_WITH":
        p = props.get("p_value")
        return -math.log10(p) if (p and p > 0) else 0.0  # strongest GWAS first
    return 0.0  # IN_GENE / IMPLICATED_IN: structural rollup, no score -> first-k


def _cap_dense_frontier(rows: list[dict]) -> list[dict]:
    """Cap each node's expansion of the dense Phase-2 edge types to the top-k
    (settings.STRING_MAX_EXPAND_PER_NODE) so no single hub floods one frontier
    ring. Ties break deterministically by neighbour key."""
    cap = settings.STRING_MAX_EXPAND_PER_NODE
    grouped: dict[tuple[str, str], list[dict]] = {}
    passthrough: list[dict] = []
    for row in rows:
        if row["rel_type"] in _DENSE_CAPPED:
            grouped.setdefault((row["from_eid"], row["rel_type"]), []).append(row)
        else:
            passthrough.append(row)
    for (_from_eid, rel_type), group in grouped.items():
        group.sort(
            key=lambda r: (-_edge_rank(rel_type, r["rel_props"]), r["nb_key"] or "")
        )
        passthrough.extend(group[:cap])
    return passthrough


# Resolve seed business keys (ensembl_id / ensembl_tx_id / uniprot_id) to the
# internal elementId + node payload the traversal works with.
_RESOLVE_SEEDS = """
UNWIND $keys AS key
CALL {
  WITH key
  MATCH (n:Gene {ensembl_id: key}) RETURN n
  UNION WITH key MATCH (n:Transcript {ensembl_tx_id: key}) RETURN n
  UNION WITH key MATCH (n:Protein {uniprot_id: key}) RETURN n
  UNION WITH key MATCH (n:Variant {rsid: key}) RETURN n
  UNION WITH key MATCH (n:Disease {ontology_id: key}) RETURN n
}
RETURN elementId(n) AS eid,
       labels(n)[0] AS label,
       properties(n) AS props,
       coalesce(n.ensembl_id, n.ensembl_tx_id, n.uniprot_id, n.rsid, n.ontology_id) AS node_key
"""

# Expand one whole frontier ring in a single query.
_EXPAND_RING = f"""
UNWIND $eids AS eid
MATCH (n) WHERE elementId(n) = eid
MATCH (n)-[r]-(nb)
WHERE type(r) IN {_TRAVERSAL_REL_TYPES}
RETURN eid AS from_eid,
       elementId(nb) AS nb_eid,
       labels(nb)[0] AS nb_label,
       properties(nb) AS nb_props,
       coalesce(nb.ensembl_id, nb.ensembl_tx_id, nb.uniprot_id, nb.rsid, nb.ontology_id) AS nb_key,
       type(r) AS rel_type,
       elementId(r) AS rel_eid,
       r.confidence AS confidence,
       properties(r) AS rel_props,
       elementId(startNode(r)) AS start_eid,
       elementId(endNode(r)) AS end_eid,
       coalesce(startNode(r).ensembl_id, startNode(r).ensembl_tx_id, startNode(r).uniprot_id, startNode(r).rsid, startNode(r).ontology_id) AS source_key,
       coalesce(endNode(r).ensembl_id, endNode(r).ensembl_tx_id, endNode(r).uniprot_id, endNode(r).rsid, endNode(r).ontology_id) AS target_key
"""

# is_tf for a gene now means "encodes a TF protein", reachable via the
# transcript (PRODUCES -> TRANSLATES_TO) or directly (ENCODES). All our proteins
# are TFs, so existence of either path is sufficient.
_GENE_IS_TF = """
UNWIND $ensembl_ids AS eid
MATCH (g:Gene {ensembl_id: eid})
RETURN eid AS ensembl_id,
       (EXISTS { (g)-[:ENCODES]->(:Protein) }
        OR EXISTS { (g)-[:PRODUCES]->(:Transcript)-[:TRANSLATES_TO]->(:Protein) }) AS is_tf
"""


def _node_kind(label: str) -> str:
    return {
        "Gene": "gene", "Transcript": "transcript", "Protein": "protein",
        "Variant": "variant", "Disease": "disease",
    }.get(label, label.lower())


async def signal_decay_subgraph(
    seed_keys: list[str],
    decay: float | None = None,
    min_signal: float | None = None,
    max_nodes: int | None = None,
) -> dict:
    """Ring-batched signal-decay expansion from the given seed node keys.

    Tissue is intentionally NOT a parameter: it never gates traversal or presence
    (ADR-0006); the frontend dims by tissue weight, and tw_* are returned on
    PRODUCES edges so it can.
    """
    decay = settings.TRAVERSAL_DECAY if decay is None else decay
    min_signal = settings.TRAVERSAL_MIN_SIGNAL if min_signal is None else min_signal
    max_nodes = settings.TRAVERSAL_MAX_NODES if max_nodes is None else max_nodes

    signal: dict[str, float] = {}
    node_payload: dict[str, dict] = {}  # eid -> {label, props, key}
    edges: dict[str, dict] = {}  # rel_eid -> {rel_type, source, target, props, src_eid, tgt_eid}
    # The seed molecule's own vertical chain (its gene + transcripts + protein,
    # reachable from the seed via STRUCTURAL edges only) is pinned: it represents
    # the same molecule as the seed and must survive the max_nodes cap, even when
    # peripheral regulator-targets have higher signal.
    structural_only: dict[str, bool] = {}
    _STRUCTURAL = {"PRODUCES", "TRANSLATES_TO", "ENCODES"}

    async with get_session() as session:
        seed_rows = await (await session.run(_RESOLVE_SEEDS, keys=seed_keys)).data()
        if not seed_rows:
            return {"nodes": [], "edges": []}
        for row in seed_rows:
            signal[row["eid"]] = 1.0
            structural_only[row["eid"]] = True
            node_payload[row["eid"]] = {
                "label": row["label"],
                "props": row["props"],
                "key": row["node_key"],
            }

        expanded: set[str] = set()
        frontier = list(signal.keys())
        depth = 0
        while frontier and depth < _MAX_DEPTH and len(node_payload) < max_nodes:
            rows = await (await session.run(_EXPAND_RING, eids=frontier)).data()
            rows = _cap_dense_frontier(rows)
            expanded.update(frontier)
            next_frontier: list[str] = []
            for row in rows:
                cond = _conductance(row["rel_type"], row["rel_props"])
                new_sig = signal[row["from_eid"]] * decay * cond
                if new_sig < min_signal:
                    continue
                nb = row["nb_eid"]
                if nb not in node_payload:
                    node_payload[nb] = {
                        "label": row["nb_label"],
                        "props": row["nb_props"],
                        "key": row["nb_key"],
                    }
                if new_sig > signal.get(nb, 0.0):
                    signal[nb] = new_sig
                # Propagate seed-chain pinning along structural edges only.
                if structural_only.get(row["from_eid"]) and row["rel_type"] in _STRUCTURAL:
                    structural_only[nb] = True
                # Record the edge (dedup by relationship elementId).
                edges.setdefault(
                    row["rel_eid"],
                    {
                        "rel_type": row["rel_type"],
                        "source": row["source_key"],
                        "target": row["target_key"],
                        "props": row["rel_props"],
                        "src_eid": row["start_eid"],
                        "tgt_eid": row["end_eid"],
                    },
                )
                if nb not in expanded:
                    next_frontier.append(nb)
            # Dedup frontier, keep only not-yet-expanded.
            frontier = [e for e in dict.fromkeys(next_frontier) if e not in expanded]
            depth += 1

    # Trim to max_nodes by (signal desc, key), but always keep the seed molecule's
    # pinned vertical chain (ADR-0005/0006: the seed and its own gene/transcripts/
    # protein are the subject of the query and never get capped out).
    ordered = sorted(
        node_payload.keys(),
        key=lambda e: (-signal.get(e, 0.0), node_payload[e]["key"]),
    )
    pinned = [e for e in ordered if structural_only.get(e)]
    rest = [e for e in ordered if not structural_only.get(e)]
    kept = set(pinned) | set(rest[: max(0, max_nodes - len(pinned))])
    kept_eids = [e for e in ordered if e in kept]  # keep deterministic order

    # is_tf for kept genes (re-routed through the protein — ADR-0004).
    gene_ensembl = [
        node_payload[e]["props"]["ensembl_id"]
        for e in kept
        if node_payload[e]["label"] == "Gene"
    ]
    is_tf_map: dict[str, bool] = {}
    if gene_ensembl:
        async with get_session() as session:
            for row in await (
                await session.run(_GENE_IS_TF, ensembl_ids=gene_ensembl)
            ).data():
                is_tf_map[row["ensembl_id"]] = bool(row["is_tf"])

    nodes: list[dict] = []
    for e in kept_eids:
        p = node_payload[e]
        kind = _node_kind(p["label"])
        node = {"kind": kind, "props": p["props"]}
        if kind == "gene":
            node["is_tf"] = is_tf_map.get(p["props"]["ensembl_id"], False)
        nodes.append(node)

    out_edges = [
        {
            "rel_type": ed["rel_type"],
            "source": ed["source"],
            "target": ed["target"],
            "props": ed["props"],
        }
        for ed in edges.values()
        if ed["src_eid"] in kept and ed["tgt_eid"] in kept
    ]
    return {"nodes": nodes, "edges": out_edges}
