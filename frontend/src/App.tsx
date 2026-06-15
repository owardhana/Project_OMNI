import { useEffect, useState } from 'react';

import './App.css';
import { DEFAULT_GENE } from './api/client';
import EdgeDetailPanel from './components/EdgeDetailPanel';
import GraphViewer3D from './components/GraphViewer3D';
import LayerToggle from './components/LayerToggle';
import NodeDetailPanel from './components/NodeDetailPanel';
import QueryPanel from './components/QueryPanel';
import SearchBar from './components/SearchBar';
import TissueFilter from './components/TissueFilter';
import { useGraph } from './hooks/useGraph';
import type { LayerKey } from './styles/layers';
import type { FGLink, FGNode } from './types/graph';

export default function App() {
  const { graphData, loading, error, loadGene, expandNode } = useGraph();
  const [currentGene, setCurrentGene] = useState(DEFAULT_GENE);
  const [activeTissue, setActiveTissue] = useState('all');
  const [visibleLayers, setVisibleLayers] = useState<Record<string, boolean>>({
    genomics: true,
    transcriptomics: true,
  });
  const [selectedNode, setSelectedNode] = useState<FGNode | null>(null);
  const [hoveredEdge, setHoveredEdge] = useState<FGLink | null>(null);

  // Initial load: DEFAULT_GENE (TP53) neighborhood.
  useEffect(() => {
    loadGene(DEFAULT_GENE, 'all');
  }, [loadGene]);

  const handleSelectGene = (symbol: string) => {
    setCurrentGene(symbol);
    setSelectedNode(null);
    loadGene(symbol, activeTissue);
  };

  const handleTissueChange = (tissue: string) => {
    setActiveTissue(tissue);
    loadGene(currentGene, tissue);
  };

  const handleToggleLayer = (layer: LayerKey) => {
    setVisibleLayers((prev) => ({ ...prev, [layer]: !(prev[layer] ?? true) }));
  };

  const handleExpand = (node: FGNode) => {
    if (node.node_type === 'gene' && node.hgnc_symbol) {
      expandNode(node.hgnc_symbol, activeTissue);
    }
  };

  return (
    <div className="app">
      <GraphViewer3D
        data={graphData}
        visibleLayers={visibleLayers}
        onNodeClick={setSelectedNode}
        onBackgroundClick={() => setSelectedNode(null)}
        onEdgeHover={setHoveredEdge}
      />

      <header className="topbar">
        <div className="topbar-left">
          <div className="brand">OmniGraph</div>
          <SearchBar onSelect={handleSelectGene} />
        </div>
        <LayerToggle visibleLayers={visibleLayers} onToggle={handleToggleLayer} />
        <TissueFilter active={activeTissue} onChange={handleTissueChange} />
      </header>

      <div className="status-line">
        {loading && <span>Loading {currentGene}…</span>}
        {error && <span className="status-error">Error: {error}</span>}
        {!loading && !error && (
          <span>
            {currentGene}: {graphData.nodes.length} nodes, {graphData.links.length} edges
          </span>
        )}
      </div>

      <div className="legend">
        <span><i className="dot tf" /> TF</span>
        <span><i className="dot gene" /> Gene</span>
        <span><i className="dot transcript" /> Transcript</span>
      </div>

      <NodeDetailPanel
        node={selectedNode}
        onClose={() => setSelectedNode(null)}
        onExpand={handleExpand}
      />

      <EdgeDetailPanel link={hoveredEdge} />

      <QueryPanel tissue={activeTissue} />
    </div>
  );
}
