"""Staging (Feature 2, stage 4): turn relation verdicts into CandidateEdge /
CandidateEvidence — never trusted topology (ADR-0013).

Two dedup rules on the way in:
  1. Symmetric normalization — INTERACTS_WITH A-B == B-A, so the triple_key sorts
     the endpoints; directed edges keep order.
  2. Enrichment-not-candidate — if a TRUSTED edge of that type already exists between
     the two real nodes, this paper is enrichment: append the PMID to that edge
     (additive; never overwrite canonical source_db / provenance_tier) and mint no
     candidate.

CandidateEdge stores endpoint ids as STRING PROPERTIES, not relationships to real
nodes, so it is invisible to traversal/search/counts. Evidence is one node per PMID;
confidence is recomputed from independent-PMID agreement, not model self-report.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from backend.extraction.relation import RelationVerdict

logger = logging.getLogger(__name__)

# kind -> (label, id property). Allowlist: interpolated into Cypher, never user input.
_KIND_MAP = {
    "gene": ("Gene", "ensembl_id"),
    "protein": ("Protein", "uniprot_id"),
    "disease": ("Disease", "ontology_id"),
}
_SYMMETRIC = {"INTERACTS_WITH"}
# Edge types the extractor may stage (allowlist for Cypher interpolation).
_ALLOWED_EDGES = {"INTERACTS_WITH", "IMPLICATED_IN"}


class _EndpointsView:
    """Adapts a CandidateEdge property dict to the attrs `trusted_edge_exists` reads
    off a verdict (`rel_type` -> `edge_type`). Shared by the ValidationAgent (promote)
    and the review surface (would_be_action preview) so both go through one code path."""

    __slots__ = ("edge_type", "subject_id", "subject_kind", "object_id", "object_kind")

    def __init__(self, ce: dict):
        self.edge_type = ce.get("rel_type")
        self.subject_id = ce.get("subject_id")
        self.subject_kind = ce.get("subject_kind")
        self.object_id = ce.get("object_id")
        self.object_kind = ce.get("object_kind")


def endpoints_view(ce: dict) -> _EndpointsView:
    return _EndpointsView(ce)


def triple_key(v: RelationVerdict) -> str:
    """Canonical dedup key. Symmetric edges sort endpoints so A-B == B-A."""
    ids = [v.subject_id, v.object_id]
    if v.edge_type in _SYMMETRIC:
        ids = sorted(ids)
    return f"{v.edge_type}:{ids[0]}|{ids[1]}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def trusted_edge_exists(session, v: RelationVerdict) -> bool:
    """Does a real (trusted) edge of this type already connect the two nodes?"""
    if v.subject_kind not in _KIND_MAP or v.object_kind not in _KIND_MAP:
        return False
    s_label, s_idf = _KIND_MAP[v.subject_kind]
    o_label, o_idf = _KIND_MAP[v.object_kind]
    arrow = "-" if v.edge_type in _SYMMETRIC else "->"
    query = (
        f"MATCH (s:{s_label} {{{s_idf}: $sid}})"
        f"-[r:{v.edge_type}]{arrow}(o:{o_label} {{{o_idf}: $oid}}) "
        f"RETURN count(r) > 0 AS present"
    )
    rows = await (await session.run(query, sid=v.subject_id, oid=v.object_id)).data()
    return bool(rows and rows[0]["present"])


async def _enrich_existing_edge(session, v: RelationVerdict) -> None:
    """Append the PMID to the existing trusted edge (additive; canonical provenance
    untouched)."""
    s_label, s_idf = _KIND_MAP[v.subject_kind]
    o_label, o_idf = _KIND_MAP[v.object_kind]
    arrow = "-" if v.edge_type in _SYMMETRIC else "->"
    query = (
        f"MATCH (s:{s_label} {{{s_idf}: $sid}})"
        f"-[r:{v.edge_type}]{arrow}(o:{o_label} {{{o_idf}: $oid}}) "
        "SET r.pmids = coalesce(r.pmids, []) + "
        "    [x IN [$pmid] WHERE NOT x IN coalesce(r.pmids, [])], "
        "    r.lit_enriched = true"
    )
    await session.run(query, sid=v.subject_id, oid=v.object_id, pmid=v.pmid)


_UPSERT_CANDIDATE = """
MERGE (ce:CandidateEdge {triple_key: $tk})
ON CREATE SET ce.rel_type = $rel_type,
              ce.subject_id = $sid, ce.subject_kind = $skind,
              ce.object_id = $oid, ce.object_kind = $okind,
              ce.status = 'pending',
              ce.provenance_tier = 'literature',
              ce.first_seen = $now
SET ce.last_seen = $now,
    ce.source_agent = $source_agent,
    ce.agent_version = $agent_version
MERGE (ev:CandidateEvidence {triple_key: $tk, pmid: $pmid})
SET ev.polarity = $polarity,
    ev.model_conf = $confidence,
    ev.sentence_span = $evidence_span,
    ev.model = $model,
    ev.extracted_at = $now
MERGE (ev)-[:SUPPORTS]->(ce)
WITH ce
MATCH (ce)<-[:SUPPORTS]-(e:CandidateEvidence)
WITH ce,
     sum(CASE WHEN e.polarity = 'affirm' THEN 1 ELSE 0 END) AS n_affirm,
     sum(CASE WHEN e.polarity = 'negate' THEN 1 ELSE 0 END) AS n_negate
SET ce.n_affirm = n_affirm,
    ce.n_negate = n_negate,
    ce.confidence = CASE WHEN n_affirm = 0 THEN 0.0
                    ELSE (1.0 - 1.0 / (2 ^ n_affirm)) * n_affirm / (n_affirm + n_negate)
                    END
RETURN ce.confidence AS confidence, ce.n_affirm AS n_affirm, ce.n_negate AS n_negate
"""


async def stage_verdict(session, v: RelationVerdict, provenance: dict) -> dict:
    """Route one verdict. Returns {status, ...}.

    - asserted=False -> skipped (sentence doesn't state the relation).
    - trusted edge present -> enriched (PMID appended to the real edge).
    - else -> candidate (CandidateEdge upserted, confidence recomputed).
    """
    if not v.asserted or v.edge_type not in _ALLOWED_EDGES:
        return {"status": "skipped"}

    if await trusted_edge_exists(session, v):
        await _enrich_existing_edge(session, v)
        return {"status": "enriched", "edge_type": v.edge_type}

    params = {
        "tk": triple_key(v),
        "rel_type": v.edge_type,
        "sid": v.subject_id, "skind": v.subject_kind,
        "oid": v.object_id, "okind": v.object_kind,
        "pmid": v.pmid, "polarity": v.polarity,
        "confidence": v.confidence, "evidence_span": v.evidence_span,
        "model": v.model,  # actual model that produced this verdict (may differ per path)
        "now": _now(),
        "source_agent": provenance.get("source_agent"),
        "agent_version": provenance.get("agent_version"),
    }
    rows = await (await session.run(_UPSERT_CANDIDATE, **params)).data()
    result = rows[0] if rows else {}
    return {"status": "candidate", "triple_key": params["tk"], **result}
