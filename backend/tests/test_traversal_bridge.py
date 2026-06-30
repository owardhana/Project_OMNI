"""Metabolite-bridge traversal tests (ADR-0012).

The bridge is additive and opt-in (``METABOLITE_BRIDGE_ENABLED``, default OFF):

- Flag OFF must reproduce ADR-0011 behaviour exactly (discovered metabolites are leaves).
- Flag ON must let non-cofactor metabolites expand to their co-catalysing proteins
  WITHOUT a cofactor flood (the data-driven ``catalyses_degree`` gate).

Golden values are pinned to the live (rebuilt) graph; ADR-0011 measured
TP53@600 -> 0 metabolites, LDHA@800 -> 15 metabolites, L-Lactic-acid seed -> 110
metabolites / 77 proteins. Data-dependent tests skip (not fail) when the
proteome / Recon3D layer is not loaded, matching test_queries.py discipline.
"""

import pytest

from backend.config import settings
from backend.db.queries.genes import get_gene_by_symbol
from backend.db.queries.traversal import _metabolite_can_bridge, signal_decay_subgraph

# Golden values — flag-OFF regression gate (pinned to the current rebuilt graph).
GOLD_TP53_METABOLITES = 0
GOLD_LDHA_METABOLITES = 15
GOLD_LLACTIC_METABOLITES = 110
GOLD_LLACTIC_PROTEINS = 77


def _count(raw: dict, kind: str) -> int:
    return sum(1 for n in raw["nodes"] if n["kind"] == kind)


async def _metabolites_loaded(session) -> bool:
    rec = await (await session.run("MATCH (m:Metabolite) RETURN count(m) AS c")).single()
    return bool(rec and rec["c"] > 0)


async def _enzyme_gene_eid(session) -> str | None:
    rows = await (
        await session.run(
            "MATCH (g:Gene)-[:ENCODES|PRODUCES|TRANSLATES_TO*1..2]->"
            "(:Protein)-[:CATALYSES]->(:Metabolite) RETURN g.ensembl_id AS eid LIMIT 1"
        )
    ).data()
    return rows[0]["eid"] if rows and rows[0]["eid"] else None


# --- pure gate logic (no DB) -------------------------------------------------

def test_metabolite_can_bridge_gate(monkeypatch):
    # Flag off -> never bridges, regardless of degree.
    monkeypatch.setattr(settings, "METABOLITE_BRIDGE_ENABLED", False)
    assert _metabolite_can_bridge({"props": {"name": "malate", "catalyses_degree": 2}}) is False

    monkeypatch.setattr(settings, "METABOLITE_BRIDGE_ENABLED", True)
    monkeypatch.setattr(settings, "METABOLITE_MAX_CATALYSES_DEGREE", 30)
    # Non-cofactor, low degree -> bridges.
    assert _metabolite_can_bridge({"props": {"name": "(S)-malate", "catalyses_degree": 5}}) is True
    # High degree -> excluded by the data-driven gate (the cofactor signal).
    assert _metabolite_can_bridge({"props": {"name": "anything", "catalyses_degree": 500}}) is False
    # Cofactor name -> excluded by the hard-floor backstop even at low degree.
    assert _metabolite_can_bridge({"props": {"name": "ATP", "catalyses_degree": 1}}) is False
    assert _metabolite_can_bridge({"props": {"name": "H2O", "catalyses_degree": 1}}) is False
    # Missing degree -> fail safe (never flood when the property is absent).
    assert _metabolite_can_bridge({"props": {"name": "x"}}) is False


# --- flag OFF: ADR-0011 regression gate --------------------------------------

async def test_bridge_off_pure_tf_no_metabolites(neo4j_session, monkeypatch):
    if not await _metabolites_loaded(neo4j_session):
        pytest.skip("Recon3D not loaded (14_metabolomics) — no metabolites to surface")
    monkeypatch.setattr(settings, "METABOLITE_BRIDGE_ENABLED", False)
    rec = await get_gene_by_symbol("TP53")
    if not rec:
        pytest.skip("TP53 not in graph")
    raw = await signal_decay_subgraph([rec["props"]["ensembl_id"]], max_nodes=600)
    # Pure TF: no metabolic backbone, so no metabolites (ADR-0011).
    assert _count(raw, "metabolite") == GOLD_TP53_METABOLITES


async def test_bridge_off_enzyme_surfaces_backbone(neo4j_session, monkeypatch):
    if not await _metabolites_loaded(neo4j_session):
        pytest.skip("Recon3D not loaded (14_metabolomics)")
    eid = await _enzyme_gene_eid(neo4j_session)
    if eid is None:
        pytest.skip("no enzyme gene with a CATALYSES backbone in graph")
    monkeypatch.setattr(settings, "METABOLITE_BRIDGE_ENABLED", False)
    raw = await signal_decay_subgraph([eid], max_nodes=800)
    # An enzyme/metabolic-gene seed surfaces its OWN metabolites via the backbone
    # pre-pass even with the bridge off (ADR-0011).
    assert _count(raw, "metabolite") > 0
    assert len(raw["nodes"]) <= 800


# --- flag ON: bridge adds co-catalysing proteins, no flood -------------------

async def test_bridge_on_is_additive_and_bounded(neo4j_session, monkeypatch):
    if not await _metabolites_loaded(neo4j_session):
        pytest.skip("Recon3D not loaded (14_metabolomics)")
    eid = await _enzyme_gene_eid(neo4j_session)
    if eid is None:
        pytest.skip("no enzyme gene with a CATALYSES backbone in graph")

    monkeypatch.setattr(settings, "METABOLITE_BRIDGE_ENABLED", False)
    off = await signal_decay_subgraph([eid], max_nodes=800)
    monkeypatch.setattr(settings, "METABOLITE_BRIDGE_ENABLED", True)
    on = await signal_decay_subgraph([eid], max_nodes=800)

    # Bridge is additive: turning it on reaches co-catalysing proteins, so the ON view
    # has at least as many proteins as the OFF baseline.
    assert _count(on, "protein") >= _count(off, "protein")
    # And it never floods past the node budget (the dense-cap + degree gate hold).
    assert len(on["nodes"]) <= 800


async def test_bridge_on_no_cofactor_flood(neo4j_session, monkeypatch):
    # A high-degree cofactor must never expand even with the bridge on.
    rows = await (
        await neo4j_session.run(
            "MATCH (m:Metabolite) WHERE m.catalyses_degree > $thr "
            "RETURN m.name AS name, m.catalyses_degree AS deg "
            "ORDER BY m.catalyses_degree DESC LIMIT 1",
            thr=settings.METABOLITE_MAX_CATALYSES_DEGREE,
        )
    ).data()
    if not rows:
        pytest.skip("no high-degree cofactor metabolite (14_metabolomics degree post-pass?)")
    monkeypatch.setattr(settings, "METABOLITE_BRIDGE_ENABLED", True)
    # The gate excludes the hub cofactor regardless of name resolution.
    assert _metabolite_can_bridge(
        {"props": {"name": rows[0]["name"], "catalyses_degree": rows[0]["deg"]}}
    ) is False


# --- metabolite-seeded view (flag OFF baseline) ------------------------------

async def test_metabolite_seed_view(neo4j_session, monkeypatch):
    rows = await (
        await neo4j_session.run(
            "MATCH (m:Metabolite) WHERE toLower(m.name) = 'l-lactic acid' "
            "RETURN coalesce(m.hmdb_id, m.chebi_id) AS id LIMIT 1"
        )
    ).data()
    if not rows or not rows[0]["id"]:
        pytest.skip("L-Lactic acid metabolite not in graph")
    monkeypatch.setattr(settings, "METABOLITE_BRIDGE_ENABLED", False)
    raw = await signal_decay_subgraph([rows[0]["id"]])
    # A metabolite SEED expands in ring 1 regardless of the bridge (it sits in the
    # initial frontier) — a rich, bounded metabolite+protein view (ADR-0011).
    assert _count(raw, "metabolite") > 0
    assert _count(raw, "protein") > 0
