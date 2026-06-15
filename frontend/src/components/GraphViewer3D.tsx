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

const PLANE_SIZE = 900;

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
  // Loose ref: react-force-graph exposes scene()/camera() at runtime.
  const fgRef = useRef<any>(null);
  const planesRef = useRef<Record<string, THREE.Mesh>>({});

  // Add the two semi-transparent layer planes to the scene on mount, and remove
  // them on unmount (empty deps + cleanup so StrictMode's remount re-adds them to
  // the live scene and disposes the discarded ones).
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
        opacity: 0.06,
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
      backgroundColor="#0b0f1a"
      nodeId="id"
      nodeLabel={(n: FGNode) =>
        n.node_type === 'gene'
          ? `${n.hgnc_symbol ?? n.ensembl_id}${n.is_tf ? ' (TF)' : ''}`
          : `${n.hgnc_symbol ?? n.ensembl_tx_id}`
      }
      nodeColor={(n: FGNode) => nodeColor(n)}
      nodeVal={(n: FGNode) => nodeSize(n)}
      nodeOpacity={0.95}
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
      linkOpacity={0.55}
      linkWidth={1}
      linkDirectionalArrowLength={3.5}
      linkDirectionalArrowRelPos={1}
      onNodeClick={(n: FGNode) => onNodeClick(n)}
      onBackgroundClick={() => onBackgroundClick()}
      onLinkHover={(l: FGLink | null) => onEdgeHover(l)}
    />
  );
}
