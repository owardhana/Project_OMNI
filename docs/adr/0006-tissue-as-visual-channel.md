# ADR 0006 — Tissue is a visual/emphasis channel, not a presence or traversal gate

Status: Accepted (2026-06-15)

## Context

The global tissue filter (All / Blood / Liver / Brain) was implemented as a
**hard backend filter**: `_fetch_subgraph` drops every `PRODUCES` edge with
`tw_<tissue> ≤ 0.3` (`backend/db/queries/genes.py`). Because transcripts enter the
graph only via `PRODUCES`, selecting a specific tissue makes transcripts
**disappear** — observed for TP53 in blood/liver/brain (it shows under "all").

Three problems:

1. **Diverges from spec.** [vision-and-mvp.md](../vision-and-mvp.md) always defined the tissue
   filter as **edge opacity** (dim below threshold, all data loaded), never removal.
2. **The threshold is biologically too aggressive.** `tw = TPM / p99(tissue)`
   clipped to [0,1] (`etl/03_gtex.py`). The 99th percentile is dominated by
   ultra-high-expressed genes, so an ordinary gene sits far below `0.3` in every
   tissue and all its transcripts drop.
3. **Signal-decay would re-introduce it.** ADR-0005 set `PRODUCES` conductance =
   tissue weight, so low-expression transcripts would prune below the signal floor
   — the same vanish, through a new path.

## Decision

**Tissue never determines presence.** It is a visual emphasis channel only.

- **Backend** stops filtering `PRODUCES` by tissue in the neighborhood/subgraph
  fetch — transcripts are always returned.
- **Frontend** scales `PRODUCES`-edge and transcript **opacity continuously** by
  `tw_<tissue>` for the active tissue (e.g. `opacity = clamp(tw, 0.15, 1.0)`):
  weakly-expressed fade but remain visible and selectable. "All" view = full
  opacity (use max `tw_*` across tissues where a single value is needed).
- **Traversal (amends ADR-0005):** tissue is removed from conductance. `PRODUCES`
  conductance becomes a **structural constant (~0.9)**, independent of tissue, so
  the signal-decay path cannot prune a transcript for being weakly expressed.
- **Null weights** (`tw_*` absent — GTEx didn't match the gene) are treated as
  faint / "no data", still present. (If null turns out to be widespread, that is a
  *separate* ETL coverage gap to chase, not a reason to hide nodes.)

### What does NOT change

A **user query** that explicitly asks for a tissue ("what transcripts does BRCA2
produce in liver?") legitimately filters on `r.tw_liver > 0.3` in Text2Cypher —
that is user-requested intent, distinct from the global tissue *toggle*. Query
filtering stays; the global toggle becomes opacity.

## Consequences

- `backend/db/queries/genes.py` — drop the `tissue_filter` clause from the
  `PRODUCES` fetch; `resolve_tissue_key` still validates the tissue but it is no
  longer interpolated into a `WHERE`.
- Frontend (`GraphViewer3D` / `useGraph`) — map `tw_<tissue>` → opacity for
  `PRODUCES` edges and transcript nodes; the tissue toggle re-renders opacity, not
  data.
- **Shared mechanism with bug #4.** react-force-graph caches accessor results
  (`linkVisibility`, `linkColor`, opacity, `nodeVisibility`) and does not re-run
  them until a redraw — a hover forces one, which is why toggles "don't take until
  you mouse over." So a tissue change updating opacity has the **same** failure as
  the layer-toggle bug: it won't apply live without a `fgRef.current.refresh()` on
  the relevant state change. Fix both with one `refresh()` — on `activeTissue`
  change (this ADR) and on `visibleLayers` change (task #4) — not as two separate
  bugs.
- ADR-0005's conductance table is amended (see its note); `TISSUE_WEIGHT_THRESHOLD`
  (0.3) is no longer a hard gate — at most it shapes the opacity ramp, and the
  Text2Cypher query examples keep using it for explicit tissue questions.
- Verify at build time whether benchmark genes' `tw_*` are low vs null (a quick
  Neo4j check) — informs the opacity ramp and surfaces any coverage gap.
