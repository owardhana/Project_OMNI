// Layer config + node/edge color and size helpers (graphite model).

import type { FGLink, FGNode, GraphNode } from '../types/graph';

export type LayerKey = 'genomics' | 'transcriptomics';

export const LAYERS: Record<LayerKey, { z: number; color: string; label: string }> = {
  genomics: { z: 0, color: '#f59e0b', label: 'Genomics' },
  transcriptomics: { z: 300, color: '#60a5fa', label: 'Transcriptomics' },
};

export const NODE_COLORS = {
  tf: '#f59e0b', // amber — Gene with outgoing REGULATES
  gene: '#4ade80', // green — Gene (no outgoing REGULATES)
  transcript: '#60a5fa', // blue — Transcript
};

export const EDGE_COLORS = {
  activator: '#22c55e',
  repressor: '#ef4444',
  produces: '#a78bfa',
  unknown: '#9ca3af',
};

export const NODE_SIZES = {
  tf: 12,
  gene: 8,
  transcript: 6,
};

export function nodeLayer(node: GraphNode): LayerKey {
  return node.node_type === 'transcript' ? 'transcriptomics' : 'genomics';
}

export function nodeColor(node: FGNode): string {
  if (node.node_type === 'transcript') return NODE_COLORS.transcript;
  return node.is_tf ? NODE_COLORS.tf : NODE_COLORS.gene;
}

export function nodeSize(node: FGNode): number {
  if (node.node_type === 'transcript') return NODE_SIZES.transcript;
  return node.is_tf ? NODE_SIZES.tf : NODE_SIZES.gene;
}

export function edgeColor(link: FGLink): string {
  if (link.rel_type === 'PRODUCES') return EDGE_COLORS.produces;
  if (link.mode === 'activator') return EDGE_COLORS.activator;
  if (link.mode === 'repressor') return EDGE_COLORS.repressor;
  return EDGE_COLORS.unknown;
}
