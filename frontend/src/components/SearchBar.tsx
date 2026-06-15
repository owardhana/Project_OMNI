import { useState } from 'react';

import { useSearch } from '../hooks/useSearch';

interface Props {
  onSelect: (symbol: string) => void;
}

export default function SearchBar({ onSelect }: Props) {
  const { query, setQuery, results, setResults } = useSearch();
  const [open, setOpen] = useState(false);

  const pick = (symbol: string) => {
    onSelect(symbol);
    setQuery('');
    setResults([]);
    setOpen(false);
  };

  return (
    <div className="search-bar">
      <input
        className="search-input"
        placeholder="Search gene (e.g. TP53, BRCA2)…"
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
            pick(results[0].hgnc_symbol ?? results[0].ensembl_id);
          }
        }}
      />
      {open && results.length > 0 && (
        <ul className="search-dropdown">
          {results.map((r) => (
            <li key={r.ensembl_id}>
              <button
                className="search-option"
                onClick={() => pick(r.hgnc_symbol ?? r.ensembl_id)}
              >
                <span className="search-symbol">
                  {r.hgnc_symbol ?? r.ensembl_id}
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
