"""provenance_tier must flow raw Neo4j edge -> API model -> frontend (Feature 2 P2).

Offline: exercises the model builders directly. Combined with the promotion smoke
(the DB edge gets the tier), traversal's properties(r) (carries it), and the frontend
tsc (FGLink consumes it), this closes the API seam of the "proposed" rendering path.
"""

from backend.api.models import edge_detail_from_raw, edge_from_raw


def _raw(props):
    return {"source": "P1", "target": "P2", "rel_type": "INTERACTS_WITH", "props": props}


def test_graph_edge_carries_literature_tier():
    e = edge_from_raw(_raw({"provenance_tier": "literature", "source_db": "literature_extracted"}), [])
    assert e.provenance_tier == "literature"
    assert e.source_db == "literature_extracted"


def test_edge_detail_carries_literature_tier():
    d = edge_detail_from_raw(_raw({"provenance_tier": "literature", "pmids": ["1", "2"]}), [])
    assert d.provenance_tier == "literature"
    assert d.pmids == ["1", "2"]


def test_canonical_edge_has_null_tier():
    # Absent provenance_tier = canonical (never written) — must serialize as None.
    e = edge_from_raw(_raw({"source_db": "STRING", "combined_score": 0.9}), [])
    assert e.provenance_tier is None
