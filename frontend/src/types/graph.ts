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

export type GraphNode = GeneNode | TranscriptNode;

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  rel_type: 'REGULATES' | 'PRODUCES';
  mode?: string | null;
  confidence?: number | null;
  confidence_tier?: string | null;
  tissue_weights?: Record<string, number> | null;
  source_db?: string | null;
  pmids: string[];
  citation_attempted: boolean;
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface SearchResult {
  ensembl_id: string;
  hgnc_symbol?: string | null;
  description?: string | null;
  is_tf: boolean;
  score: number;
}

export interface QueryRequest {
  question: string;
  tissue?: string;
  max_hops?: number;
}

export interface QueryResponse {
  answer: string;
  cypher: string;
  results: Record<string, unknown>[];
  citations: string[];
  error?: string | null;
}

// react-force-graph runtime shapes (nodes/links gain simulation fields).
export type FGNode = GraphNode & {
  x?: number;
  y?: number;
  z?: number;
  fz?: number;
};

export type FGLink = GraphEdge & {
  source: string | FGNode;
  target: string | FGNode;
};

export interface ForceGraphData {
  nodes: FGNode[];
  links: FGLink[];
}
