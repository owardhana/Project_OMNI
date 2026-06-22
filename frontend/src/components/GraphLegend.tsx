import { useMemo, useState } from 'react';

import { EDGE_COLORS, NODE_COLORS } from '../styles/layers';
import type { ForceGraphData } from '../types/graph';

// Collapsible bottom-right legend that auto-updates from the node + edge types
// actually present in the current graph (8c). Includes the metabolite swatch.
const NODE_LABEL: Record<string, string> = {
  gene: 'Gene',
  transcript: 'Transcript',
  protein: 'Protein',
  variant: 'Variant',
  metabolite: 'Metabolite',
  disease: 'Disease',
};

const NODE_SWATCH: Record<string, string> = {
  gene: NODE_COLORS.gene,
  transcript: NODE_COLORS.transcript,
  protein: NODE_COLORS.protein,
  variant: NODE_COLORS.variant,
  metabolite: NODE_COLORS.metabolite,
  disease: NODE_COLORS.disease,
};

const EDGE_LABEL: Record<string, { label: string; color: string }> = {
  REGULATES: { label: 'Regulates', color: EDGE_COLORS.activator },
  PRODUCES: { label: 'Produces', color: EDGE_COLORS.produces },
  TRANSLATES_TO: { label: 'Translates', color: EDGE_COLORS.translates },
  ENCODES: { label: 'Encodes', color: EDGE_COLORS.encodes },
  INTERACTS_WITH: { label: 'Interacts', color: EDGE_COLORS.interacts_with },
  ASSOCIATED_WITH: { label: 'Associated', color: EDGE_COLORS.associated_with },
  IN_GENE: { label: 'In gene', color: EDGE_COLORS.in_gene },
  IMPLICATED_IN: { label: 'Implicated', color: EDGE_COLORS.implicated_in },
  CATALYSES: { label: 'Catalyses', color: EDGE_COLORS.catalyses },
  DIFFERENTIALLY_EXPRESSED: {
    label: 'Diff. expressed',
    color: EDGE_COLORS.differentially_expressed,
  },
};

export default function GraphLegend({ data }: { data: ForceGraphData }) {
  const [open, setOpen] = useState(true);

  const { nodeTypes, hasTF, edgeTypes } = useMemo(() => {
    const nt = new Set<string>();
    let tf = false;
    for (const n of data.nodes) {
      nt.add(n.node_type);
      if (n.node_type === 'protein' && n.subtype === 'transcription_factor') tf = true;
    }
    const et = new Set<string>();
    for (const l of data.links) et.add(l.rel_type);
    return { nodeTypes: [...nt], hasTF: tf, edgeTypes: [...et] };
  }, [data]);

  if (data.nodes.length === 0) return null;

  return (
    <div className={`legend ${open ? '' : 'legend-collapsed'}`}>
      <button
        className="legend-head"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span>Legend</span>
        <span className="legend-caret">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="legend-body">
          <div className="legend-group">
            {hasTF && (
              <span className="legend-item">
                <i className="dot" style={{ background: NODE_COLORS.protein_tf }} />
                TF protein
              </span>
            )}
            {nodeTypes.map((t) => (
              <span key={t} className="legend-item">
                <i className="dot" style={{ background: NODE_SWATCH[t] ?? '#9ca3af' }} />
                {NODE_LABEL[t] ?? t}
              </span>
            ))}
          </div>
          {edgeTypes.length > 0 && (
            <div className="legend-group legend-edges">
              {edgeTypes
                .filter((e) => EDGE_LABEL[e])
                .map((e) => (
                  <span key={e} className="legend-item">
                    <i className="edge-swatch" style={{ background: EDGE_LABEL[e].color }} />
                    {EDGE_LABEL[e].label}
                  </span>
                ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
