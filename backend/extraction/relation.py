"""Relation extraction (Feature 2, stage 3): one cheap LLM verdict per
(sentence, entity-pair).

Endpoint kinds pin the edge type and its direction, so the model only judges whether
the sentence asserts the relation and its polarity (affirm/negate/hedge). Output is
constrained JSON, parsed defensively. negate/hedge verdicts are kept (stage.py floors
them) so contradictions are recorded, not silently dropped.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from backend.config import settings
from backend.extraction.dictionary import Entry
from backend.extraction.ratelimit import AsyncRateLimiter
from backend.llm.client import complete
from backend.llm.prompts.extraction import (
    EXTRACTION_SYSTEM_PROMPT,
    build_extraction_prompt,
)

logger = logging.getLogger(__name__)

# Module-level singleton so every concurrent verdict task shares one per-minute budget
# (the free tier's cap is account-wide, not per-connection). Sized from config.
_rate_limiter = AsyncRateLimiter(settings.EXTRACTION_LLM_RATE_PER_MIN)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_POLARITIES = {"affirm", "negate", "hedge"}


@dataclass
class RelationVerdict:
    edge_type: str
    subject_id: str
    subject_kind: str
    object_id: str
    object_kind: str
    asserted: bool
    polarity: str
    confidence: float
    evidence_span: str
    pmid: str
    sentence: str
    model: str  # the model that produced this verdict (recorded on CandidateEvidence)


def edge_type_for(kind_a: str, kind_b: str) -> str | None:
    """MVP edge type for a pair of entity kinds, or None if out of scope.
    Direction is pinned by kinds (handled in `_orient`)."""
    kinds = {kind_a, kind_b}
    if kinds == {"protein"}:          # protein-protein
        return "INTERACTS_WITH"
    if kinds == {"gene", "disease"}:
        return "IMPLICATED_IN"
    return None


def _orient(edge_type: str, a: Entry, b: Entry) -> tuple[Entry, Entry]:
    """Order (subject, object) by the edge's fixed direction. INTERACTS_WITH is
    symmetric (canonicalized later in stage); IMPLICATED_IN is gene -> disease."""
    if edge_type == "IMPLICATED_IN":
        return (a, b) if a.kind == "gene" else (b, a)
    return (a, b)


def _parse(raw: str) -> dict | None:
    m = _JSON_RE.search(raw)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    polarity = str(obj.get("polarity", "")).lower()
    if polarity not in _POLARITIES:
        return None
    try:
        confidence = float(obj.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "asserted": bool(obj.get("asserted", False)),
        "polarity": polarity,
        "confidence": max(0.0, min(1.0, confidence)),
        "evidence_span": str(obj.get("evidence_span", ""))[:500],
    }


def _completion_kwargs() -> dict:
    """Extra OpenRouter args for the verdict call:
    - a bounded ``timeout`` so a slow/queued free model can't block a verdict for the
      SDK's ~10-min default (a timeout raises → counted as an llm_error → chunk retried);
    - ``reasoning.exclude`` so a reasoning model's chain-of-thought preamble doesn't reach
      the JSON parser (no-op on non-reasoning models)."""
    kwargs: dict = {"temperature": 0, "timeout": settings.EXTRACTION_LLM_TIMEOUT_S}
    if settings.EXTRACTION_EXCLUDE_REASONING:
        kwargs["extra_body"] = {"reasoning": {"exclude": True}}
    return kwargs


async def extract_relation(
    sentence: str, a: Entry, b: Entry, pmid: str, model: str | None = None
) -> RelationVerdict | None:
    """Return a verdict for the pair in this sentence, or None if the pair's kinds
    are out of the MVP edge vocabulary or the model output was unparseable.

    ``model`` defaults to ``settings.EXTRACTION_MODEL`` and is recorded on the verdict
    (→ ``CandidateEvidence.model``) so backfill vs. nightly provenance is truthful even
    if the two paths ever run different models."""
    edge_type = edge_type_for(a.kind, b.kind)
    if edge_type is None:
        return None
    model = model or settings.EXTRACTION_MODEL
    subj, obj = _orient(edge_type, a, b)
    await _rate_limiter.acquire()  # pace to the free-tier per-minute cap (incl. retries)
    raw = await complete(
        model,
        [
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": build_extraction_prompt(sentence, subj, obj, edge_type)},
        ],
        **_completion_kwargs(),
    )
    parsed = _parse(raw)
    if parsed is None:
        logger.warning("relation: unparseable verdict for %s/%s pmid=%s",
                       subj.canonical, obj.canonical, pmid)
        return None
    return RelationVerdict(
        edge_type=edge_type,
        subject_id=subj.node_id, subject_kind=subj.kind,
        object_id=obj.node_id, object_kind=obj.kind,
        pmid=pmid, sentence=sentence, model=model, **parsed,
    )
