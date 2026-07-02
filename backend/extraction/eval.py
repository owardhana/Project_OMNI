"""Precision/recall evaluation for the extractor (Feature 2 #16).

Two halves:
  - Pure/offline: build a fixture Gazetteer, compute P/R/F1 from triple sets. Runs in
    CI, no spend.
  - LLM-driven: `evaluate()` runs the real relation extractor over labelled cases and
    scores it against gold. This spends (one cheap call per co-mention pair), so it is
    only invoked by the gated eval test (RUN_EXTRACTION_EVAL=1) — never in normal CI,
    keeping the feature effectively off.

A predicted/gold key includes pmid + the canonical triple_key + polarity, so a wrong
polarity (missed negation) counts as both a false positive and a false negative.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from backend.agents.extraction_agent import _candidate_pairs, _resolve_entities
from backend.extraction.dictionary import Entry, Gazetteer
from backend.extraction.relation import extract_relation
from backend.extraction.stage import triple_key

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "extraction_eval.json"


def load_cases(path: Path | None = None) -> dict:
    with open(path or FIXTURE_PATH) as fh:
        return json.load(fh)


def build_fixture_gazetteer(entities: list[dict]) -> Gazetteer:
    return Gazetteer.from_entries(
        [Entry(e["surface"], e["id"], e["kind"], e["canonical"]) for e in entities]
    )


def _key(pmid: str, edge_type: str, sid: str, oid: str, polarity: str) -> str:
    ns = SimpleNamespace(edge_type=edge_type, subject_id=sid, object_id=oid)
    return f"{pmid}#{triple_key(ns)}#{polarity}"


def prf(gold: set[str], predicted: set[str]) -> dict:
    """Precision / recall / F1 from two key sets. Empty-vs-empty scores 1.0."""
    tp = len(gold & predicted)
    fp = len(predicted - gold)
    fn = len(gold - predicted)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": precision, "recall": recall, "f1": f1}


def gold_keys(data: dict) -> set[str]:
    keys: set[str] = set()
    for case in data["cases"]:
        for g in case["gold"]:
            keys.add(_key(case["pmid"], g["edge_type"], g["subject_id"],
                          g["object_id"], g["polarity"]))
    return keys


async def evaluate(data: dict | None = None) -> dict:
    """Run the real extractor over the fixtures and score it. SPENDS (LLM)."""
    data = data or load_cases()
    gaz = build_fixture_gazetteer(data["entities"])
    gold = gold_keys(data)
    predicted: set[str] = set()
    for case in data["cases"]:
        entities = _resolve_entities(gaz.match(case["text"]))
        if len(entities) < 2:
            continue
        for a, b in _candidate_pairs(entities):
            v = await extract_relation(case["text"], a, b, case["pmid"])
            if v and v.asserted:
                predicted.add(_key(case["pmid"], v.edge_type, v.subject_id,
                                   v.object_id, v.polarity))
    return {"metrics": prf(gold, predicted),
            "n_gold": len(gold), "n_predicted": len(predicted)}
