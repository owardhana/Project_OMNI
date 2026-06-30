import { useEffect, useState } from 'react';

import './App.css';
import { api, DEFAULT_GENE } from './api/client';
import EdgeDetailPanel from './components/EdgeDetailPanel';
import EntityBrowser from './components/EntityBrowser';
import GraphLegend from './components/GraphLegend';
import GraphViewer3D from './components/GraphViewer3D';
import LayerToggle from './components/LayerToggle';
import NodeDetailPanel from './components/NodeDetailPanel';
import ChatPanel from './components/ChatPanel';
import QueryPanel from './components/QueryPanel';
import SearchBar from './components/SearchBar';
import ShortcutsOverlay from './components/ShortcutsOverlay';
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
    loadMetabolite,
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
    metabolomics: true,
    phenotype: true,
  });
  const [selectedNode, setSelectedNode] = useState<FGNode | null>(null);
  const [hoveredEdge, setHoveredEdge] = useState<FGLink | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<FGLink | null>(null);
  const [cameraMode, setCameraMode] = useState<'orbit' | 'fly'>('orbit');
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  // First-visit hint that the '?' overlay exists (shown once, then dismissed).
  const [showHint, setShowHint] = useState(
    () => localStorage.getItem('shortcuts_shown') !== '1',
  );

  // Initial load: DEFAULT_GENE (TP53) neighborhood.
  useEffect(() => {
    loadGene(DEFAULT_GENE);
  }, [loadGene]);

  // '?' toggles the shortcut overlay; Esc closes overlay/panels. Gated on inputs
  // so typing in search/query boxes still works. (GraphViewer3D owns C/F/Esc for
  // the camera; this listener is additive — Esc here also clears selection.)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      if (e.key === '?') {
        setShortcutsOpen((v) => !v);
        setShowHint(false);
        localStorage.setItem('shortcuts_shown', '1');
      } else if (e.key === 'Escape') {
        setShortcutsOpen(false);
        setSelectedNode(null);
        setSelectedEdge(null);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  const dismissHint = () => {
    setShowHint(false);
    localStorage.setItem('shortcuts_shown', '1');
  };

  const handleSelect = (r: SearchResult) => {
    setSelectedNode(null);
    setSelectedEdge(null);
    if (r.node_type === 'disease') {
      setCurrentEntity(r.name ?? r.id);
      loadDisease(r.id); // /api/disease/{ontology_id}/graph
    } else if (r.node_type === 'metabolite') {
      setCurrentEntity(r.name ?? r.id);
      loadMetabolite(r.id); // /api/metabolite/{hmdb_id|chebi_id}/graph
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
    // Both genes and TF proteins carry a symbol that resolves to a gene graph;
    // variant/metabolite/disease nodes have no hgnc_symbol, so guard the access.
    const sym = 'hgnc_symbol' in node ? node.hgnc_symbol : undefined;
    if (sym) expandNode(sym);
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
        onCameraModeChange={setCameraMode}
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

      {error && (
        <div className="error-banner" role="alert">
          <span className="error-icon" aria-hidden>⚠</span>
          <span>Failed to load {currentEntity}: {error}</span>
        </div>
      )}

      <GraphLegend data={graphData} />

      {/* Thin bottom status bar: graph metrics · active seed(s) · camera mode. */}
      <div className="status-bar">
        {loading ? (
          <span className="skeleton-line" aria-label={`Loading ${currentEntity}`} />
        ) : (
          <span className="status-metric">
            <strong>{graphData.nodes.length}</strong> nodes ·{' '}
            <strong>{graphData.links.length}</strong> edges
          </span>
        )}
        <span className="status-sep">·</span>
        <span className="status-seed">
          {multiSeeds.length > 0
            ? multiSeeds.map((s) => s.id).join(', ')
            : currentEntity}
        </span>
        <span className="status-spacer" />
        <span className={`status-cam ${cameraMode}`}>{cameraMode.toUpperCase()}</span>
      </div>

      {showHint && (
        <button className="shortcut-hint" onClick={dismissHint}>
          Press <kbd className="kbd">?</kbd> for keyboard shortcuts
        </button>
      )}
      {shortcutsOpen && <ShortcutsOverlay onClose={() => setShortcutsOpen(false)} />}

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
      <ChatPanel tissue={activeTissue} />
    </div>
  );
}
