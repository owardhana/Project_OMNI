import { useEffect, useRef, useState } from 'react';

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
  metabolite: 'Metabolite',
  disease: 'Disease',
};

function displayName(r: SearchResult): string {
  return r.hgnc_symbol ?? r.name ?? r.id;
}

export default function SearchBar({ onSelect }: Props) {
  const { query, setQuery, results, setResults } = useSearch();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1); // keyboard-highlighted result
  const inputRef = useRef<HTMLInputElement>(null);

  // '/' focuses the search box (unless already typing in a field).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      if (e.key === '/') {
        e.preventDefault();
        inputRef.current?.focus();
        setOpen(true);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  const pick = (r: SearchResult) => {
    onSelect(r);
    setQuery('');
    setResults([]);
    setOpen(false);
    setActive(-1);
  };

  const clear = () => {
    setQuery('');
    setResults([]);
    setOpen(false);
    setActive(-1);
  };

  return (
    <div className="search-bar">
      <div className="search-bar-input-wrap">
        <input
          ref={inputRef}
          className="search-input"
          placeholder="Search gene, protein, disease…"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
            setActive(-1);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              clear();
            } else if (e.key === 'ArrowDown' && results.length > 0) {
              e.preventDefault();
              setActive((i) => Math.min(i + 1, results.length - 1));
            } else if (e.key === 'ArrowUp' && results.length > 0) {
              e.preventDefault();
              setActive((i) => Math.max(i - 1, 0));
            } else if (e.key === 'Enter' && results.length > 0) {
              pick(results[active >= 0 ? active : 0]);
            }
          }}
        />
        {query && (
          <button className="search-clear" onClick={clear} aria-label="Clear search">
            ×
          </button>
        )}
      </div>
      {open && results.length > 0 && (
        <ul className="search-dropdown">
          {results.map((r, i) => (
            <li key={`${r.node_type}:${r.id}`}>
              <button
                className={`search-option ${i === active ? 'active' : ''}`}
                onClick={() => pick(r)}
                onMouseEnter={() => setActive(i)}
              >
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
