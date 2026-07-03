"""Literature-extraction dictionary/matcher tests (Feature 2, P1 foundation).

Pure-offline: a Gazetteer is built from fixture Entry lists, no Neo4j / network.
These lock the closed-world entity-linking behaviour the rest of the pipeline
depends on (alias resolution, longest-match, casing/ambiguity gates).
"""

from backend.agents.extraction_agent import _candidate_pairs, _resolve_entities
from backend.extraction.dictionary import Entry, Gazetteer
from backend.extraction.ingest import split_sentences
from backend.extraction.relation import (
    RelationVerdict,
    _orient,
    _parse,
    edge_type_for,
)
from backend.extraction.stage import triple_key

_ENTRIES = [
    Entry("TP53", "ENSG00000141510", "gene", "TP53"),
    Entry("p53", "ENSG00000141510", "gene", "TP53"),        # alias -> same ENSG
    Entry("EGFR", "ENSG00000146648", "gene", "EGFR"),
    Entry("HLA-A", "ENSG00000206503", "gene", "HLA-A"),
    Entry("type 2 diabetes mellitus", "EFO_0001360", "disease", "type 2 diabetes mellitus"),
    Entry("diabetes mellitus", "EFO_0000400", "disease", "diabetes mellitus"),
    Entry("MET", "ENSG00000105976", "gene", "MET"),          # also an English word
    Entry("cancer", "EFO_0000311", "disease", "cancer"),      # generic — floods prose
    Entry("breast cancer", "EFO_0000305", "disease", "breast cancer"),
]


def _gaz() -> Gazetteer:
    return Gazetteer.from_entries(_ENTRIES)


def _resolved(matches):
    return {(m.surface, m.candidates[0].node_id) for m in matches}


def test_alias_resolves_to_canonical_id():
    hits = _gaz().match("the p53 pathway")
    assert ("p53", "ENSG00000141510") in _resolved(hits)


def test_longest_match_wins():
    # "type 2 diabetes mellitus" must win over the shorter "diabetes mellitus".
    hits = _gaz().match("patients with type 2 diabetes mellitus were enrolled")
    surfaces = {m.surface for m in hits}
    assert "type 2 diabetes mellitus" in surfaces
    assert "diabetes mellitus" not in surfaces


def test_trailing_punctuation_stripped():
    # sentence-final period must not break the multi-word disease match.
    hits = _gaz().match("associated with type 2 diabetes mellitus.")
    assert "type 2 diabetes mellitus" in {m.surface for m in hits}


def test_hyphenated_symbol_matches_whole():
    hits = _gaz().match("HLA-A expression was elevated")
    assert ("HLA-A", "ENSG00000206503") in _resolved(hits)


def test_ambiguous_symbol_requires_exact_case():
    # lowercase 'met' (English word) must NOT match the gene MET...
    assert not _gaz().match("the met receptor tyrosine kinase")
    # ...but uppercase MET must.
    assert "MET" in {m.surface for m in _gaz().match("MET amplification")}


def test_short_symbol_is_case_sensitive():
    # 'egfr' lowercase should not match the 4-char symbol EGFR.
    assert "EGFR" not in {m.surface for m in _gaz().match("egfr was measured")}
    assert "EGFR" in {m.surface for m in _gaz().match("EGFR was measured")}


def test_bare_number_surface_gated():
    # Junk single-token Disease.name values like "2" must never match (they'd hit
    # every number in prose). Found in the #17 live disease-surface audit.
    g = Gazetteer.from_entries([
        Entry("2", "EFO_JUNK", "disease", "2"),
        Entry("TP53", "ENSG1", "gene", "TP53"),
    ])
    surfaces = {m.surface for m in g.match("TP53 increased 2 fold in the assay")}
    assert "2" not in surfaces
    assert "TP53" in surfaces


def test_generic_disease_word_gated_standalone():
    # bare "cancer" must NOT match (floods prose)...
    assert "cancer" not in {m.surface for m in _gaz().match("the risk of cancer rises")}
    # ...but the specific "breast cancer" phrase must (longest-match).
    assert "breast cancer" in {m.surface for m in _gaz().match("associated with breast cancer")}


def test_co_mention_gate_precondition():
    # A sentence with >=2 distinct linked entities is the minimal candidate signal.
    hits = _gaz().match("p53 regulates EGFR")
    ids = {m.candidates[0].node_id for m in hits}
    assert len(ids) >= 2


# --- pipeline helpers (ingest / relation / stage) — pure, offline ------------


def test_split_sentences_and_abbreviation():
    text = "TP53 binds MDM2. It is a regulator, e.g. in stress. EGFR was elevated."
    sents = split_sentences(text)
    assert sents[0] == "TP53 binds MDM2."
    # "e.g." must not split its sentence into two.
    assert any("e.g. in stress" in s for s in sents)
    assert sents[-1] == "EGFR was elevated."


def test_edge_type_for_kinds():
    assert edge_type_for("protein", "protein") == "INTERACTS_WITH"
    assert edge_type_for("gene", "disease") == "IMPLICATED_IN"
    assert edge_type_for("disease", "gene") == "IMPLICATED_IN"
    assert edge_type_for("gene", "gene") is None          # out of MVP vocab
    assert edge_type_for("protein", "disease") is None


def test_orient_implicated_in_pins_gene_to_disease():
    gene = Entry("TP53", "ENSG1", "gene", "TP53")
    dis = Entry("cancer", "EFO1", "disease", "breast cancer")
    s, o = _orient("IMPLICATED_IN", dis, gene)  # pass reversed
    assert s.kind == "gene" and o.kind == "disease"


def test_parse_verdict_ok_and_bad():
    ok = _parse('{"asserted": true, "polarity": "affirm", "confidence": 1.4, "evidence_span": "binds"}')
    assert ok["asserted"] and ok["polarity"] == "affirm"
    assert ok["confidence"] == 1.0                         # clamped to [0,1]
    neg = _parse('noise {"asserted": true, "polarity": "negate", "confidence": 0.8} tail')
    assert neg["polarity"] == "negate"
    assert _parse("not json") is None
    assert _parse('{"asserted": true, "polarity": "maybe"}') is None  # bad polarity


def _verdict(edge_type, sid, oid, skind, okind):
    return RelationVerdict(edge_type, sid, skind, oid, okind, True, "affirm", 0.9, "x", "1", "s")


def test_triple_key_symmetric_and_directed():
    # INTERACTS_WITH is symmetric -> A-B == B-A.
    ab = triple_key(_verdict("INTERACTS_WITH", "P1", "P2", "protein", "protein"))
    ba = triple_key(_verdict("INTERACTS_WITH", "P2", "P1", "protein", "protein"))
    assert ab == ba
    # IMPLICATED_IN is directed -> order preserved.
    assert triple_key(_verdict("IMPLICATED_IN", "G1", "D1", "gene", "disease")) \
        != triple_key(_verdict("IMPLICATED_IN", "D1", "G1", "disease", "gene"))


# --- orchestration pairing (extraction_agent) — pure, offline -----------------


class _M:  # minimal stand-in for a dictionary.Match (only .candidates is used)
    def __init__(self, *entries):
        self.candidates = list(entries)


def test_resolve_entities_dedupes_by_node_id():
    tp53_gene = Entry("TP53", "ENSG1", "gene", "TP53")
    tp53_again = Entry("p53", "ENSG1", "gene", "TP53")  # same node_id
    egfr = Entry("EGFR", "ENSG2", "gene", "EGFR")
    ents = _resolve_entities([_M(tp53_gene), _M(tp53_again), _M(egfr)])
    assert {e.node_id for e in ents} == {"ENSG1", "ENSG2"}


def test_gene_protein_duality_enables_interacts_pair():
    # Mirrors build_gazetteer_from_graph: a symbol resolves to BOTH a gene and a
    # protein node. _resolve_entities must keep both candidates, else INTERACTS_WITH
    # (protein-protein) can never form and the extractor is IMPLICATED_IN-only.
    mdm2 = _M(Entry("MDM2", "ENSG_M", "gene", "MDM2"), Entry("MDM2", "P_MDM2", "protein", "MDM2"))
    tp53 = _M(Entry("TP53", "ENSG_T", "gene", "TP53"), Entry("TP53", "P_TP53", "protein", "TP53"))
    ents = _resolve_entities([mdm2, tp53])
    assert len(ents) == 4  # 2 genes + 2 proteins, all distinct node_ids
    kinds = {frozenset({a.kind, b.kind}) for a, b in _candidate_pairs(ents)}
    assert frozenset({"protein"}) in kinds        # protein-protein INTERACTS_WITH fires
    assert frozenset({"gene"}) not in kinds        # gene-gene is out of vocab


def test_candidate_pairs_filters_selfpairs_and_out_of_vocab():
    prot_a = Entry("A", "P1", "protein", "A")
    prot_b = Entry("B", "P2", "protein", "B")
    gene = Entry("G", "ENSG1", "gene", "G")
    disease = Entry("D", "EFO1", "disease", "D")
    # protein-protein -> 1 pair; gene-disease -> 1 pair; gene-protein/protein-disease
    # /gene-gene -> none (out of MVP vocab). No self-pairs.
    pairs = _candidate_pairs([prot_a, prot_b, gene, disease])
    kinds = {frozenset({a.kind, b.kind}) for a, b in pairs}
    assert frozenset({"protein"}) in kinds          # INTERACTS_WITH
    assert frozenset({"gene", "disease"}) in kinds   # IMPLICATED_IN
    assert all(a.node_id != b.node_id for a, b in pairs)
    # gene+protein is not a valid pair -> excluded
    assert frozenset({"gene", "protein"}) not in kinds
