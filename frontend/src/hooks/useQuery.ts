import { useCallback, useState } from 'react';

import { api } from '../api/client';
import type { QueryResponse } from '../types/graph';

// State management for the natural-language query panel.
export function useQuery() {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<QueryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async (question: string, tissue = 'all') => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.query({ question, tissue, max_hops: 2 });
      setResult(res);
      if (res.error) setError(res.error);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  return { loading, result, error, run };
}
