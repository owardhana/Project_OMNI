import type { FGNode } from '../types/graph';

interface Props {
  node: FGNode | null;
  onClose: () => void;
  onExpand: (node: FGNode) => void;
}

export default function NodeDetailPanel({ node, onClose, onExpand }: Props) {
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
          <dl className="node-fields">
            <dt>Subtype</dt>
            <dd>{node.subtype ?? '—'}</dd>
          </dl>
          <button className="expand-btn" onClick={() => onExpand(node)}>
            Expand Neighborhood
          </button>
          <p className="muted node-hint">Protein node (proteomics layer)</p>
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
    </aside>
  );
}
