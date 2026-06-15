import { useEffect, useRef } from 'react';
import ForceGraph3D from 'react-force-graph-3d';
import * as THREE from 'three';

import {
  LAYERS,
  edgeColor,
  nodeColor,
  nodeLayer,
  nodeSize,
} from '../styles/layers';
import type { FGLink, FGNode, ForceGraphData } from '../types/graph';

interface Props {
  data: ForceGraphData;
  visibleLayers: Record<string, boolean>;
  onNodeClick: (node: FGNode) => void;
  onBackgroundClick: () => void;
  onEdgeHover: (link: FGLink | null) => void;
}

const PLANE_SIZE = 1100;
// Side-profile camera: offset mostly along +X with a slight elevation, looking
// at the origin. The layer-separation axis (world Z) then runs across the
// screen so the two layers read as distinct stacked clouds, not top-down.
const CAMERA = { x: 820, y: 150, z: 0 };

function resolveEndpoint(end: string | FGNode): FGNode | null {
  return typeof end === 'object' ? end : null;
}

export default function GraphViewer3D({
  data,
  visibleLayers,
  onNodeClick,
  onBackgroundClick,
  onEdgeHover,
}: Props) {
  // Loose ref: react-force-graph exposes scene()/camera()/d3Force() at runtime.
  const fgRef = useRef<any>(null);
  const planesRef = useRef<Record<string, THREE.Mesh>>({});
  const initRef = useRef(false);

  // Add the two semi-transparent layer planes on mount; remove on unmount.
  useEffect(() => {
    const scene = fgRef.current?.scene?.();
    if (!scene) return;
    const added: THREE.Mesh[] = [];
    (Object.keys(LAYERS) as (keyof typeof LAYERS)[]).forEach((key) => {
      const layer = LAYERS[key];
      const geometry = new THREE.PlaneGeometry(PLANE_SIZE, PLANE_SIZE);
      const material = new THREE.MeshBasicMaterial({
        color: layer.color,
        transparent: true,
        opacity: 0.05,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.z = layer.z;
      scene.add(mesh);
      planesRef.current[key] = mesh;
      added.push(mesh);
    });
    return () => {
      added.forEach((mesh) => {
        scene.remove(mesh);
        mesh.geometry.dispose();
        (mesh.material as THREE.Material).dispose();
      });
      planesRef.current = {};
    };
  }, []);

  // Toggle plane visibility with their layer.
  useEffect(() => {
    Object.entries(planesRef.current).forEach(([key, mesh]) => {
      mesh.visible = visibleLayers[key] ?? true;
    });
  }, [visibleLayers]);

  // Once the first graph arrives: loosen charge for a roomy X/Y spread within
  // each layer (Z is pinned per-node via fz = layer target), then swing the
  // camera to the side profile. Runs once so manual rotation is preserved after.
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg || data.nodes.length === 0 || initRef.current) return;
    initRef.current = true;

    const charge = fg.d3Force('charge');
    if (charge?.strength) charge.strength(-55);
    fg.d3ReheatSimulation?.();

    const timer = setTimeout(() => {
      fg.cameraPosition?.(CAMERA, { x: 0, y: 0, z: 0 }, 2200);
    }, 1400);
    return () => clearTimeout(timer);
  }, [data]);

  // Dev-only test hook: expose the force-graph instance + current data so an
  // end-to-end script can map 3D nodes to screen coordinates. Harmless in prod.
  useEffect(() => {
    if (import.meta.env.DEV) {
      (window as unknown as Record<string, unknown>).__omniFG = fgRef.current;
      (window as unknown as Record<string, unknown>).__omniData = data;
    }
  }, [data]);

  return (
    <ForceGraph3D
      ref={fgRef}
      graphData={data}
      backgroundColor="#070b16"
      showNavInfo={false}
      nodeId="id"
      nodeResolution={16}
      nodeOpacity={0.92}
      nodeRelSize={4}
      nodeLabel={(n: FGNode) =>
        n.node_type === 'gene'
          ? `${n.hgnc_symbol ?? n.ensembl_id}${n.is_tf ? ' · TF' : ' · Gene'}`
          : `${n.hgnc_symbol ?? n.ensembl_tx_id} · Transcript`
      }
      nodeColor={(n: FGNode) => nodeColor(n)}
      nodeVal={(n: FGNode) => nodeSize(n)}
      nodeVisibility={(n: FGNode) => visibleLayers[nodeLayer(n)] ?? true}
      linkVisibility={(l: FGLink) => {
        const s = resolveEndpoint(l.source);
        const t = resolveEndpoint(l.target);
        if (!s || !t) return true;
        return (
          (visibleLayers[nodeLayer(s)] ?? true) &&
          (visibleLayers[nodeLayer(t)] ?? true)
        );
      }}
      linkColor={(l: FGLink) => edgeColor(l)}
      linkOpacity={0.45}
      linkWidth={0.6}
      linkDirectionalArrowLength={2.5}
      linkDirectionalArrowRelPos={1}
      linkDirectionalParticles={2}
      linkDirectionalParticleWidth={1.6}
      linkDirectionalParticleSpeed={0.006}
      linkDirectionalParticleColor={(l: FGLink) => edgeColor(l)}
      onNodeClick={(n: FGNode) => onNodeClick(n)}
      onBackgroundClick={() => onBackgroundClick()}
      onLinkHover={(l: FGLink | null) => onEdgeHover(l)}
    />
  );
}
