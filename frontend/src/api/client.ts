// Typed fetch wrappers for the OmniGraph backend.

import type {
  EntitySearchResponse,
  GeneNode,
  GraphResponse,
  PathResponse,
  QueryRequest,
  QueryResponse,
  SearchResult,
  TranscriptNode,
} from '../types/graph';

export interface EntityFilters {
  q?: string;
  types?: string;
  chromosome?: string;
  clinical?: string;
  pli_min?: number;
  limit?: number;
  offset?: number;
}

// `??` (not `||`) so a production build with VITE_API_URL="" keeps an EMPTY base →
// same-origin relative calls (/api/...), which is how Caddy serves the app + proxies
// the API on one host. Dev leaves VITE_API_URL unset → falls back to localhost:8000.
const BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

// Streaming chat (Feature 1). The SSE route is POST (body carries session_id +
// message), so we read the response body and parse `data: {json}\n\n` frames rather
// than using EventSource (GET-only).
export type ChatEvent =
  | { type: 'token'; text: string }
  | { type: 'tool'; name: string; status: 'running' | 'done' }
  | { type: 'done'; answer: string }
  | { type: 'error'; message: string };

export async function chatStream(
  body: { session_id: string; message: string; tissue?: string },
  onEvent: (ev: ChatEvent) => void,
): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) throw new Error(`chat stream failed: ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const frames = buf.split('\n\n');
    buf = frames.pop() ?? ''; // keep the trailing partial frame
    for (const frame of frames) {
      const line = frame.trim();
      if (line.startsWith('data:')) {
        try {
          onEvent(JSON.parse(line.slice(5).trim()) as ChatEvent);
        } catch {
          /* ignore malformed frame */
        }
      }
    }
  }
}

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
  // Mixed-entity search (Gene/Transcript/Protein/Disease) — each result carries
  // node_type. searchGenes kept as a back-compat alias.
  searchNodes: (q: string, limit = 10) =>
    getJSON<SearchResult[]>(
      `/api/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    ),
  searchGenes: (q: string, limit = 10) =>
    getJSON<SearchResult[]>(
      `/api/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    ),

  getGene: (symbol: string) =>
    getJSON<GeneNode>(`/api/gene/${encodeURIComponent(symbol)}`),

  getDiseaseGraph: (ontologyId: string, maxNodes?: number) =>
    getJSON<GraphResponse>(
      `/api/disease/${encodeURIComponent(ontologyId)}/graph${
        maxNodes != null ? `?max_nodes=${maxNodes}` : ''
      }`,
    ),

  // Signal-decay traversal (ADR-0005); optional max_nodes overrides the server
  // default. Tissue is NOT sent — it is a render-time opacity concern (ADR-0006).
  getGeneGraph: (symbol: string, maxNodes?: number) =>
    getJSON<GraphResponse>(
      `/api/gene/${encodeURIComponent(symbol)}/graph${
        maxNodes != null ? `?max_nodes=${maxNodes}` : ''
      }`,
    ),

  // Metabolite neighborhood (ADR-0009/0010). Seeded by hmdb_id or chebi_id — the
  // search result's `id` carries whichever the node has.
  getMetaboliteGraph: (id: string, maxNodes?: number) =>
    getJSON<GraphResponse>(
      `/api/metabolite/${encodeURIComponent(id)}/graph${
        maxNodes != null ? `?max_nodes=${maxNodes}` : ''
      }`,
    ),

  getTranscript: (ensemblTxId: string) =>
    getJSON<TranscriptNode>(`/api/transcript/${encodeURIComponent(ensemblTxId)}`),

  query: (body: QueryRequest) => postJSON<QueryResponse>('/api/query', body),

  searchEntities: (f: EntityFilters) => {
    const p = new URLSearchParams();
    if (f.q) p.set('q', f.q);
    if (f.types) p.set('types', f.types);
    if (f.chromosome) p.set('chromosome', f.chromosome);
    if (f.clinical) p.set('clinical', f.clinical);
    if (f.pli_min != null) p.set('pli_min', String(f.pli_min));
    p.set('limit', String(f.limit ?? 50));
    p.set('offset', String(f.offset ?? 0));
    return getJSON<EntitySearchResponse>(`/api/entities?${p.toString()}`);
  },

  // Multi-seed merge + shortest path (Phase 14).
  getMultiGraph: (seedIds: string[], seedTypes: string[]) =>
    postJSON<GraphResponse>('/api/graph/multi', {
      seed_ids: seedIds,
      seed_types: seedTypes,
    }),

  getPath: (fromId: string, typeA: string, toId: string, typeB: string) =>
    getJSON<PathResponse>(
      `/api/graph/path?from_id=${encodeURIComponent(fromId)}&type_a=${typeA}` +
        `&to_id=${encodeURIComponent(toId)}&type_b=${typeB}`,
    ),
};

export const DEFAULT_GENE = (import.meta.env.VITE_DEFAULT_GENE as string) || 'TP53';
