import { useEffect, useState } from 'react';

import { api } from '../api/client';
import type { EntityItem } from '../types/graph';

interface Props {
  onMultiLoad: (seeds: { id: string; node_type: string }[]) => void;
  onClear: () => void;
}

const TABS: { key: string; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'gene', label: 'Gene' },
  { key: 'protein', label: 'Protein' },
  { key: 'variant', label: 'Variant' },
  { key: 'disease', label: 'Disease' },
];

const PAGE = 50;

export default function EntityBrowser({ onMultiLoad, onClear }: Props) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [tab, setTab] = useState('all');
  const [showFilters, setShowFilters] = useState(false);
  const [chromosome, setChromosome] = useState('');
  const [clinical, setClinical] = useState('');
  const [pliMin, setPliMin] = useState(0);

  const [items, setItems] = useState<EntityItem[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Map<string, EntityItem>>(new Map());

  const filters = (off: number) => ({
    q,
    types: tab === 'all' ? '' : tab === 'gene' ? 'Gene' : tab,
    chromosome: chromosome || undefined,
    clinical: clinical || undefined,
    pli_min: pliMin > 0 ? pliMin : undefined,
    limit: PAGE,
    offset: off,
  });

  // Debounced search; resets the page whenever query/tab/filters change.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const h = setTimeout(async () => {
      try {
        const res = await api.searchEntities(filters(0));
        if (!cancelled) {
          setItems(res.results);
          setHasMore(res.has_more);
          setOffset(0);
        }
      } catch {
        if (!cancelled) setItems([]);
      }
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(h);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, tab, chromosome, clinical, pliMin, open]);

  const loadMore = async () => {
    const next = offset + PAGE;
    const res = await api.searchEntities(filters(next));
    setItems((prev) => [...prev, ...res.results]);
    setHasMore(res.has_more);
    setOffset(next);
  };

  const keyOf = (it: EntityItem) => `${it.node_type}:${it.id}`;
  const toggle = (it: EntityItem) =>
    setSelected((prev) => {
      const m = new Map(prev);
      const k = keyOf(it);
      if (m.has(k)) m.delete(k);
      else m.set(k, it);
      return m;
    });

  const loadSelected = () => {
    if (selected.size === 0) return; // no-op when nothing selected
    onMultiLoad([...selected.values()].map((it) => ({ id: it.id, node_type: it.node_type })));
  };

  if (!open) {
    return (
      <button
        className="entity-handle"
        onClick={() => setOpen(true)}
        aria-label="Open entity browser"
      >
        ENTITY BROWSER
      </button>
    );
  }

  return (
    <aside className="entity-browser">
      <div className="entity-browser-head">
        <strong>Entity Browser</strong>
        <button className="node-panel-close" onClick={() => setOpen(false)}>
          ‹
        </button>
      </div>

      <input
        className="search-input eb-search"
        placeholder="Search entities…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />

      <div className="eb-tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={`eb-tab ${tab === t.key ? 'active' : ''}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <button className="eb-filter-toggle" onClick={() => setShowFilters((v) => !v)}>
        {showFilters ? '▾' : '▸'} Filters
      </button>
      {showFilters && (
        <div className="eb-filters">
          <label>
            Chromosome
            <input value={chromosome} onChange={(e) => setChromosome(e.target.value)} />
          </label>
          <label>
            Clinical significance
            <input value={clinical} onChange={(e) => setClinical(e.target.value)} />
          </label>
          <label>
            LoF intolerance ≥ {pliMin.toFixed(2)}
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={pliMin}
              onChange={(e) => setPliMin(Number(e.target.value))}
            />
          </label>
        </div>
      )}

      <ul className="eb-list">
        {items.map((it) => (
          <li key={keyOf(it)} className="eb-row">
            <label>
              <input
                type="checkbox"
                checked={selected.has(keyOf(it))}
                onChange={() => toggle(it)}
              />
              <span className={`type-chip type-${it.node_type}`}>{it.node_type}</span>
              <span className="eb-name">{it.display_name}</span>
              {it.description && <span className="eb-desc">{it.description}</span>}
            </label>
          </li>
        ))}
        {items.length === 0 && <li className="eb-empty muted">No results</li>}
      </ul>
      {hasMore && (
        <button className="eb-more" onClick={loadMore}>
          Load more
        </button>
      )}

      <div className="eb-footer">
        <button className="eb-load" disabled={selected.size === 0} onClick={loadSelected}>
          Load selected ({selected.size})
        </button>
        <button
          className="eb-clear"
          onClick={() => {
            setSelected(new Map());
            onClear();
          }}
        >
          Clear graph
        </button>
      </div>
    </aside>
  );
}
