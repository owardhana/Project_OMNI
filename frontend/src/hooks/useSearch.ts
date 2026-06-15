import { useEffect, useState } from 'react';

import { api } from '../api/client';
import type { SearchResult } from '../types/graph';

// Debounced (300ms) gene search for the autocomplete dropdown.
export function useSearch() {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);

  useEffect(() => {
    if (!query.trim()) {
      setResults([]);
      return;
    }
    let cancelled = false;
    const handle = setTimeout(async () => {
      try {
        const res = await api.searchGenes(query.trim());
        if (!cancelled) setResults(res);
      } catch {
        if (!cancelled) setResults([]);
      }
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [query]);

  return { query, setQuery, results, setResults };
}
