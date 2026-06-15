import { useState } from 'react';

import { useQuery } from '../hooks/useQuery';

interface Props {
  tissue: string;
}

export default function QueryPanel({ tissue }: Props) {
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState('');
  const [showCypher, setShowCypher] = useState(false);
  const { loading, result, error, run } = useQuery();

  const submit = () => {
    if (question.trim()) run(question.trim(), tissue);
  };

  return (
    <div className={`query-panel${open ? ' open' : ''}`}>
      <button className="query-toggle" onClick={() => setOpen((v) => !v)}>
        {open ? '▾ Ask OmniGraph' : '▸ Ask OmniGraph'}
      </button>

      {open && (
        <div className="query-body">
          <div className="query-input-row">
            <input
              className="query-input"
              placeholder="e.g. What TFs regulate TP53?"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && submit()}
            />
            <button className="query-submit" onClick={submit} disabled={loading}>
              {loading ? '…' : 'Ask'}
            </button>
          </div>

          {loading && <div className="query-spinner">Querying the graph…</div>}
          {error && !loading && (
            <div className="query-error">Query failed: {error}</div>
          )}

          {result && !loading && (
            <div className="query-result">
              <div className="query-answer">{result.answer}</div>

              {result.cypher && (
                <div className="query-cypher">
                  <button
                    className="cypher-toggle"
                    onClick={() => setShowCypher((v) => !v)}
                  >
                    {showCypher ? 'Hide' : 'Show'} Cypher
                  </button>
                  {showCypher && <pre className="cypher-code">{result.cypher}</pre>}
                </div>
              )}

              {result.citations.length > 0 && (
                <div className="query-citations">
                  <span className="edge-key">Citations: </span>
                  {result.citations.map((pmid) => (
                    <a
                      key={pmid}
                      href={`https://pubmed.ncbi.nlm.nih.gov/${pmid}`}
                      target="_blank"
                      rel="noreferrer"
                      className="pmid-pill"
                    >
                      {pmid}
                    </a>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
