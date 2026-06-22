// Layer config + node/edge color and size helpers (graphite model, 4 layers).

import type { FGLink, FGNode, GraphNode } from '../types/graph';

export type LayerKey =
  | 'genomics'
  | 'transcriptomics'
  | 'proteomics'
  | 'metabolomics'
  | 'phenotype';

// Backend layer_z metadata parity (ADR-0009 — shifted 900 -> 1200 when the
// metabolomics layer was inserted). The frontend positions layers on the world Y
// axis (below); this exports the backend metadata value for cross-checks.
export const DISEASE_LAYER_Z = 1200;

// Five stacked omics/phenotype layers, stacked along the VERTICAL axis (world Y)
// so genomics reads at the bottom and the phenotype layer at the top. Y-centres
// are evenly spaced (300 apart) so the force layout reads as distinct stacked
// planes. `color` tints the (intentionally muted) layer plane — the metabolomics
// plane is orange and the phenotype plane pink (ADR-0009 palette); the lower three
// stay neutral so node colour carries the meaning. The layer toggle's swatches are
// NOT defined here: they derive from LAYER_NODE_COLORS (below) so the toggle, the
// legend, and the graph all read their colours from NODE_COLORS — one source.
export const LAYERS: Record<
  LayerKey,
  { y: number; color: string; label: string }
> = {
  genomics: { y: -300, color: '#6b7280', label: 'Genomics' },
  transcriptomics: { y: 0, color: '#6b7280', label: 'Transcriptomics' },
  proteomics: { y: 300, color: '#6b7280', label: 'Proteomics' },
  metabolomics: { y: 600, color: '#fb923c', label: 'Metabolomics' },
  phenotype: { y: 900, color: '#f472b6', label: 'Phenotype' },
};

export const GENE_Y = LAYERS.genomics.y;
export const TRANSCRIPT_Y = LAYERS.transcriptomics.y;
export const PROTEIN_Y = LAYERS.proteomics.y;
export const METABOLITE_Y = LAYERS.metabolomics.y;
export const DISEASE_Y = LAYERS.phenotype.y;
export const Y_JITTER = 42; // +/- deterministic per-node jitter so planes aren't flat

// Node colors: saturated color lives only on the graph (neutral chrome elsewhere).
export const NODE_COLORS = {
  protein_tf: '#f59e0b', // amber — transcription-factor protein (accent)
  protein: '#c084fc', // violet — all other protein subtypes
  gene: '#4ade80', // green — gene
  transcript: '#60a5fa', // blue — transcript
  variant: '#2dd4bf', // teal — variant (in the genomics plane)
  metabolite: '#fb923c', // orange — metabolite (metabolomics plane, ADR-0009)
  disease: '#f472b6', // hot pink — disease (phenotype plane)
};

// The node-type colours present in each layer, derived from NODE_COLORS so the
// layer toggle swatches are EXACTLY the graph node colours (single source of
// truth — fixes the toggle/legend/graph colour drift). Two layers hold more than
// one node type, so they carry two swatches: genomics = gene + variant,
// proteomics = (non-TF) protein + TF protein.
export const LAYER_NODE_COLORS: Record<LayerKey, string[]> = {
  genomics: [NODE_COLORS.gene, NODE_COLORS.variant],
  transcriptomics: [NODE_COLORS.transcript],
  proteomics: [NODE_COLORS.protein, NODE_COLORS.protein_tf],
  metabolomics: [NODE_COLORS.metabolite],
  phenotype: [NODE_COLORS.disease],
};

export const EDGE_COLORS = {
  activator: '#22c55e',
  repressor: '#ef4444',
  produces: '#818cf8',
  translates: '#c084fc',
  encodes: '#c084fc',
  interacts_with: '#64748b',
  associated_with: '#f472b6',
  in_gene: '#2dd4bf',
  implicated_in: '#fb923c',
  catalyses: '#fb923c', // orange — enzymatic Protein->Metabolite (ADR-0009)
  differentially_expressed: '#f59e0b', // amber — TCGA cancer DE (Gene->Disease)
  unknown: '#9ca3af',
};

export const NODE_SIZES = {
  protein: 11, // TF hubs read large (further scaled x1.4 below)
  gene: 8,
  transcript: 5,
  variant: 7,
  metabolite: 7, // small-molecule node (ADR-0009)
  disease: 9, // scaled x1.6 below so diseases read largest
};

export const TF_SIZE_SCALE = 1.4;
export const DISEASE_SIZE_SCALE = 1.6;

export function nodeLayer(node: GraphNode): LayerKey {
  if (node.node_type === 'transcript') return 'transcriptomics';
  if (node.node_type === 'protein') return 'proteomics';
  if (node.node_type === 'metabolite') return 'metabolomics';
  if (node.node_type === 'disease') return 'phenotype';
  // gene + variant both live in the genomics plane.
  return 'genomics';
}

export function nodeColor(node: FGNode): string {
  if (node.node_type === 'transcript') return NODE_COLORS.transcript;
  if (node.node_type === 'protein') {
    return node.subtype === 'transcription_factor'
      ? NODE_COLORS.protein_tf
      : NODE_COLORS.protein;
  }
  if (node.node_type === 'variant') return NODE_COLORS.variant;
  if (node.node_type === 'metabolite') return NODE_COLORS.metabolite;
  if (node.node_type === 'disease') return NODE_COLORS.disease;
  return NODE_COLORS.gene;
}

export function nodeSize(node: FGNode): number {
  if (node.node_type === 'transcript') return NODE_SIZES.transcript;
  if (node.node_type === 'protein') {
    return node.subtype === 'transcription_factor'
      ? NODE_SIZES.protein * TF_SIZE_SCALE
      : NODE_SIZES.protein;
  }
  if (node.node_type === 'variant') return NODE_SIZES.variant;
  if (node.node_type === 'metabolite') return NODE_SIZES.metabolite;
  if (node.node_type === 'disease') return NODE_SIZES.disease * DISEASE_SIZE_SCALE;
  return NODE_SIZES.gene;
}

// Node shape per kind reinforces the identity system (color + layer + shape):
// gene/variant/disease = sphere, transcript = octahedron, protein = box.
export type NodeShape = 'sphere' | 'octahedron' | 'box';
export function nodeShape(node: GraphNode): NodeShape {
  if (node.node_type === 'transcript') return 'octahedron';
  if (node.node_type === 'protein') return 'box';
  return 'sphere';
}

export function edgeColor(link: FGLink): string {
  switch (link.rel_type) {
    case 'PRODUCES':
      return EDGE_COLORS.produces;
    case 'TRANSLATES_TO':
      return EDGE_COLORS.translates;
    case 'ENCODES':
      return EDGE_COLORS.encodes;
    case 'INTERACTS_WITH':
      return EDGE_COLORS.interacts_with;
    case 'ASSOCIATED_WITH':
      return EDGE_COLORS.associated_with;
    case 'IN_GENE':
      return EDGE_COLORS.in_gene;
    case 'IMPLICATED_IN':
      return EDGE_COLORS.implicated_in;
    case 'CATALYSES':
      return EDGE_COLORS.catalyses;
    case 'DIFFERENTIALLY_EXPRESSED':
      return EDGE_COLORS.differentially_expressed;
    case 'REGULATES':
      if (link.mode === 'activator') return EDGE_COLORS.activator;
      if (link.mode === 'repressor') return EDGE_COLORS.repressor;
      return EDGE_COLORS.unknown;
    default:
      return EDGE_COLORS.unknown;
  }
}
