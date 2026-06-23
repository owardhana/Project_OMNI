# ADR 0005 — Signal-decay traversal instead of a fixed hop count

Status: Accepted (2026-06-15)

## Context

The spec bounds neighborhood queries by a fixed "1–2 hops" ([vision-and-mvp.md](../vision-and-mvp.md);
`max_hops` in `backend/api/models.py` and the gene queries). Two pressures break
that:

1. **The TF→protein remodel (ADR-0004) multiplies nodes per query** — a single
   gene query now touches three layers. A fixed hop count either under-shows the
   biology or explodes on hub genes.
2. **A hop count is biologically arbitrary** — a high-confidence regulatory chain
   should reach further than a weak one. We want relevance, not distance, to bound
   the result.

## Decision

Bound traversal by a **decaying signal** (confidence-gated spreading activation),
with a user-set hard cap as a guardrail.

Signal starts at `1.0` at the seed. Crossing an edge:

```
signal_next = signal_cur × d × c(edge)
```

- **`c(edge)` — per-edge conductance (the biology):**
  - `REGULATES` → DoRothEA `confidence` (0–1)
  - `PRODUCES` → **structural constant (~0.9)**, tissue-independent.
    (Originally specced as the tissue weight; **amended by ADR-0006** — tissue is a
    visual channel, not a traversal input, so weak expression dims rather than
    prunes.)
  - `TRANSLATES_TO` / `ENCODES` → ~1.0 (structural, near-certain)
- **`d` — global per-hop decay** (default `0.7`). Essential: structural edges have
  `c ≈ 1.0`, so without a global per-hop decay the vertical backbone never
  attenuates and the result is unbounded. `d` guarantees distance falloff and
  termination regardless of edge type.

Expand the frontier in **descending signal order**; stop when `signal < ε`
(signal floor, default `0.05`) **or** node count ≥ **`max_nodes`** (user-set,
default `150`). Ties are broken **deterministically** (edge confidence, then node
ID) so a render under the hard cap is reproducible.

Defaults `d=0.7`, `ε=0.05`, `max_nodes=150` are user-adjustable.

## Resolved dependency — interaction with the tissue filter (task #3)

Originally, `PRODUCES` conductance = tissue weight risked re-introducing the
"transcripts vanish under blood/brain/liver" symptom (low expression → low signal
→ pruned below `ε`). **Resolved by [ADR-0006](0006-tissue-as-visual-channel.md):**
tissue is a frontend opacity/emphasis channel and is **removed from conductance**
(`PRODUCES` is now a structural constant). Weak expression dims; it never prunes.
No open dependency remains.

## Consequences

- **API contract** — `max_hops` is replaced by `min_signal` (ε), `decay` (d),
  `max_nodes`. Endpoints, `QueryRequest`, and the frontend query params change.
- **Implementation** — backend **ring-batched frontier expansion**: one Cypher per
  hop (UNWIND the whole frontier's node ids), Python applies `d·c` and the `ε`
  floor, expands each node once (BFS), then trims the accumulated visited set to
  `max_nodes` by `(signal desc, id)`. This is one query per depth (~3–8 hops),
  **not** one per node — chosen so the demo/worst-case gene (TP53, whose protein
  regulates 245 genes) renders in <3s. Minor, intentional divergence from strict
  global "descending signal order": ring-batching includes all depth-1 nodes
  before depth-2, so under a tight cap a weak direct regulator outranks a strong
  2-hop node — which reads as "everything one step away first" and is acceptable.
  APOC (`apoc.path.expandConfig`) is an available fallback engine.
- **Subsumes the per-query bounding rule** — no separate "seed gets full chain,
  neighbors get minimal anchor" heuristic is needed; structural edges keep a
  seed's own vertical chain at high signal while weak regulatory edges self-prune.

## Scope note

This is an accepted-but-non-trivial MVP inclusion (the user chose biological
fidelity over the simpler fixed cap). If month-3 time runs short, the graceful
fallback is **`max_nodes` cap alone** (ship the decay later) — the API shape
already supports it (set `ε=0`, rely on the cap).
