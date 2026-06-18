import { useEffect, useRef, useState } from 'react';
import ForceGraph3D from 'react-force-graph-3d';
import { forceCollide } from 'd3-force-3d';
import * as THREE from 'three';
import { FlyControls } from 'three/examples/jsm/controls/FlyControls.js';

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
  activeTissue: string;
  selectedEdgeId: string | null;
  onNodeClick: (node: FGNode) => void;
  onBackgroundClick: () => void;
  onEdgeHover: (link: FGLink | null) => void;
  onEdgeClick: (link: FGLink | null) => void;
}

const PLANE_SIZE = 1400;
// Front camera, slightly elevated, looking at the origin: the vertical (Y) axis
// carries the layer stack, so genomics reads at the bottom, proteomics at the
// top. The orbit controls below soft-lock the polar angle so it never flips.
const CAMERA = { x: 220, y: 260, z: 1050 };
// Soft-lock: orbit freely in azimuth (spin the web), tilt between a slight
// overhead and level, but never past the equator — so genomics can never appear
// above proteomics.
const MIN_POLAR = 0.6;
const MAX_POLAR = Math.PI / 2;

// Map the UI tissue toggle to a tissue_weights key (ADR-0006: opacity, not filter).
const TISSUE_KEY: Record<string, string> = {
  blood: 'whole_blood',
  liver: 'liver',
  brain: 'brain_prefrontal_cortex',
};

function resolveEndpoint(end: string | FGNode): FGNode | null {
  return typeof end === 'object' ? end : null;
}

function hexToRgba(hex: string, alpha: number): string {
  const h = hex.replace('#', '');
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`;
}

// PRODUCES edges dim continuously by the active tissue's weight (never removed —
// ADR-0006). All other edges keep a base opacity regardless of tissue.
function linkAlpha(link: FGLink, activeTissue: string): number {
  const base = 0.5;
  if (activeTissue === 'all' || link.rel_type !== 'PRODUCES') return base;
  const key = TISSUE_KEY[activeTissue];
  const tw = link.tissue_weights?.[key];
  if (tw == null) return 0.12; // no data -> faint, still visible
  return Math.max(0.12, Math.min(1, tw));
}

export default function GraphViewer3D({
  data,
  visibleLayers,
  activeTissue,
  selectedEdgeId,
  onNodeClick,
  onBackgroundClick,
  onEdgeHover,
  onEdgeClick,
}: Props) {
  const fgRef = useRef<any>(null);
  const planesRef = useRef<Record<string, THREE.Mesh>>({});
  const initRef = useRef(false);
  const [cameraMode, setCameraMode] = useState<'orbit' | 'fly'>('orbit');
  const flyRef = useRef<FlyControls | null>(null);
  const flyRafRef = useRef<number | null>(null);
  const flyClockRef = useRef<THREE.Clock | null>(null); // lazy: don't alloc per render

  // Explicitly size the canvas to the viewport. The chrome is all
  // position:absolute, so the ForceGraph3D container has no intrinsic height —
  // without this the WebGL canvas collapses to 0x0 and nothing renders. The
  // `|| default` guards a degenerate 0 (a graph must never render into 0x0).
  const measure = () => ({
    w: (typeof window !== 'undefined' && window.innerWidth) || 1280,
    h: (typeof window !== 'undefined' && window.innerHeight) || 800,
  });
  const [dims, setDims] = useState(measure);
  useEffect(() => {
    const onResize = () => setDims(measure());
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  // Press 'f'/'F' to toggle Orbit <-> Fly; Esc returns to Orbit. Ignore when
  // typing into an input so the search/query boxes still work.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      // Toggle on 'c' (camera) — NOT 'f': FlyControls binds F to "move down", so
      // a shared key would exit fly-mode every time you tried to descend.
      if (e.key === 'c' || e.key === 'C') {
        setCameraMode((m) => (m === 'orbit' ? 'fly' : 'orbit'));
      } else if (e.key === 'Escape') {
        setCameraMode('orbit');
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  // Fly mode: disable OrbitControls and drive a FlyControls update loop. WASD =
  // move, R/F = up/down, mouse drag = look (dragToLook). Esc/C return to orbit.
  useEffect(() => {
    const fg = fgRef.current;
    const camera = fg?.camera?.();
    const renderer = fg?.renderer?.();
    const orbit = fg?.controls?.();
    if (!camera || !renderer) return;

    if (cameraMode === 'fly') {
      if (orbit) orbit.enabled = false;
      const fly = new FlyControls(camera, renderer.domElement);
      fly.movementSpeed = 80;
      // rollSpeed also scales drag-look sensitivity; 0.005 made dragging feel
      // dead. ~0.6 gives a responsive free-look without spinning.
      fly.rollSpeed = 0.6;
      fly.dragToLook = true;
      flyRef.current = fly;
      if (!flyClockRef.current) flyClockRef.current = new THREE.Clock();
      flyClockRef.current.start();
      const tick = () => {
        flyRef.current?.update(flyClockRef.current!.getDelta());
        flyRafRef.current = requestAnimationFrame(tick);
      };
      flyRafRef.current = requestAnimationFrame(tick);
    } else if (orbit) {
      orbit.enabled = true;
    }

    return () => {
      if (flyRafRef.current != null) cancelAnimationFrame(flyRafRef.current);
      flyRafRef.current = null;
      flyRef.current?.dispose?.();
      flyRef.current = null;
    };
  }, [cameraMode]);

  // Semi-transparent layer planes, one per omics layer.
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
        opacity: 0.04,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.rotation.x = -Math.PI / 2; // lay flat: a horizontal floor per layer
      mesh.position.y = layer.y;
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

  useEffect(() => {
    Object.entries(planesRef.current).forEach(([key, mesh]) => {
      mesh.visible = visibleLayers[key] ?? true;
    });
  }, [visibleLayers]);

  // react-force-graph caches accessor results; toggling a layer or changing the
  // tissue won't re-run linkVisibility/linkColor/node accessors until a redraw.
  // refresh() forces it — fixes both the layer-toggle "edges persist" bug (#4)
  // and live tissue opacity (#3, ADR-0006).
  useEffect(() => {
    fgRef.current?.refresh?.();
  }, [visibleLayers, activeTissue, selectedEdgeId]);

  // Tune the force layout once data first arrives: stronger repulsion + collision
  // + longer links spread the graph into a legible web rather than a clump (#5).
  // NOTE: these are starting-point values — fine-tune in a real browser, since the
  // force simulation (rAF-driven) can't run in the headless preview.
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg || data.nodes.length === 0 || initRef.current) return;
    initRef.current = true;

    fg.d3Force('charge')?.strength(-200);
    fg.d3Force('link')?.distance(70);
    fg.d3Force('collision', forceCollide((n: FGNode) => nodeSize(n) + 8));
    fg.d3Force('center')?.strength(0.05); // let the web breathe
    fg.d3ReheatSimulation?.();

    // Soft-lock the orbit controls so the layer stack never flips vertically.
    const controls = fg.controls?.();
    if (controls) {
      controls.minPolarAngle = MIN_POLAR;
      controls.maxPolarAngle = MAX_POLAR;
      controls.enableDamping = true;
      controls.dampingFactor = 0.12;
    }
    // Frame the stack immediately (warmupTicks already pre-spread the layout).
    fg.cameraPosition?.(CAMERA, { x: 0, y: 0, z: 0 }, 0);
  }, [data]);

  useEffect(() => {
    if (import.meta.env.DEV) {
      (window as unknown as Record<string, unknown>).__omniFG = fgRef.current;
      (window as unknown as Record<string, unknown>).__omniData = data;
    }
  }, [data]);

  return (
    <>
    <ForceGraph3D
      ref={fgRef}
      graphData={data}
      width={dims.w}
      height={dims.h}
      backgroundColor="#050508"
      showNavInfo={false}
      controlType="orbit"
      warmupTicks={100}
      cooldownTicks={200}
      nodeId="id"
      nodeResolution={16}
      nodeOpacity={0.95}
      nodeRelSize={4}
      nodeLabel={(n: FGNode) => {
        if (n.node_type === 'gene') return `${n.hgnc_symbol ?? n.ensembl_id} · Gene`;
        if (n.node_type === 'protein')
          return `${n.hgnc_symbol ?? n.uniprot_id} (protein)${n.subtype === 'transcription_factor' ? ' · TF' : ''}`;
        if (n.node_type === 'transcript')
          return `${n.hgnc_symbol ?? n.ensembl_tx_id} · Transcript`;
        if (n.node_type === 'variant')
          return `${n.rsid ?? n.id} · Variant${n.clinical_significance ? ' · ' + n.clinical_significance : ''}`;
        return `${n.name ?? n.ontology_id} · Disease`;
      }}
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
      linkColor={(l: FGLink) => hexToRgba(edgeColor(l), linkAlpha(l, activeTissue))}
      linkOpacity={1}
      // INTERACTS_WITH (intra-layer PPI) renders thinner than inter-layer edges.
      linkWidth={(l: FGLink) => (l.rel_type === 'INTERACTS_WITH' ? 0.8 * 0.6 : 0.8)}
      linkDirectionalArrowLength={2.8}
      linkDirectionalArrowRelPos={1}
      linkCurvature={0.12}
      // Moving flow particles only on the clicked (selected) edge.
      linkDirectionalParticles={(l: FGLink) => (l.id === selectedEdgeId ? 4 : 0)}
      linkDirectionalParticleWidth={2.2}
      linkDirectionalParticleSpeed={0.0015}
      linkDirectionalParticleColor={(l: FGLink) => hexToRgba(edgeColor(l), 1)}
      onNodeClick={(n: FGNode) => onNodeClick(n)}
      onBackgroundClick={() => onBackgroundClick()}
      onLinkHover={(l: FGLink | null) => onEdgeHover(l)}
      onLinkClick={(l: FGLink | null) => onEdgeClick(l)}
    />
    <div
      data-testid="camera-hud"
      style={{
        position: 'absolute',
        bottom: 16,
        left: 16,
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        fontSize: 11,
        letterSpacing: '0.08em',
        color: cameraMode === 'fly' ? '#f59e0b' : '#9ca3af',
        background: 'rgba(5, 5, 8, 0.6)',
        border: '1px solid rgba(255,255,255,0.08)',
        padding: '4px 9px',
        borderRadius: 4,
        pointerEvents: 'none',
        zIndex: 20,
      }}
    >
      {cameraMode === 'fly'
        ? 'FLY MODE · WASD move · R/F up·down · drag look · C/Esc orbit'
        : 'ORBIT · press C to fly'}
    </div>
    </>
  );
}
