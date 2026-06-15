"""Pydantic request/response models + builders from raw Neo4j data.

The DB layer (backend/db) returns plain dicts (it may not import the API layer).
These builders convert that raw shape into typed API models — including
reconstructing the ``tissue_weights`` dict from the flat ``tw_<tissue>`` edge
properties stored in Neo4j (see docs/adr/0001-tissue-weights-flat-properties.md).
"""

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field

# Z position per omics layer (graphite model): genomics=0, transcriptomics=300.
GENE_LAYER_Z = 0
TRANSCRIPT_LAYER_Z = 300


class GeneNode(BaseModel):
    id: str  # graph node id == ensembl_id
    ensembl_id: str
    hgnc_symbol: Optional[str] = None
    hgnc_id: Optional[str] = None
    description: Optional[str] = None
    chromosome: Optional[str] = None
    biotype: Optional[str] = None
    is_tf: bool = False
    node_type: Literal["gene"] = "gene"
    layer_z: int = GENE_LAYER_Z


class TranscriptNode(BaseModel):
    id: str  # graph node id == ensembl_tx_id
    ensembl_tx_id: str
    hgnc_symbol: Optional[str] = None
    biotype: Optional[str] = None
    length_bp: Optional[int] = None
    node_type: Literal["transcript"] = "transcript"
    layer_z: int = TRANSCRIPT_LAYER_Z


GraphNode = Annotated[Union[GeneNode, TranscriptNode], Field(discriminator="node_type")]


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    rel_type: str  # "REGULATES" | "PRODUCES"
    mode: Optional[str] = None
    confidence: Optional[float] = None
    confidence_tier: Optional[str] = None
    tissue_weights: Optional[dict[str, float]] = None
    source_db: Optional[str] = None
    pmids: list[str] = Field(default_factory=list)
    citation_attempted: bool = False


class GraphResponse(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class SearchResult(BaseModel):
    ensembl_id: str
    hgnc_symbol: Optional[str] = None
    description: Optional[str] = None
    is_tf: bool = False
    score: float = 0.0


class QueryRequest(BaseModel):
    question: str
    tissue: str = "all"
    max_hops: int = 2


class QueryResponse(BaseModel):
    answer: str
    cypher: str
    results: list[dict] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    error: Optional[str] = None


class EdgeDetail(BaseModel):
    rel_type: str
    source: str
    target: str
    mode: Optional[str] = None
    confidence: Optional[float] = None
    confidence_tier: Optional[str] = None
    tissue_weights: Optional[dict[str, float]] = None
    source_db: Optional[str] = None
    pmids: list[str] = Field(default_factory=list)
    citation_attempted: bool = False


# --------------------------------------------------------------------------- #
# Builders: raw Neo4j dict -> API model
# --------------------------------------------------------------------------- #

def _coerce_pmids(value) -> list[str]:
    if not value:
        return []
    return [str(p) for p in value]


def build_tissue_weights(props: dict, tissues: list[str]) -> Optional[dict[str, float]]:
    """Reconstruct the tissue_weights dict from flat tw_<tissue> properties.

    Returns None when no tissue weight is present (e.g. GTEx had no data for the
    gene), so the UI can show "no data".
    """
    weights: dict[str, float] = {}
    found = False
    for tissue in tissues:
        value = props.get(f"tw_{tissue}")
        if value is not None:
            weights[tissue] = float(value)
            found = True
        else:
            weights[tissue] = 0.0
    return weights if found else None


def gene_node_from_props(props: dict, is_tf: bool) -> GeneNode:
    return GeneNode(
        id=props["ensembl_id"],
        ensembl_id=props["ensembl_id"],
        hgnc_symbol=props.get("hgnc_symbol"),
        hgnc_id=props.get("hgnc_id"),
        description=props.get("description"),
        chromosome=props.get("chromosome"),
        biotype=props.get("biotype"),
        is_tf=is_tf,
    )


def transcript_node_from_props(props: dict) -> TranscriptNode:
    return TranscriptNode(
        id=props["ensembl_tx_id"],
        ensembl_tx_id=props["ensembl_tx_id"],
        hgnc_symbol=props.get("hgnc_symbol"),
        biotype=props.get("biotype"),
        length_bp=props.get("length_bp"),
    )


def edge_from_raw(raw_edge: dict, tissues: list[str]) -> GraphEdge:
    props = raw_edge["props"]
    rel_type = raw_edge["rel_type"]
    tissue_weights = (
        build_tissue_weights(props, tissues) if rel_type == "PRODUCES" else None
    )
    return GraphEdge(
        id=f"{raw_edge['source']}__{rel_type}__{raw_edge['target']}",
        source=raw_edge["source"],
        target=raw_edge["target"],
        rel_type=rel_type,
        mode=props.get("mode"),
        confidence=props.get("confidence"),
        confidence_tier=props.get("confidence_tier"),
        tissue_weights=tissue_weights,
        source_db=props.get("source_db"),
        pmids=_coerce_pmids(props.get("pmids")),
        citation_attempted=bool(props.get("citation_attempted", False)),
    )


def graph_response_from_raw(raw: dict, tissues: list[str]) -> GraphResponse:
    nodes: list[GraphNode] = []
    for node in raw["nodes"]:
        if node["kind"] == "gene":
            nodes.append(gene_node_from_props(node["props"], node["is_tf"]))
        else:
            nodes.append(transcript_node_from_props(node["props"]))
    edges = [edge_from_raw(e, tissues) for e in raw["edges"]]
    return GraphResponse(nodes=nodes, edges=edges)


def edge_detail_from_raw(raw_edge: dict, tissues: list[str]) -> EdgeDetail:
    props = raw_edge["props"]
    rel_type = raw_edge["rel_type"]
    tissue_weights = (
        build_tissue_weights(props, tissues) if rel_type == "PRODUCES" else None
    )
    return EdgeDetail(
        rel_type=rel_type,
        source=raw_edge["source"],
        target=raw_edge["target"],
        mode=props.get("mode"),
        confidence=props.get("confidence"),
        confidence_tier=props.get("confidence_tier"),
        tissue_weights=tissue_weights,
        source_db=props.get("source_db"),
        pmids=_coerce_pmids(props.get("pmids")),
        citation_attempted=bool(props.get("citation_attempted", False)),
    )
