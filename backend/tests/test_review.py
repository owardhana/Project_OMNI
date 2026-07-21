"""Admin review-surface shaping tests (Feature 2 P3, ADR-0014) — pure/offline.

These lock the payload-shaping the dashboard depends on (endpoint id->name resolution,
contradicting-evidence-first ordering, the rel_type->edge_type shim that drives the
would_be_action preview). The revert round-trips are Cypher and live only — see the
smoke script; a passing offline suite here does NOT prove revert.
"""

from backend.extraction.review import _endpoint, evidence_sort_key
from backend.extraction.stage import endpoints_view, triple_key
from backend.extraction.relation import RelationVerdict


def test_endpoint_resolves_name_and_falls_back_to_id():
    names = {("gene", "ENSG1"): "TP53"}
    assert _endpoint("gene", "ENSG1", names) == {"id": "ENSG1", "kind": "gene", "name": "TP53"}
    # unresolved id (e.g. a stale candidate whose node was removed) falls back to the id.
    assert _endpoint("disease", "EFO_X", names)["name"] == "EFO_X"


def test_evidence_negate_surfaces_first_then_affirm_then_hedge():
    ev = [
        {"pmid": "1", "polarity": "affirm", "extracted_at": "2026-01-01"},
        {"pmid": "2", "polarity": "hedge", "extracted_at": "2026-01-01"},
        {"pmid": "3", "polarity": "negate", "extracted_at": "2026-01-02"},
        {"pmid": "4", "polarity": "negate", "extracted_at": "2026-01-01"},
    ]
    order = [e["pmid"] for e in sorted(ev, key=evidence_sort_key)]
    # both negates first (contradicting evidence shown, not just counted), older first,
    # then affirm, then hedge.
    assert order == ["4", "3", "1", "2"]


def test_endpoints_view_maps_rel_type_to_edge_type():
    # the shim feeds trusted_edge_exists (would_be_action preview); it must expose the
    # candidate's rel_type as .edge_type and carry the endpoint kinds/ids through.
    ce = {"rel_type": "INTERACTS_WITH", "subject_id": "P1", "subject_kind": "protein",
          "object_id": "P2", "object_kind": "protein"}
    ep = endpoints_view(ce)
    assert ep.edge_type == "INTERACTS_WITH"
    assert (ep.subject_id, ep.subject_kind) == ("P1", "protein")
    assert (ep.object_id, ep.object_kind) == ("P2", "protein")


def test_enrich_revert_delta_preserves_overlap_pmids():
    # Pure mirror of the ENRICH promote/revert Cypher (ADR-0014 §2): the delta recorded
    # at promote time is exactly the pmids NOT already present; revert removes only that
    # set, so a pmid the canonical edge already had (the overlap) must survive.
    existing = ["A", "B"]
    affirming = ["B", "C"]                       # B overlaps canonical
    delta = [x for x in affirming if x not in existing]
    assert delta == ["C"]                          # only the genuinely-new pmid
    after_promote = existing + delta
    assert after_promote == ["A", "B", "C"]
    after_revert = [x for x in after_promote if x not in delta]
    assert after_revert == ["A", "B"]              # overlap B preserved, not stripped


def _v(edge_type, sid, oid, skind, okind):
    return RelationVerdict(edge_type, sid, skind, oid, okind, True, "affirm", 0.9, "x", "1", "s", "test-model")


def test_mint_edge_triple_key_matches_candidate_key():
    # the minted edge carries r.triple_key = the candidate's key, so it can be traced
    # back (provenance + future deep-link). Symmetric keys are endpoint-order-independent.
    assert triple_key(_v("INTERACTS_WITH", "P1", "P2", "protein", "protein")) \
        == triple_key(_v("INTERACTS_WITH", "P2", "P1", "protein", "protein"))
