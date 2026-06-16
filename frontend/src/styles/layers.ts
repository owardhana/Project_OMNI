// Layer config + node/edge color and size helpers (graphite model, 3 layers).

import type { FGLink, FGNode, GraphNode } from '../types/graph';

export type LayerKey = 'genomics' | 'transcriptomics' | 'proteomics';

// Three stacked omics layers (ADR-0004), stacked along the VERTICAL axis (world
// Y) so genomics reads at the bottom of the screen and proteomics at the top.
// Y-centres are symmetric around the origin and widely spaced so the force layout
// reads as distinct stacked planes (a web across layers), not one clump. The
// camera is soft-locked (orbit, constrained polar angle) so this never flips.
export const LAYERS: Record<
  LayerKey,
  { y: number; color: string; label: string }
> = {
  genomics: { y: -300, color: '#6b7280', label: 'Genomics' },
  transcriptomics: { y: 0, color: '#6b7280', label: 'Transcriptomics' },
  proteomics: { y: 300, color: '#6b7280', label: 'Proteomics' },
};

export const GENE_Y = LAYERS.genomics.y;
export const TRANSCRIPT_Y = LAYERS.transcriptomics.y;
export const PROTEIN_Y = LAYERS.proteomics.y;
export const Y_JITTER = 42; // +/- deterministic per-node jitter so planes aren't flat

// Node colors: saturated color lives only on the graph (neutral chrome elsewhere).
// A TF is a protein subtype -> amber; genes green; transcripts blue.
export const NODE_COLORS = {
  protein_tf: '#f59e0b', // amber — transcription-factor protein
  protein: '#c084fc', // violet — other protein subtypes (future)
  gene: '#4ade80', // green — gene
  transcript: '#60a5fa', // blue — transcript
};

export const EDGE_COLORS = {
  activator: '#22c55e',
  repressor: '#ef4444',
  produces: '#60a5fa',
  translates: '#a78bfa',
  encodes: '#a78bfa',
  unknown: '#9ca3af',
};

export const NODE_SIZES = {
  protein: 13, // TF hubs read large
  gene: 8,
  transcript: 5,
};

export function nodeLayer(node: GraphNode): LayerKey {
  if (node.node_type === 'transcript') return 'transcriptomics';
  if (node.node_type === 'protein') return 'proteomics';
  return 'genomics';
}

export function nodeColor(node: FGNode): string {
  if (node.node_type === 'transcript') return NODE_COLORS.transcript;
  if (node.node_type === 'protein') {
    return node.subtype === 'transcription_factor'
      ? NODE_COLORS.protein_tf
      : NODE_COLORS.protein;
  }
  return NODE_COLORS.gene;
}

export function nodeSize(node: FGNode): number {
  if (node.node_type === 'transcript') return NODE_SIZES.transcript;
  if (node.node_type === 'protein') return NODE_SIZES.protein;
  return NODE_SIZES.gene;
}

// Node shape per layer reinforces the identity system (color + layer + shape):
// gene = sphere, transcript = octahedron, protein = box.
export type NodeShape = 'sphere' | 'octahedron' | 'box';
export function nodeShape(node: GraphNode): NodeShape {
  if (node.node_type === 'transcript') return 'octahedron';
  if (node.node_type === 'protein') return 'box';
  return 'sphere';
}

export function edgeColor(link: FGLink): string {
  if (link.rel_type === 'PRODUCES') return EDGE_COLORS.produces;
  if (link.rel_type === 'TRANSLATES_TO') return EDGE_COLORS.translates;
  if (link.rel_type === 'ENCODES') return EDGE_COLORS.encodes;
  if (link.mode === 'activator') return EDGE_COLORS.activator;
  if (link.mode === 'repressor') return EDGE_COLORS.repressor;
  return EDGE_COLORS.unknown;
}
