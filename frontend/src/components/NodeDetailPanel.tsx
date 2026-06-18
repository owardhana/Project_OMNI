import { useState } from 'react';

import type { FGLink, FGNode, ForceGraphData } from '../types/graph';

interface Props {
  node: FGNode | null;
  graphData: ForceGraphData;
  onClose: () => void;
  onExpand: (node: FGNode) => void;
}

function endId(end: string | FGNode): string {
  return typeof end === 'object' ? end.id : end;
}

// Variants ASSOCIATED_WITH this disease that are present in the current graph.
function variantCount(ontologyId: string, links: FGLink[]): number {
  return links.filter(
    (l) => l.rel_type === 'ASSOCIATED_WITH' && endId(l.target) === ontologyId,
  ).length;
}

export default function NodeDetailPanel({
  node,
  graphData,
  onClose,
  onExpand,
}: Props) {
  const [proteinExpanded, setProteinExpanded] = useState(false);
  if (!node) return null;

  return (
    <aside className="node-panel">
      <button className="node-panel-close" onClick={onClose} aria-label="Close">
        ×
      </button>

      {node.node_type === 'gene' && (
        <>
          <h2 className="node-title">
            {node.hgnc_symbol ?? node.ensembl_id}
            {node.is_tf && <span className="tf-badge">TF gene</span>}
          </h2>
          <div className="node-subtitle">{node.ensembl_id} · Gene</div>
          {node.description && <p className="node-desc">{node.description}</p>}
          <dl className="node-fields">
            <dt>Chromosome</dt>
            <dd>{node.chromosome ?? '—'}</dd>
            <dt>Biotype</dt>
            <dd>{node.biotype ?? '—'}</dd>
            <dt>LoF intolerance</dt>
            <dd>
              {node.pli_score != null ? node.pli_score.toFixed(3) : '—'}
              {node.pli_score != null && node.pli_score > 0.9 && (
                <span className="flag-chip">high</span>
              )}
            </dd>
            {node.cancer_gene && (
              <>
                <dt>Cancer gene</dt>
                <dd>
                  <span className="flag-chip">yes</span>
                </dd>
              </>
            )}
          </dl>
          <button className="expand-btn" onClick={() => onExpand(node)}>
            Expand Neighborhood
          </button>
        </>
      )}

      {node.node_type === 'protein' && (
        <>
          <h2 className="node-title">
            {node.hgnc_symbol ?? node.uniprot_id} (protein)
            {node.subtype === 'transcription_factor' && (
              <span className="tf-badge">TF</span>
            )}
          </h2>
          <div className="node-subtitle">{node.uniprot_id} · UniProt</div>
          {node.summary_text && (
            <p
              className={`node-desc ${proteinExpanded ? '' : 'truncate-3'}`}
              onClick={() => setProteinExpanded((v) => !v)}
              title="Click to expand/collapse"
            >
              {node.summary_text}
            </p>
          )}
          <dl className="node-fields">
            <dt>Subtype</dt>
            <dd>{node.subtype ?? '—'}</dd>
            <dt>Subcellular</dt>
            <dd>{node.subcellular_loc ?? '—'}</dd>
            <dt>Mol. weight</dt>
            <dd>{node.molecular_weight != null ? `${node.molecular_weight} Da` : '—'}</dd>
          </dl>
          {node.go_terms && node.go_terms.length > 0 && (
            <div className="chip-row">
              {node.go_terms.slice(0, 5).map((go) => (
                <span key={go} className="go-chip">
                  {go}
                </span>
              ))}
            </div>
          )}
          <button className="expand-btn" onClick={() => onExpand(node)}>
            Expand Neighborhood
          </button>
        </>
      )}

      {node.node_type === 'transcript' && (
        <>
          <h2 className="node-title">{node.hgnc_symbol ?? node.ensembl_tx_id}</h2>
          <div className="node-subtitle">{node.ensembl_tx_id} · Transcript</div>
          <dl className="node-fields">
            <dt>Biotype</dt>
            <dd>{node.biotype ?? '—'}</dd>
            <dt>Length</dt>
            <dd>{node.length_bp != null ? `${node.length_bp} bp` : '—'}</dd>
          </dl>
          <p className="muted node-hint">Transcript node (transcriptomics layer)</p>
        </>
      )}

      {node.node_type === 'variant' && (
        <>
          <h2 className="node-title">{node.rsid ?? node.id}</h2>
          <div className="node-subtitle">Variant · genomics layer</div>
          <dl className="node-fields">
            <dt>Position</dt>
            <dd>
              {node.chromosome
                ? `chr${node.chromosome}:${node.position_grch38 ?? '?'}`
                : '—'}
            </dd>
            <dt>Clinical significance</dt>
            <dd>{node.clinical_significance ?? '—'}</dd>
            <dt>Consequence</dt>
            <dd>{node.consequence_type ?? '—'}</dd>
            <dt>gnomAD AF</dt>
            <dd>{node.gnomad_af != null ? node.gnomad_af.toExponential(2) : '—'}</dd>
          </dl>
        </>
      )}

      {node.node_type === 'disease' && (
        <>
          <h2 className="node-title">{node.name ?? node.ontology_id}</h2>
          <div className="node-subtitle">{node.ontology_id} · Disease</div>
          {node.description && node.description !== node.name && (
            <p className="node-desc">{node.description}</p>
          )}
          <dl className="node-fields">
            <dt>Category</dt>
            <dd>{node.category ?? '—'}</dd>
            <dt>Associated variants</dt>
            <dd>{variantCount(node.ontology_id, graphData.links)}</dd>
          </dl>
          <p className="muted node-hint">Disease node (phenotype layer)</p>
        </>
      )}
    </aside>
  );
}
