"""Cypher correctness tests against the live graph."""

from backend.db.queries.genes import (
    get_gene_by_symbol,
    get_gene_neighborhood,
)
from backend.db.queries.graph import search_genes


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
