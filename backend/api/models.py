"""Pydantic request/response models + builders from raw Neo4j data.

The DB layer (backend/db) returns plain dicts (it may not import the API layer).
These builders convert that raw shape into typed API models — including
reconstructing the ``tissue_weights`` dict from the flat ``tw_<tissue>`` edge
properties stored in Neo4j (see docs/adr/0001-tissue-weights-flat-properties.md).
"""

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field

# Z position per omics layer (graphite model): genomics=0, transcriptomics=300,
# proteomics=600 (ADR-0004), metabolomics=900 (ADR-0009 — the 4th layer),
# phenotype=1200 (Disease, shifted up from 900 by ADR-0009 — now the 5th layer).
# Variants sit in the genomics layer alongside genes; metabolites get their own
# plane between proteomics and phenotype. Frontend uses its own layer coords; this
# is metadata. The 900 -> 1200 Disease shift is the ADR-0009 regression vector —
# any hardcoded 900 not routed through DISEASE_LAYER_Z breaks silently.
GENE_LAYER_Z = 0
TRANSCRIPT_LAYER_Z = 300
PROTEIN_LAYER_Z = 600
METABOLITE_LAYER_Z = 900  # ADR-0009 — metabolomics, between proteomics and phenotype
DISEASE_LAYER_Z = 1200  # ADR-0009 — phenotype, shifted up from 900


class GeneNode(BaseModel):
    id: str  # graph node id == ensembl_id
    ensembl_id: str
    hgnc_symbol: Optional[str] = None
    hgnc_id: Optional[str] = None
    description: Optional[str] = None
    chromosome: Optional[str] = None
    biotype: Optional[str] = None
    is_tf: bool = False
    pli_score: Optional[float] = None
    cancer_gene: Optional[bool] = None
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


class ProteinNode(BaseModel):
    id: str  # graph node id == uniprot_id
    uniprot_id: str
    hgnc_symbol: Optional[str] = None
    # subtype distinguishes kinds of protein (only transcription_factor in MVP);
    # node_type is the entity-kind discriminator (gene | transcript | protein).
    subtype: Optional[str] = None
    summary_text: Optional[str] = None
    go_terms: list[str] = Field(default_factory=list)
    subcellular_loc: Optional[str] = None
    molecular_weight: Optional[float] = None
    node_type: Literal["protein"] = "protein"
    layer_z: int = PROTEIN_LAYER_Z


class VariantNode(BaseModel):
    id: str  # rsid or chr:pos:ref:alt
    rsid: Optional[str] = None
    chromosome: Optional[str] = None
    position_grch38: Optional[int] = None
    consequence_type: Optional[str] = None
    cadd_score: Optional[float] = None
    gnomad_af: Optional[float] = None
    clinical_significance: Optional[str] = None
    node_type: Literal["variant"] = "variant"
    layer_z: int = GENE_LAYER_Z  # variant sits in the genomics layer


class DiseaseNode(BaseModel):
    id: str  # ontology_id (EFO / MONDO / Orphanet / ...)
    ontology_id: str
    name: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    node_type: Literal["disease"] = "disease"
    layer_z: int = DISEASE_LAYER_Z  # phenotype layer (above metabolomics, ADR-0009)


class MetaboliteNode(BaseModel):
    id: str  # hmdb_id (primary) or chebi_id (fallback) — ADR-0009
    hmdb_id: Optional[str] = None
    chebi_id: Optional[str] = None
    name: Optional[str] = None
    formula: Optional[str] = None
    charge: Optional[int] = None
    node_type: Literal["metabolite"] = "metabolite"
    layer_z: int = METABOLITE_LAYER_Z  # metabolomics layer (above proteomics)


GraphNode = Annotated[
    Union[GeneNode, TranscriptNode, ProteinNode, VariantNode, DiseaseNode, MetaboliteNode],
    Field(discriminator="node_type"),
]


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    rel_type: str  # REGULATES | PRODUCES | TRANSLATES_TO | ENCODES |
    #                INTERACTS_WITH | ASSOCIATED_WITH | IN_GENE | IMPLICATED_IN |
    #                DIFFERENTIALLY_EXPRESSED (Gene->Disease, TCGA) |
    #                CATALYSES (Protein->Metabolite, Recon3D)
    mode: Optional[str] = None
    confidence: Optional[float] = None
    confidence_tier: Optional[str] = None
    tissue_weights: Optional[dict[str, float]] = None
    # Phase 2 edge attributes (only set on the relevant relationship types).
    combined_score: Optional[float] = None  # INTERACTS_WITH (STRING)
    experimental_score: Optional[float] = None
    coexpression_score: Optional[float] = None
    p_value: Optional[float] = None  # ASSOCIATED_WITH (GWAS)
    consequence_type: Optional[str] = None  # IN_GENE
    # Phase 3 edge attributes (docs/data-architecture.md).
    log2fc: Optional[float] = None  # DIFFERENTIALLY_EXPRESSED (TCGA)
    direction: Optional[str] = None  # "up" | "down"
    tumor_type: Optional[str] = None  # TCGA cancer code (e.g. "LUAD")
    role: Optional[str] = None  # CATALYSES: "substrate" | "product"
    reaction_id: Optional[str] = None  # CATALYSES: Recon3D reaction ID
    source_db: Optional[str] = None
    pmids: list[str] = Field(default_factory=list)
    citation_attempted: bool = False


class GraphWarning(BaseModel):
    type: str  # e.g. "disconnected"
    component_count: Optional[int] = None
    message: str


class GraphResponse(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    warnings: list[GraphWarning] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class MultiGraphRequest(BaseModel):
    seed_ids: list[str]
    seed_types: list[str]  # parallel to seed_ids: gene|protein|variant|disease


class PathResponse(BaseModel):
    path_found: bool
    hop_count: Optional[int] = None
    path_quality: Literal["direct", "moderate", "weak", "no_path"]
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    warning: Optional[str] = None


class SearchResult(BaseModel):
    id: str  # ensembl_id | ensembl_tx_id | uniprot_id | ontology_id | hmdb_id
    node_type: str  # gene | transcript | protein | disease | metabolite
    hgnc_symbol: Optional[str] = None
    name: Optional[str] = None  # disease display name
    description: Optional[str] = None
    is_tf: bool = False
    score: float = 0.0
    ensembl_id: Optional[str] = None  # populated for gene results (back-compat)


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
    combined_score: Optional[float] = None
    experimental_score: Optional[float] = None
    coexpression_score: Optional[float] = None
    p_value: Optional[float] = None
    consequence_type: Optional[str] = None
    log2fc: Optional[float] = None  # DIFFERENTIALLY_EXPRESSED (TCGA)
    direction: Optional[str] = None
    tumor_type: Optional[str] = None
    role: Optional[str] = None  # CATALYSES
    reaction_id: Optional[str] = None
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
        pli_score=props.get("pli_score"),
        cancer_gene=props.get("cancer_gene"),
    )


def transcript_node_from_props(props: dict) -> TranscriptNode:
    return TranscriptNode(
        id=props["ensembl_tx_id"],
        ensembl_tx_id=props["ensembl_tx_id"],
        hgnc_symbol=props.get("hgnc_symbol"),
        biotype=props.get("biotype"),
        length_bp=props.get("length_bp"),
    )


def protein_node_from_props(props: dict) -> ProteinNode:
    return ProteinNode(
        id=props["uniprot_id"],
        uniprot_id=props["uniprot_id"],
        hgnc_symbol=props.get("hgnc_symbol"),
        subtype=props.get("subtype"),
        summary_text=props.get("summary_text"),
        go_terms=list(props.get("go_terms") or []),
        subcellular_loc=props.get("subcellular_loc"),
        molecular_weight=props.get("molecular_weight"),
    )


def variant_node_from_props(props: dict) -> VariantNode:
    rsid = props.get("rsid")
    return VariantNode(
        id=rsid if rsid is not None else props.get("id", ""),
        rsid=rsid,
        chromosome=props.get("chromosome"),
        position_grch38=props.get("position_grch38"),
        consequence_type=props.get("consequence_type"),
        cadd_score=props.get("cadd_score"),
        gnomad_af=props.get("gnomad_af"),
        clinical_significance=props.get("clinical_significance"),
    )


def disease_node_from_props(props: dict) -> DiseaseNode:
    return DiseaseNode(
        id=props["ontology_id"],
        ontology_id=props["ontology_id"],
        name=props.get("name"),
        category=props.get("category"),
        description=props.get("description"),
    )


def metabolite_node_from_props(props: dict) -> MetaboliteNode:
    # Canonical key is hmdb_id (primary) with chebi_id fallback (ADR-0009).
    hmdb_id = props.get("hmdb_id")
    chebi_id = props.get("chebi_id")
    key = hmdb_id if hmdb_id is not None else (chebi_id or props.get("id", ""))
    return MetaboliteNode(
        id=key,
        hmdb_id=hmdb_id,
        chebi_id=chebi_id,
        name=props.get("name"),
        formula=props.get("formula"),
        charge=props.get("charge"),
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
        combined_score=props.get("combined_score"),
        experimental_score=props.get("experimental_score"),
        coexpression_score=props.get("coexpression_score"),
        p_value=props.get("p_value"),
        consequence_type=props.get("consequence_type"),
        log2fc=props.get("log2fc"),
        direction=props.get("direction"),
        tumor_type=props.get("tumor_type"),
        role=props.get("role"),
        reaction_id=props.get("reaction_id"),
        source_db=props.get("source_db"),
        pmids=_coerce_pmids(props.get("pmids")),
        citation_attempted=bool(props.get("citation_attempted", False)),
    )


_NODE_BUILDERS = {
    "protein": protein_node_from_props,
    "variant": variant_node_from_props,
    "disease": disease_node_from_props,
    "metabolite": metabolite_node_from_props,
    "transcript": transcript_node_from_props,
}


def graph_response_from_raw(raw: dict, tissues: list[str]) -> GraphResponse:
    nodes: list[GraphNode] = []
    for node in raw["nodes"]:
        kind = node["kind"]
        if kind == "gene":
            nodes.append(gene_node_from_props(node["props"], node.get("is_tf", False)))
        else:
            builder = _NODE_BUILDERS.get(kind, transcript_node_from_props)
            nodes.append(builder(node["props"]))
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
        combined_score=props.get("combined_score"),
        experimental_score=props.get("experimental_score"),
        coexpression_score=props.get("coexpression_score"),
        p_value=props.get("p_value"),
        consequence_type=props.get("consequence_type"),
        log2fc=props.get("log2fc"),
        direction=props.get("direction"),
        tumor_type=props.get("tumor_type"),
        role=props.get("role"),
        reaction_id=props.get("reaction_id"),
        source_db=props.get("source_db"),
        pmids=_coerce_pmids(props.get("pmids")),
        citation_attempted=bool(props.get("citation_attempted", False)),
    )
