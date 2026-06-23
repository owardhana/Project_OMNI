import { useCallback, useState } from 'react';

import { api } from '../api/client';
import {
  DISEASE_Y,
  GENE_Y,
  METABOLITE_Y,
  PROTEIN_Y,
  TRANSCRIPT_Y,
  Y_JITTER,
} from '../styles/layers';
import type { FGNode, ForceGraphData, GraphResponse } from '../types/graph';

// Deterministic per-node jitter in [-Y_JITTER, +Y_JITTER] from the node id, so a
// node's target stays stable across re-layouts (no jumpiness on expand).
function jitterFor(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) | 0;
  return ((Math.abs(h) % 1000) / 1000 - 0.5) * 2 * Y_JITTER;
}

function yTargetFor(node: FGNode): number {
  const jitter = jitterFor(node.id);
  if (node.node_type === 'transcript') return TRANSCRIPT_Y + jitter;
  if (node.node_type === 'protein') return PROTEIN_Y + jitter;
  if (node.node_type === 'metabolite') return METABOLITE_Y + jitter;
  if (node.node_type === 'disease') return DISEASE_Y + jitter;
  return GENE_Y + jitter; // gene + variant both live in the genomics plane
}

// Convert a backend GraphResponse into react-force-graph data. Each node is
// pinned to its layer's Y (the vertical axis, plus deterministic jitter); X/Z
// stay free-simulated, so each layer is a horizontal disc stacked vertically.
function toForceGraph(resp: GraphResponse): ForceGraphData {
  const links = resp.edges.map((e) => ({ ...e }));
  const nodes: FGNode[] = resp.nodes.map((n) => {
    const node: FGNode = { ...n };
    node.yTarget = yTargetFor(node);
    node.y = node.yTarget;
    node.fy = node.yTarget; // pin y to the layer target; X/Z stay free
    return node;
  });
  return { nodes, links };
}

function mergeGraph(prev: ForceGraphData, incoming: ForceGraphData): ForceGraphData {
  const nodeIds = new Set(prev.nodes.map((n) => n.id));
  const linkIds = new Set(prev.links.map((l) => l.id));
  const nodes = [...prev.nodes, ...incoming.nodes.filter((n) => !nodeIds.has(n.id))];
  const links = [...prev.links, ...incoming.links.filter((l) => !linkIds.has(l.id))];
  return { nodes, links };
}

export function useGraph() {
  const [graphData, setGraphData] = useState<ForceGraphData>({ nodes: [], links: [] });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Tissue is a render-time opacity concern (ADR-0006), so it is NOT a fetch
  // parameter — the subgraph is the same regardless of tissue.
  const loadGene = useCallback(async (symbol: string) => {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.getGeneGraph(symbol);
      setGraphData(toForceGraph(resp));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadDisease = useCallback(async (ontologyId: string) => {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.getDiseaseGraph(ontologyId);
      setGraphData(toForceGraph(resp));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadMetabolite = useCallback(async (id: string) => {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.getMetaboliteGraph(id);
      setGraphData(toForceGraph(resp));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const expandNode = useCallback(async (symbol: string) => {
    try {
      const resp = await api.getGeneGraph(symbol);
      setGraphData((prev) => mergeGraph(prev, toForceGraph(resp)));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  // Additive merge of an externally-built subgraph (Phase 14 multi-load / path).
  const mergeInto = useCallback((resp: GraphResponse) => {
    setGraphData((prev) => mergeGraph(prev, toForceGraph(resp)));
  }, []);

  const clearGraph = useCallback(() => {
    setGraphData({ nodes: [], links: [] });
  }, []);

  return {
    graphData,
    loading,
    error,
    loadGene,
    loadDisease,
    loadMetabolite,
    expandNode,
    mergeInto,
    clearGraph,
  };
}
