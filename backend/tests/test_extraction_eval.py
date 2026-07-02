"""Precision/recall gate for the extractor (Feature 2 #16).

Offline tests run in CI (no spend): they check the fixture Gazetteer links the
labelled entities in realistic sentences, that negatives produce the right pairing,
and that the P/R/F1 metric is correct. The LLM-driven eval is gated behind
RUN_EXTRACTION_EVAL=1 so it never spends in normal runs (feature stays off).
"""

import asyncio
import os

import pytest

from backend.agents.extraction_agent import _candidate_pairs, _resolve_entities
from backend.extraction.eval import (
    build_fixture_gazetteer,
    evaluate,
    gold_keys,
    load_cases,
    prf,
)

_DATA = load_cases()
_GAZ = build_fixture_gazetteer(_DATA["entities"])


def _case(pmid):
    return next(c for c in _DATA["cases"] if c["pmid"] == pmid)


def _linked_ids(text):
    return {e.node_id for e in _resolve_entities(_GAZ.match(text))}


def test_gold_entities_are_linked_in_realistic_text():
    # Every entity a gold triple references must be found by the closed-world matcher
    # in the case's sentence — this is the entity-linking recall the pipeline needs.
    for case in _DATA["cases"]:
        found = _linked_ids(case["text"])
        for g in case["gold"]:
            assert g["subject_id"] in found, f"{case['pmid']}: missing {g['subject_id']}"
            assert g["object_id"] in found, f"{case['pmid']}: missing {g['object_id']}"


def test_precision_trap_forms_a_pair_but_has_no_gold():
    # F0008: two proteins co-mentioned, but no relation asserted -> a pair IS formed
    # (so the extractor's restraint is tested) yet gold is empty.
    ents = _resolve_entities(_GAZ.match(_case("F0008")["text"]))
    assert len(_candidate_pairs(ents)) == 1
    assert _case("F0008")["gold"] == []


def test_single_entity_and_no_entity_cases_form_no_pairs():
    for pmid in ("F0009", "F0010"):
        ents = _resolve_entities(_GAZ.match(_case(pmid)["text"]))
        assert _candidate_pairs(ents) == []


def test_disease_linking_covered_by_fixtures():
    # Guard that the harder edge type (IMPLICATED_IN, disease linking) is represented,
    # incl. a negation case — recall here lags protein-protein (advisor).
    gold_edges = [g["edge_type"] for c in _DATA["cases"] for g in c["gold"]]
    polarities = [g["polarity"] for c in _DATA["cases"] for g in c["gold"]]
    assert "IMPLICATED_IN" in gold_edges and "INTERACTS_WITH" in gold_edges
    assert {"affirm", "negate", "hedge"} <= set(polarities)


def test_prf_metric():
    assert prf(set(), set())["precision"] == 1.0
    m = prf({"a", "b"}, {"a", "c"})   # tp=1, fp=1, fn=1
    assert m["tp"] == 1 and m["fp"] == 1 and m["fn"] == 1
    assert m["precision"] == 0.5 and m["recall"] == 0.5
    assert gold_keys(_DATA)  # non-empty gold set builds


@pytest.mark.skipif(
    not os.getenv("RUN_EXTRACTION_EVAL"),
    reason="LLM eval spends — set RUN_EXTRACTION_EVAL=1 to run",
)
def test_extractor_precision_meets_floor():
    result = asyncio.run(evaluate(_DATA))
    print("extraction eval:", result)
    assert result["metrics"]["precision"] >= 0.8, result
