import { useCallback, useState } from 'react';

import { api } from '../api/client';
import type { ForceGraphData, GraphResponse } from '../types/graph';

// Convert a backend GraphResponse into react-force-graph data. Node Z is pinned
// to its layer (fz) so the force simulation only moves nodes within their X/Y
// plane — the graphite-layer look.
function toForceGraph(resp: GraphResponse): ForceGraphData {
  return {
    nodes: resp.nodes.map((n) => ({ ...n, fz: n.layer_z })),
    links: resp.edges.map((e) => ({ ...e })),
  };
}

// Merge new graph data into existing, preserving existing node/link object
// references (so react-force-graph keeps their simulated positions on expand).
function mergeGraph(prev: ForceGraphData, incoming: ForceGraphData): ForceGraphData {
  const nodeIds = new Set(prev.nodes.map((n) => n.id));
  const linkIds = new Set(prev.links.map((l) => l.id));
  return {
    nodes: [...prev.nodes, ...incoming.nodes.filter((n) => !nodeIds.has(n.id))],
    links: [...prev.links, ...incoming.links.filter((l) => !linkIds.has(l.id))],
  };
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

  const expandNode = useCallback(
    async (symbol: string, tissue = 'all') => {
      try {
        const resp = await api.getGeneGraph(symbol, tissue, 1);
        setGraphData((prev) => mergeGraph(prev, toForceGraph(resp)));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [],
  );

  return { graphData, loading, error, loadGene, expandNode };
}
