import type { FGLink, FGNode } from '../types/graph';

interface Props {
  link: FGLink | null;
}

function endpointLabel(end: string | FGNode): string {
  if (typeof end === 'object') {
    return end.hgnc_symbol ?? end.id;
  }
  return end;
}

export default function EdgeDetailPanel({ link }: Props) {
  if (!link) return null;

  const weights = link.tissue_weights ?? null;

  return (
    <div className="edge-panel">
      <div className="edge-panel-title">
        {endpointLabel(link.source)} → {endpointLabel(link.target)}
      </div>
      <div className="edge-row">
        <span className="edge-key">Type</span>
        <span>{link.rel_type}</span>
      </div>
      {link.mode && (
        <div className="edge-row">
          <span className="edge-key">Mode</span>
          <span>{link.mode}</span>
        </div>
      )}
      {link.confidence != null && (
        <div className="edge-row">
          <span className="edge-key">Confidence</span>
          <span>
            {link.confidence.toFixed(2)}
            {link.confidence_tier ? ` (tier ${link.confidence_tier})` : ''}
          </span>
        </div>
      )}
      {weights && (
        <div className="edge-weights">
          <div className="edge-key">Tissue expression</div>
          {Object.entries(weights).map(([tissue, value]) => (
            <div key={tissue} className="weight-row">
              <span className="weight-label">{tissue}</span>
              <span className="weight-bar-track">
                <span
                  className="weight-bar-fill"
                  style={{ width: `${Math.round(value * 100)}%` }}
                />
              </span>
              <span className="weight-val">{value.toFixed(2)}</span>
            </div>
          ))}
        </div>
      )}
      <div className="edge-pmids">
        <div className="edge-key">Citations</div>
        {link.pmids.length === 0 ? (
          <span className="muted">
            {link.citation_attempted ? 'None found' : 'Not yet attempted'}
          </span>
        ) : (
          <ul className="pmid-list">
            {link.pmids.map((pmid) => (
              <li key={pmid}>
                <a
                  href={`https://pubmed.ncbi.nlm.nih.gov/${pmid}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  PMID {pmid}
                </a>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
