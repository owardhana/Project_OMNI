// Typed fetch wrappers for the OmniGraph backend.

import type {
  GeneNode,
  GraphResponse,
  QueryRequest,
  QueryResponse,
  SearchResult,
  TranscriptNode,
} from '../types/graph';

const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`);
  if (!res.ok) {
    throw new Error(`GET ${path} failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`POST ${path} failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  searchGenes: (q: string, limit = 10) =>
    getJSON<SearchResult[]>(
      `/api/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    ),

  getGene: (symbol: string) =>
    getJSON<GeneNode>(`/api/gene/${encodeURIComponent(symbol)}`),

  getGeneGraph: (symbol: string, tissue = 'all', hops = 1) =>
    getJSON<GraphResponse>(
      `/api/gene/${encodeURIComponent(symbol)}/graph?tissue=${encodeURIComponent(
        tissue,
      )}&hops=${hops}`,
    ),

  getTranscript: (ensemblTxId: string) =>
    getJSON<TranscriptNode>(`/api/transcript/${encodeURIComponent(ensemblTxId)}`),

  query: (body: QueryRequest) => postJSON<QueryResponse>('/api/query', body),
};

export const DEFAULT_GENE = (import.meta.env.VITE_DEFAULT_GENE as string) || 'TP53';
