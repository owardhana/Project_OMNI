import { useCallback, useState } from 'react';

import { api } from '../api/client';
import {
  GENE_Z,
  INTERLAYER_NUDGE,
  TRANSCRIPT_Z,
  Z_JITTER,
} from '../styles/layers';
import type { FGLink, FGNode, ForceGraphData, GraphResponse } from '../types/graph';

// Deterministic per-node jitter in [-Z_JITTER, +Z_JITTER] from the node id, so a
// node's target stays stable across re-layouts (no jumpiness on expand).
function jitterFor(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) | 0;
  return ((Math.abs(h) % 1000) / 1000 - 0.5) * 2 * Z_JITTER;
}

function linkEndpointId(end: string | FGNode): string {
  return typeof end === 'object' ? end.id : end;
}

// Genes that produce a transcript (have a cross-layer PRODUCES edge) get pulled
// toward the transcriptomics layer for emphasis.
function interlayerGeneIds(links: FGLink[]): Set<string> {
  const ids = new Set<string>();
  for (const l of links) {
    if (l.rel_type === 'PRODUCES') ids.add(linkEndpointId(l.source));
  }
  return ids;
}

function zTargetFor(node: FGNode, interlayerGenes: Set<string>): number {
  const jitter = jitterFor(node.id);
  if (node.node_type === 'transcript') {
    // transcripts always connect down to their gene -> drift toward genomics
    return TRANSCRIPT_Z - INTERLAYER_NUDGE + jitter;
  }
  const nudge = interlayerGenes.has(node.id) ? INTERLAYER_NUDGE : 0;
  return GENE_Z + nudge + jitter;
}

// Convert a backend GraphResponse into react-force-graph data. Instead of hard-
// pinning Z, each node gets a soft zTarget (its layer +/- interlayer drift +
// jitter); GraphViewer3D applies a forceZ toward it, so layers are distinct but
// organically uneven rather than perfectly flat planes.
function toForceGraph(resp: GraphResponse): ForceGraphData {
  const links: FGLink[] = resp.edges.map((e) => ({ ...e }));
  const interlayer = interlayerGeneIds(links);
  const nodes: FGNode[] = resp.nodes.map((n) => {
    const node: FGNode = { ...n };
    node.zTarget = zTargetFor(node, interlayer);
    node.z = node.zTarget;
    node.fz = node.zTarget; // pin z to the layer target; X/Y stay free
    return node;
  });
  return { nodes, links };
}

// Merge new graph data into existing, preserving existing node/link references
// (so the force sim keeps their positions). Recompute zTargets on the merged set
// so a gene that just gained a transcript becomes interlayer.
function mergeGraph(prev: ForceGraphData, incoming: ForceGraphData): ForceGraphData {
  const nodeIds = new Set(prev.nodes.map((n) => n.id));
  const linkIds = new Set(prev.links.map((l) => l.id));
  const nodes = [...prev.nodes, ...incoming.nodes.filter((n) => !nodeIds.has(n.id))];
  const links = [...prev.links, ...incoming.links.filter((l) => !linkIds.has(l.id))];
  const interlayer = interlayerGeneIds(links);
  for (const n of nodes) {
    n.zTarget = zTargetFor(n, interlayer); // refresh (a gene may now be interlayer)
    n.fz = n.zTarget;
  }
  return { nodes, links };
}

export function useGraph() {
  const [graphData, setGraphData] = useState<ForceGraphData>({ nodes: [], links: [] });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadGene = useCallback(async (symbol: string, tissue = 'all', hops = 1) => {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.getGeneGraph(symbol, tissue, hops);
      setGraphData(toForceGraph(resp));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const expandNode = useCallback(async (symbol: string, tissue = 'all') => {
    try {
      const resp = await api.getGeneGraph(symbol, tissue, 1);
      setGraphData((prev) => mergeGraph(prev, toForceGraph(resp)));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  return { graphData, loading, error, loadGene, expandNode };
}
