// TypeScript interfaces mirroring backend/api/models.py (single source of truth).

export interface GeneNode {
  id: string;
  ensembl_id: string;
  hgnc_symbol?: string | null;
  hgnc_id?: string | null;
  description?: string | null;
  chromosome?: string | null;
  biotype?: string | null;
  is_tf: boolean;
  pli_score?: number | null;
  cancer_gene?: boolean | null;
  node_type: 'gene';
  layer_z: number;
}

export interface TranscriptNode {
  id: string;
  ensembl_tx_id: string;
  hgnc_symbol?: string | null;
  biotype?: string | null;
  length_bp?: number | null;
  node_type: 'transcript';
  layer_z: number;
}

export interface ProteinNode {
  id: string;
  uniprot_id: string;
  hgnc_symbol?: string | null;
  subtype?: string | null; // 'transcription_factor' in MVP
  summary_text?: string | null;
  go_terms?: string[];
  subcellular_loc?: string | null;
  molecular_weight?: number | null;
  node_type: 'protein';
  layer_z: number;
}

export interface VariantNode {
  id: string;
  rsid?: string | null;
  chromosome?: string | null;
  position_grch38?: number | null;
  consequence_type?: string | null;
  cadd_score?: number | null;
  gnomad_af?: number | null;
  clinical_significance?: string | null;
  node_type: 'variant';
  layer_z: number;
}

export interface DiseaseNode {
  id: string;
  ontology_id: string;
  name?: string | null;
  category?: string | null;
  description?: string | null;
  node_type: 'disease';
  layer_z: number;
}

export interface MetaboliteNode {
  id: string; // hmdb_id (primary) or chebi_id (fallback) — ADR-0009
  hmdb_id?: string | null;
  chebi_id?: string | null;
  name?: string | null;
  formula?: string | null;
  charge?: number | null;
  node_type: 'metabolite';
  layer_z: number;
}

export type GraphNode =
  | GeneNode
  | TranscriptNode
  | ProteinNode
  | VariantNode
  | DiseaseNode
  | MetaboliteNode;

export type RelType =
  | 'REGULATES'
  | 'PRODUCES'
  | 'TRANSLATES_TO'
  | 'ENCODES'
  | 'INTERACTS_WITH'
  | 'ASSOCIATED_WITH'
  | 'IN_GENE'
  | 'IMPLICATED_IN'
  | 'DIFFERENTIALLY_EXPRESSED'
  | 'CATALYSES';

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  rel_type: RelType;
  mode?: string | null;
  confidence?: number | null;
  confidence_tier?: string | null;
  tissue_weights?: Record<string, number> | null;
  combined_score?: number | null;
  experimental_score?: number | null;
  coexpression_score?: number | null;
  p_value?: number | null;
  consequence_type?: string | null;
  // Phase 3 edge attributes (docs/data-architecture.md).
  log2fc?: number | null; // DIFFERENTIALLY_EXPRESSED (TCGA)
  direction?: string | null; // 'up' | 'down'
  tumor_type?: string | null; // TCGA cancer code
  role?: string | null; // CATALYSES: 'substrate' | 'product'
  reaction_id?: string | null; // CATALYSES: Recon3D reaction id
  source_db?: string | null;
  pmids: string[];
  citation_attempted: boolean;
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  // Phase 14: POST /api/graph/multi may flag disconnected seed clusters.
  warnings?: GraphWarning[];
  metadata?: Record<string, unknown>;
}

export interface GraphWarning {
  type: string;
  component_count?: number;
  message: string;
}

export interface SearchResult {
  id: string;
  node_type: 'gene' | 'transcript' | 'protein' | 'variant' | 'disease' | 'metabolite';
  hgnc_symbol?: string | null;
  name?: string | null;
  description?: string | null;
  is_tf: boolean;
  score: number;
  ensembl_id?: string | null;
}

export interface PathResponse {
  path_found: boolean;
  hop_count: number | null;
  path_quality: 'direct' | 'moderate' | 'weak' | 'no_path';
  nodes: GraphNode[];
  edges: GraphEdge[];
  warning?: string | null;
}

export interface EntityItem {
  id: string;
  node_type: SearchResult['node_type'];
  display_name: string;
  description?: string | null;
  is_tf?: boolean;
}

export interface EntitySearchResponse {
  items: EntityItem[];
  results: EntityItem[];
  total: number;
  has_more: boolean;
}

// react-force-graph runtime shapes (nodes/links gain simulation fields).
export type FGNode = GraphNode & {
  x?: number;
  y?: number;
  z?: number;
  yTarget?: number; // layer target on the vertical axis (see useGraph)
  fy?: number; // pinned y = yTarget; X/Z stay free
  // Phase 14 seed-tint accent (per-seed ring colour); undefined = neutral bridge.
  seedAccent?: string | null;
};

export type FGLink = GraphEdge & {
  source: string | FGNode;
  target: string | FGNode;
};

export interface ForceGraphData {
  nodes: FGNode[];
  links: FGLink[];
}
