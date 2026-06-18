"""Cypher correctness tests against the live graph."""

from backend.api import models
from backend.api.routes.graph import (
    _component_count,
    _merge_raw,
    _node_key,
    graph_multi,
    graph_path,
)
from backend.db.queries.diseases import get_disease_by_ontology_id
from backend.db.queries.genes import (
    get_gene_by_symbol,
    get_gene_neighborhood,
)
from backend.db.queries.graph import search_entities, search_genes
from backend.db.queries.traversal import signal_decay_subgraph

# Phase-2 entities present in the live graph (GWAS/ClinVar/STRING-derived). EFO
# ontology ids are stable across rebuilds; TP53/EGFR are canonical hub proteins
# for INTERACTS_WITH.
_DISEASE_OID = "EFO_0004340"  # high-degree GWAS trait


async def test_gene_lookup():
    record = await get_gene_by_symbol("TP53")
    assert record is not None
    props = record["props"]
    assert props["ensembl_id"].startswith("ENSG")
    assert props["hgnc_symbol"] == "TP53"
    assert record["is_tf"] is True  # TP53 encodes a TF protein (ADR-0004)


async def test_neighborhood():
    record = await get_gene_by_symbol("TP53")
    ensembl_id = record["props"]["ensembl_id"]
    graph = await get_gene_neighborhood(ensembl_id, tissue="all")
    assert len(graph["nodes"]) > 0
    assert len(graph["edges"]) > 0
    # Every REGULATES edge must carry an A/B confidence tier.
    for edge in graph["edges"]:
        if edge["rel_type"] == "REGULATES":
            assert edge["props"]["confidence_tier"] in ("A", "B")


async def test_regulates_is_protein_sourced():
    # ADR-0004: a TF is a Protein; the seed's incoming regulators are proteins,
    # and the seed's own protein appears (double representation, seed-chain pin).
    record = await get_gene_by_symbol("TP53")
    graph = await get_gene_neighborhood(record["props"]["ensembl_id"])
    kinds = {n["kind"] for n in graph["nodes"]}
    assert "protein" in kinds
    tp53_protein = [
        n for n in graph["nodes"]
        if n["kind"] == "protein" and n["props"].get("hgnc_symbol") == "TP53"
    ]
    assert tp53_protein, "TP53's own protein must be pinned in its subgraph"


async def test_tissue_does_not_filter():
    # ADR-0006: tissue is a visual/opacity channel, not a presence gate. The
    # subgraph must be identical regardless of tissue; PRODUCES edges still carry
    # tw_* so the frontend can dim by expression.
    record = await get_gene_by_symbol("ALB")  # albumin — canonical liver gene
    ensembl_id = record["props"]["ensembl_id"]
    g_all = await get_gene_neighborhood(ensembl_id, tissue="all")
    g_liver = await get_gene_neighborhood(ensembl_id, tissue="liver")
    assert len(g_all["nodes"]) == len(g_liver["nodes"])
    assert len(g_all["edges"]) == len(g_liver["edges"])
    produces = [e for e in g_all["edges"] if e["rel_type"] == "PRODUCES"]
    assert produces, "ALB should have transcripts"
    for edge in produces:
        assert "tw_liver" in edge["props"]  # weight present for opacity, not filtered


async def test_search():
    results = await search_genes("TP53", limit=10)
    assert results
    assert results[0]["hgnc_symbol"] == "TP53"


# --- Phase 2: disease / variant nodes ---------------------------------------

async def test_disease_lookup():
    # ADR-0007: Disease is a first-class node, addressable by ontology id.
    props = await get_disease_by_ontology_id(_DISEASE_OID)
    assert props is not None
    assert props["ontology_id"] == _DISEASE_OID
    assert props.get("name")  # GWAS traits carry a human-readable name


async def test_variant_lookup(neo4j_session):
    # GWAS-minted Variant nodes carry a dbSNP rsid and a chromosome.
    rows = await (
        await neo4j_session.run(
            "MATCH (v:Variant) WHERE v.rsid STARTS WITH 'rs' "
            "RETURN v.rsid AS rsid, v.chromosome AS chrom LIMIT 1"
        )
    ).data()
    assert rows, "graph should contain rs-prefixed GWAS variants"
    assert rows[0]["rsid"].startswith("rs")


# --- Phase 2: new edge types -------------------------------------------------

async def test_interacts_with_edges(neo4j_session):
    # STRING PPIs: TP53 is a hub protein; every edge clears STRING_MIN_CONFIDENCE
    # (combined_score >= 900 on the 0-1000 scale).
    rows = await (
        await neo4j_session.run(
            "MATCH (:Protein {hgnc_symbol:'TP53'})-[r:INTERACTS_WITH]-(:Protein) "
            "RETURN r.combined_score AS s LIMIT 50"
        )
    ).data()
    assert rows, "TP53 must have STRING interactors"
    for row in rows:
        if row["s"] is not None:
            # combined_score is stored normalised to 0-1; the import keeps only
            # edges at/above STRING_MIN_CONFIDENCE (0.9).
            assert row["s"] >= 0.9


async def test_associated_with_edges(neo4j_session):
    # GWAS associations: every kept edge clears GWAS_MIN_SIGNIFICANCE (p <= 5e-8).
    rows = await (
        await neo4j_session.run(
            "MATCH (:Variant)-[r:ASSOCIATED_WITH]->(:Disease) "
            "RETURN r.p_value AS p LIMIT 50"
        )
    ).data()
    assert rows, "graph should contain GWAS variant-disease associations"
    for row in rows:
        if row["p"] is not None:
            assert row["p"] <= 5e-8


async def test_disease_traversal():
    # A disease is a valid signal-decay seed (ADR-0007); its subgraph reaches the
    # associated variants and their genes.
    raw = await signal_decay_subgraph([_DISEASE_OID])
    assert raw["nodes"], "disease seed must expand to a subgraph"
    kinds = {n["kind"] for n in raw["nodes"]}
    assert "disease" in kinds
    assert "variant" in kinds  # disease -> ASSOCIATED_WITH -> variant


# --- Phase 2: multi-seed merge + shortest path -------------------------------

async def test_multi_seed_graph():
    req = models.MultiGraphRequest(
        seed_ids=["TP53", "EGFR"], seed_types=["gene", "gene"]
    )
    resp = await graph_multi(req)
    assert len(resp.nodes) > 0
    assert resp.metadata is not None
    # TP53 and EGFR are densely linked, so their merged subgraph is one component.
    assert resp.metadata["component_count"] == 1
    assert resp.metadata["connected"] is True
    assert not resp.warnings  # connected -> no disconnected-cluster warning
    assert resp.metadata["seeds"] == ["TP53", "EGFR"]


async def test_multi_seed_disconnected():
    # The dense live graph rarely yields disconnected real seeds, so exercise the
    # detection logic directly on two non-overlapping subgraphs.
    raw_a = {
        "nodes": [{"kind": "gene", "props": {"ensembl_id": "ENSG_A1"}},
                  {"kind": "gene", "props": {"ensembl_id": "ENSG_A2"}}],
        "edges": [{"source": "ENSG_A1", "rel_type": "REGULATES", "target": "ENSG_A2",
                   "props": {}}],
    }
    raw_b = {
        "nodes": [{"kind": "gene", "props": {"ensembl_id": "ENSG_B1"}},
                  {"kind": "gene", "props": {"ensembl_id": "ENSG_B2"}}],
        "edges": [{"source": "ENSG_B1", "rel_type": "REGULATES", "target": "ENSG_B2",
                   "props": {}}],
    }
    merged = _merge_raw([raw_a, raw_b])
    keys = {k for n in merged["nodes"] if (k := _node_key(n)) is not None}
    assert _component_count(keys, merged["edges"]) == 2


async def test_shortest_path_found():
    # TP53 and EGFR are linked within a few hops through the PPI/regulatory layer.
    resp = await graph_path(
        from_id="TP53", type_a="gene", to_id="EGFR", type_b="gene", max_hops=6
    )
    assert resp.path_found is True
    assert resp.hop_count is not None and 1 <= resp.hop_count <= 6
    assert resp.path_quality in ("direct", "moderate", "weak")
    assert len(resp.nodes) >= 2


async def test_shortest_path_not_found():
    # A non-existent disease id resolves verbatim but its anchor MATCH is empty,
    # so shortestPath yields no path -> path_found False, no crash.
    resp = await graph_path(
        from_id="TP53", type_a="gene",
        to_id="EFO_0000000_nonexistent", type_b="disease", max_hops=6,
    )
    assert resp.path_found is False
    assert resp.path_quality == "no_path"


# --- Phase 2: filtered entity browser search --------------------------------

async def test_entities_search():
    # The Gene tab must return ONLY genes (regression guard for the browser
    # type-filter): searching TP53 restricted to genes yields gene rows only.
    rows = await search_entities("TP53", ["gene"])
    assert rows
    assert all(r["node_type"] == "gene" for r in rows)
    # Entities expose a coalesced display_name (hgnc_symbol for genes).
    assert any(r.get("display_name") == "TP53" for r in rows)
