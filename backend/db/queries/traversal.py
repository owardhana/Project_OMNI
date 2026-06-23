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
    # Phase 3 (docs/data-architecture.md): TCGA differential expression edge
    # (Gene -> Disease) and the Recon3D enzymatic edge (Protein -> Metabolite).
    "DIFFERENTIALLY_EXPRESSED", "CATALYSES",
]

# Depth guard: signal decays by at most `decay` per hop, so it crosses any
# positive floor within a bounded number of hops; this is a hard safety cap.
_MAX_DEPTH = 8

# Structural ("vertical") edges — the molecular backbone gene->transcript->protein.
# Used by the backbone pre-pass (ADR-0011) and to propagate seed-chain pinning.
_STRUCTURAL_RELS = ["PRODUCES", "TRANSLATES_TO", "ENCODES"]
# The vertical chain is short (gene->transcript->protein is 2 hops); 4 is ample
# headroom and bounds the pre-pass.
_BACKBONE_MAX_DEPTH = 4


def _conductance(rel_type: str, rel_props: dict) -> float:
    """Per-edge conductance (ADR-0005, extended for Phase 2 — docs/data-architecture.md)."""
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
    if rel_type == "DIFFERENTIALLY_EXPRESSED":
        # Stronger fold-change = higher conductance (|log2fc|=4 saturates to 1.0).
        lfc = rel_props.get("log2fc")
        return min(1.0, abs(float(lfc)) / 4.0) if lfc is not None else 0.25
    if rel_type == "CATALYSES":
        return 0.7  # enzymatic link — moderately confident structural (ADR-0009)
    return settings.STRUCTURAL_CONDUCTANCE


# High-degree edge types capped per node per frontier ring. The prompt specifies
# the cap for INTERACTS_WITH (hub proteins); the same hub explosion applies to a
# gene's variants (IN_GENE) and diseases (IMPLICATED_IN), a common disease's
# variants (ASSOCIATED_WITH), and — now that the full proteome is loaded (ADR-0010)
# — REGULATES: a master-regulator TF (TP53 regulates hundreds of genes) floods
# ring 1 with regulated genes, starving the molecular backbone so a gene seed never
# reaches its proteins/metabolites. Capping lets a hub seed reach MULTIPLE hops
# (gene -> protein -> CATALYSES -> metabolite) within max_nodes. Structural
# (PRODUCES/TRANSLATES_TO/ENCODES) edges stay uncapped — they ARE the backbone.
# CATALYSES is NOT capped — most proteins catalyse only 1-5 reactions, no explosion.
# Per-type cap: REGULATES gets a higher cap (REGULATES_MAX_EXPAND_PER_NODE) so the
# regulatory story still reads; the rest use STRING_MAX_EXPAND_PER_NODE.
_DENSE_CAPPED = {
    "INTERACTS_WITH", "ASSOCIATED_WITH", "IN_GENE", "IMPLICATED_IN",
    "DIFFERENTIALLY_EXPRESSED", "REGULATES",
}


def _cap_for(rel_type: str) -> int:
    """Per-node expansion cap for a dense edge type."""
    if rel_type == "REGULATES":
        return settings.REGULATES_MAX_EXPAND_PER_NODE
    return settings.STRING_MAX_EXPAND_PER_NODE


def _edge_rank(rel_type: str, rel_props: dict) -> float:
    """Higher = kept first when capping a node's expansion of a dense edge type."""
    props = rel_props or {}
    if rel_type == "INTERACTS_WITH":
        return props.get("combined_score") or 0.0
    if rel_type == "ASSOCIATED_WITH":
        p = props.get("p_value")
        return -math.log10(p) if (p and p > 0) else 0.0  # strongest GWAS first
    if rel_type == "DIFFERENTIALLY_EXPRESSED":
        lfc = props.get("log2fc")
        return abs(float(lfc)) if lfc is not None else 0.0  # strongest fold-change first
    if rel_type == "REGULATES":
        return props.get("confidence") or 0.0  # highest-confidence DoRothEA targets first
    return 0.0  # IN_GENE / IMPLICATED_IN: structural rollup, no score -> first-k


def _cap_dense_frontier(rows: list[dict]) -> list[dict]:
    """Cap each node's expansion of the dense edge types to the top-k (per-type cap)
    so no single hub floods one frontier ring. Ties break deterministically by
    neighbour key."""
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
        passthrough.extend(group[: _cap_for(rel_type)])
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
  UNION WITH key MATCH (n:Metabolite {hmdb_id: key}) RETURN n
  UNION WITH key MATCH (n:Metabolite {chebi_id: key}) RETURN n
}
RETURN elementId(n) AS eid,
       labels(n)[0] AS label,
       properties(n) AS props,
       coalesce(n.ensembl_id, n.ensembl_tx_id, n.uniprot_id, n.rsid, n.ontology_id, n.hmdb_id, n.chebi_id) AS node_key
"""

# Expand one whole frontier ring in a single query. `$rel_types` lets the caller
# restrict the edge set: the breadth phase passes all _TRAVERSAL_REL_TYPES; the
# backbone pre-pass (ADR-0011) passes only structural edges, then only CATALYSES.
_EXPAND_RING = """
UNWIND $eids AS eid
MATCH (n) WHERE elementId(n) = eid
MATCH (n)-[r]-(nb)
WHERE type(r) IN $rel_types
RETURN eid AS from_eid,
       elementId(nb) AS nb_eid,
       labels(nb)[0] AS nb_label,
       properties(nb) AS nb_props,
       coalesce(nb.ensembl_id, nb.ensembl_tx_id, nb.uniprot_id, nb.rsid, nb.ontology_id, nb.hmdb_id, nb.chebi_id) AS nb_key,
       type(r) AS rel_type,
       elementId(r) AS rel_eid,
       r.confidence AS confidence,
       properties(r) AS rel_props,
       elementId(startNode(r)) AS start_eid,
       elementId(endNode(r)) AS end_eid,
       coalesce(startNode(r).ensembl_id, startNode(r).ensembl_tx_id, startNode(r).uniprot_id, startNode(r).rsid, startNode(r).ontology_id, startNode(r).hmdb_id, startNode(r).chebi_id) AS source_key,
       coalesce(endNode(r).ensembl_id, endNode(r).ensembl_tx_id, endNode(r).uniprot_id, endNode(r).rsid, endNode(r).ontology_id, endNode(r).hmdb_id, endNode(r).chebi_id) AS target_key
"""

# is_tf for a gene now means "encodes a TF protein", reachable via the
# transcript (PRODUCES -> TRANSLATES_TO) or directly (ENCODES). All our proteins
# are TFs, so existence of either path is sufficient.
# subtype filter REQUIRED post-ADR-0010: full proteome means ~20k genes reach a
# Protein, so an unfiltered clause flags every protein-coding gene as a TF.
_GENE_IS_TF = """
UNWIND $ensembl_ids AS eid
MATCH (g:Gene {ensembl_id: eid})
RETURN eid AS ensembl_id,
       (EXISTS { (g)-[:ENCODES]->(:Protein {subtype: 'transcription_factor'}) }
        OR EXISTS { (g)-[:PRODUCES]->(:Transcript)-[:TRANSLATES_TO]->(:Protein {subtype: 'transcription_factor'}) }) AS is_tf
"""


def _node_kind(label: str) -> str:
    return {
        "Gene": "gene", "Transcript": "transcript", "Protein": "protein",
        "Variant": "variant", "Disease": "disease", "Metabolite": "metabolite",
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
    # The seed molecule's own vertical chain (its gene + transcripts + protein, and —
    # via the backbone pre-pass, ADR-0011 — the metabolites that protein catalyses) is
    # pinned: it is the subject of the query and must survive the max_nodes cap, even
    # when peripheral regulator-targets / variants have higher signal.
    structural_only: dict[str, bool] = {}
    _STRUCTURAL = set(_STRUCTURAL_RELS)

    def _record_node(row: dict) -> str:
        nb = row["nb_eid"]
        if nb not in node_payload:
            node_payload[nb] = {
                "label": row["nb_label"],
                "props": row["nb_props"],
                "key": row["nb_key"],
            }
        return nb

    def _record_edge(row: dict) -> None:
        # Dedup by relationship elementId.
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

    async with get_session() as session:
        seed_rows = await (await session.run(_RESOLVE_SEEDS, keys=seed_keys)).data()
        if not seed_rows:
            return {"nodes": [], "edges": []}
        seed_eids = [row["eid"] for row in seed_rows]
        for row in seed_rows:
            signal[row["eid"]] = 1.0
            structural_only[row["eid"]] = True
            node_payload[row["eid"]] = {
                "label": row["label"],
                "props": row["props"],
                "key": row["node_key"],
            }

        async def _expand(eids: list[str], rel_types: list[str]) -> list[dict]:
            return await (
                await session.run(_EXPAND_RING, eids=eids, rel_types=rel_types)
            ).data()

        # ---- Phase 1: backbone pre-pass (ADR-0011) ---------------------------
        # Walk ONLY structural edges from the seeds to full depth, pinning the whole
        # vertical chain, then take exactly one CATALYSES hop from each pinned protein
        # to its metabolites (pinned leaves). This guarantees the seed's own deep omics
        # layers are both DISCOVERED and PINNED regardless of the breadth fan-out — a
        # trim-time reservation cannot reserve what the discovery guard never reaches.
        bb_frontier = list(seed_eids)
        bb_expanded: set[str] = set()
        bb_depth = 0
        while bb_frontier and bb_depth < _BACKBONE_MAX_DEPTH:
            rows = await _expand(bb_frontier, _STRUCTURAL_RELS)
            bb_expanded.update(bb_frontier)
            nxt: list[str] = []
            for row in rows:
                nb = _record_node(row)
                structural_only[nb] = True
                new_sig = signal[row["from_eid"]] * decay * _conductance(
                    row["rel_type"], row["rel_props"]
                )
                if new_sig > signal.get(nb, 0.0):
                    signal[nb] = new_sig
                _record_edge(row)
                if nb not in bb_expanded:
                    nxt.append(nb)
            bb_frontier = [e for e in dict.fromkeys(nxt) if e not in bb_expanded]
            bb_depth += 1

        # One CATALYSES hop from every pinned protein -> its metabolites. Capped per
        # protein (deterministic by metabolite key) so a promiscuous enzyme can't pin
        # hundreds of nodes past max_nodes. Metabolites are LEAVES — never added to a
        # frontier, so cofactor hubs (ATP/NAD+) can't flood from here (ADR-0011).
        bb_proteins = [e for e, p in node_payload.items() if p["label"] == "Protein"]
        if bb_proteins:
            cat_rows = await _expand(bb_proteins, ["CATALYSES"])
            per_protein: dict[str, list[dict]] = {}
            for row in cat_rows:
                if row["nb_label"] == "Metabolite":
                    per_protein.setdefault(row["from_eid"], []).append(row)
            cap = settings.BACKBONE_MAX_METABOLITES_PER_PROTEIN
            for group in per_protein.values():
                group.sort(key=lambda r: r["nb_key"] or "")
                for row in group[:cap]:
                    nb = _record_node(row)
                    structural_only[nb] = True  # pinned leaf
                    new_sig = signal[row["from_eid"]] * decay * _conductance(
                        "CATALYSES", row["rel_props"]
                    )
                    if new_sig > signal.get(nb, 0.0):
                        signal[nb] = new_sig
                    _record_edge(row)

        # ---- Phase 2: breadth signal-decay BFS -------------------------------
        # Start from the seeds only (NOT the pinned backbone — a backbone metabolite
        # in the frontier would expand CATALYSES and pull in the whole metabolic net).
        expanded: set[str] = set()
        frontier = list(seed_eids)
        depth = 0
        while frontier and depth < _MAX_DEPTH and len(node_payload) < max_nodes:
            rows = await _expand(frontier, _TRAVERSAL_REL_TYPES)
            rows = _cap_dense_frontier(rows)
            expanded.update(frontier)
            next_frontier: list[str] = []
            for row in rows:
                cond = _conductance(row["rel_type"], row["rel_props"])
                new_sig = signal[row["from_eid"]] * decay * cond
                if new_sig < min_signal:
                    continue
                nb = _record_node(row)
                if new_sig > signal.get(nb, 0.0):
                    signal[nb] = new_sig
                # Propagate seed-chain pinning along structural edges only.
                if structural_only.get(row["from_eid"]) and row["rel_type"] in _STRUCTURAL:
                    structural_only[nb] = True
                _record_edge(row)
                # Leaf rule (ADR-0011): a metabolite expands only when it is a SEED
                # (seeds sit in the initial frontier). A metabolite discovered here is
                # peripheral -> never re-expanded, so hub cofactors can't flood.
                if nb not in expanded and row["nb_label"] != "Metabolite":
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
