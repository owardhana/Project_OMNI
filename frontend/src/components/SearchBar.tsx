import { useState } from 'react';

import { useSearch } from '../hooks/useSearch';
import type { SearchResult } from '../types/graph';

interface Props {
  onSelect: (result: SearchResult) => void;
}

const TYPE_LABEL: Record<SearchResult['node_type'], string> = {
  gene: 'Gene',
  transcript: 'Transcript',
  protein: 'Protein',
  variant: 'Variant',
  disease: 'Disease',
};

function displayName(r: SearchResult): string {
  return r.hgnc_symbol ?? r.name ?? r.id;
}

export default function SearchBar({ onSelect }: Props) {
  const { query, setQuery, results, setResults } = useSearch();
  const [open, setOpen] = useState(false);

  const pick = (r: SearchResult) => {
    onSelect(r);
    setQuery('');
    setResults([]);
    setOpen(false);
  };

  return (
    <div className="search-bar">
      <input
        className="search-input"
        placeholder="Search gene, protein, disease…"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            setQuery('');
            setResults([]);
            setOpen(false);
          } else if (e.key === 'Enter' && results.length > 0) {
            pick(results[0]);
          }
        }}
      />
      {open && results.length > 0 && (
        <ul className="search-dropdown">
          {results.map((r) => (
            <li key={`${r.node_type}:${r.id}`}>
              <button className="search-option" onClick={() => pick(r)}>
                <span className="search-symbol">
                  <span className={`type-chip type-${r.node_type}`}>
                    {TYPE_LABEL[r.node_type]}
                  </span>
                  {displayName(r)}
                  {r.is_tf && <span className="tf-badge">TF</span>}
                </span>
                <span className="search-desc">{r.description}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
