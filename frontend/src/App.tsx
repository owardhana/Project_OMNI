import { useEffect, useState } from 'react';

import './App.css';
import { api, DEFAULT_GENE } from './api/client';
import EdgeDetailPanel from './components/EdgeDetailPanel';
import EntityBrowser from './components/EntityBrowser';
import GraphViewer3D from './components/GraphViewer3D';
import LayerToggle from './components/LayerToggle';
import NodeDetailPanel from './components/NodeDetailPanel';
import QueryPanel from './components/QueryPanel';
import SearchBar from './components/SearchBar';
import TissueFilter from './components/TissueFilter';
import { useGraph } from './hooks/useGraph';
import type { LayerKey } from './styles/layers';
import type { FGLink, FGNode, GraphWarning, SearchResult } from './types/graph';

type Seed = { id: string; node_type: string };

export default function App() {
  const {
    graphData,
    loading,
    error,
    loadGene,
    loadDisease,
    expandNode,
    mergeInto,
    clearGraph,
  } = useGraph();
  const [warnings, setWarnings] = useState<GraphWarning[]>([]);
  const [multiSeeds, setMultiSeeds] = useState<Seed[]>([]);
  const [pathNote, setPathNote] = useState<string | null>(null);
  const [currentEntity, setCurrentEntity] = useState(DEFAULT_GENE);
  const [activeTissue, setActiveTissue] = useState('all');
  const [visibleLayers, setVisibleLayers] = useState<Record<string, boolean>>({
    genomics: true,
    transcriptomics: true,
    proteomics: true,
    phenotype: true,
  });
  const [selectedNode, setSelectedNode] = useState<FGNode | null>(null);
  const [hoveredEdge, setHoveredEdge] = useState<FGLink | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<FGLink | null>(null);

  // Initial load: DEFAULT_GENE (TP53) neighborhood.
  useEffect(() => {
    loadGene(DEFAULT_GENE);
  }, [loadGene]);

  const handleSelect = (r: SearchResult) => {
    setSelectedNode(null);
    setSelectedEdge(null);
    if (r.node_type === 'disease') {
      setCurrentEntity(r.name ?? r.id);
      loadDisease(r.id); // /api/disease/{ontology_id}/graph
    } else {
      // gene / protein / transcript all resolve via the gene graph by symbol.
      const symbol = r.hgnc_symbol ?? r.id;
      setCurrentEntity(symbol);
      loadGene(symbol);
    }
  };

  // Tissue is a render-time opacity channel (ADR-0006) — no refetch.
  const handleTissueChange = (tissue: string) => setActiveTissue(tissue);

  const handleToggleLayer = (layer: LayerKey) => {
    setVisibleLayers((prev) => ({ ...prev, [layer]: !(prev[layer] ?? true) }));
  };

  const handleExpand = (node: FGNode) => {
    // Both genes and TF proteins carry a symbol that resolves to a gene graph.
    if (node.hgnc_symbol) expandNode(node.hgnc_symbol);
  };

  // Entity-browser multi-load: merge each seed's subgraph additively; surface
  // any disconnected-cluster warning.
  const handleMultiLoad = async (seeds: Seed[]) => {
    setPathNote(null);
    try {
      const resp = await api.getMultiGraph(
        seeds.map((s) => s.id),
        seeds.map((s) => s.node_type),
      );
      mergeInto(resp);
      setMultiSeeds(seeds);
      setWarnings(resp.warnings ?? []);
    } catch (e) {
      setWarnings([{ type: 'error', message: String(e) }]);
    }
  };

  const handleClear = () => {
    clearGraph();
    setWarnings([]);
    setMultiSeeds([]);
    setSelectedNode(null);
    setSelectedEdge(null);
    setPathNote(null);
  };

  const handleFindPath = async (a: Seed, b: Seed) => {
    setPathNote(`Finding path ${a.id} → ${b.id}…`);
    try {
      const p = await api.getPath(a.id, a.node_type, b.id, b.node_type);
      if (p.path_found) {
        mergeInto({ nodes: p.nodes, edges: p.edges });
        setPathNote(`Path ${a.id} → ${b.id}: ${p.hop_count} hops (${p.path_quality})`);
      } else {
        setPathNote(
          'No path found within 6 hops. These entities may not be directly connected at current data resolution.',
        );
      }
    } catch (e) {
      setPathNote(String(e));
    }
  };

  const disconnected = warnings.some((w) => w.type === 'disconnected');
  // Up to 3 seed pairs to offer "Find path" between.
  const seedPairs: [Seed, Seed][] = [];
  for (let i = 0; i < multiSeeds.length && seedPairs.length < 3; i++) {
    for (let j = i + 1; j < multiSeeds.length && seedPairs.length < 3; j++) {
      seedPairs.push([multiSeeds[i], multiSeeds[j]]);
    }
  }

  return (
    <div className="app">
      <GraphViewer3D
        data={graphData}
        visibleLayers={visibleLayers}
        activeTissue={activeTissue}
        selectedEdgeId={selectedEdge?.id ?? null}
        onNodeClick={(n) => {
          setSelectedNode(n);
          setSelectedEdge(null);
        }}
        onBackgroundClick={() => {
          setSelectedNode(null);
          setSelectedEdge(null);
        }}
        onEdgeHover={setHoveredEdge}
        onEdgeClick={setSelectedEdge}
      />

      <EntityBrowser onMultiLoad={handleMultiLoad} onClear={handleClear} />

      {(disconnected || pathNote) && (
        <div className="graph-banner">
          {disconnected && (
            <span className="banner-msg">
              {warnings.find((w) => w.type === 'disconnected')?.message}
            </span>
          )}
          {disconnected &&
            seedPairs.map(([a, b], i) => (
              <button
                key={i}
                className="banner-btn"
                onClick={() => handleFindPath(a, b)}
              >
                Find path: {a.id} ↔ {b.id}
              </button>
            ))}
          {pathNote && <span className="path-note">{pathNote}</span>}
        </div>
      )}

      <header className="topbar">
        <div className="topbar-left">
          <div className="brand">OmniGraph</div>
          <SearchBar onSelect={handleSelect} />
        </div>
        <LayerToggle visibleLayers={visibleLayers} onToggle={handleToggleLayer} />
        <TissueFilter active={activeTissue} onChange={handleTissueChange} />
      </header>

      <div className="status-line">
        {loading && <span>Loading {currentEntity}…</span>}
        {error && <span className="status-error">Error: {error}</span>}
        {!loading && !error && (
          <span>
            {currentEntity}: {graphData.nodes.length} nodes, {graphData.links.length} edges
          </span>
        )}
      </div>

      <div className="legend">
        <span><i className="dot gene" /> Gene</span>
        <span><i className="dot protein" /> TF protein</span>
        <span><i className="dot transcript" /> Transcript</span>
        <span><i className="dot variant" /> Variant</span>
        <span><i className="dot disease" /> Disease</span>
      </div>

      <NodeDetailPanel
        node={selectedNode}
        graphData={graphData}
        onClose={() => setSelectedNode(null)}
        onExpand={handleExpand}
      />

      {/* Pinned (clicked) edge takes precedence over the hovered one. */}
      <EdgeDetailPanel
        link={selectedEdge ?? hoveredEdge}
        onClose={selectedEdge ? () => setSelectedEdge(null) : undefined}
      />

      <QueryPanel tissue={activeTissue} />
    </div>
  );
}
