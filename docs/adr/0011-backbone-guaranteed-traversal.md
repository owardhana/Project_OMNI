# ADR 0011 — Backbone-guaranteed traversal (deep-layer capture)

Status: Accepted (2026-06-23)

Extends [ADR-0005](0005-signal-decay-traversal.md) (signal-decay traversal) and
[ADR-0010](0010-full-proteome.md) (full proteome + dense REGULATES cap).

## Context

After the full proteome landed (ADR-0010), `CATALYSES` connects 94% of
metabolites to their enzymes, yet a **gene-seeded** view still showed **no
metabolites and few non-TF proteins**, even at `max_nodes=800`. Measured:

- `TP53@600` → 534 genes, **0 metabolites**
- `LDHA@800` → 566 variants, **0 metabolites**
- `L-Lactic acid@…` (metabolite seed) → 110 metabolites, 77 proteins — fine.

Two compounding causes in `signal_decay_subgraph`
(`backend/db/queries/traversal.py`):

1. **The discovery guard, not the trim, is binding.** The expansion loop runs
   `while frontier and depth < _MAX_DEPTH and len(node_payload) < max_nodes`, but
   *within* a ring it appends every discovered neighbour to `node_payload`
   uncapped. A master regulator (TP53 → hundreds of `REGULATES`, each capped at 25
   per node) still fans out ~60 → ~685 nodes in a single ring-2 iteration, the
   guard trips, and **ring 3 never runs**. Metabolites sit at ring 2–4 (gene →
   protein → `CATALYSES` → metabolite), so they are frequently never *discovered*.
   A trim-time reservation cannot reserve what was never discovered.
2. **Signal ranking down-weights the molecular backbone.** Even when a metabolite
   *is* discovered, its signal (`≈ 0.7·0.7·0.7 = 0.34` via `ENCODES`→`CATALYSES`)
   ranks below the wide regulatory/variant fan-out, so the `max_nodes` trim drops
   it.

The existing `structural_only` pin already guarantees the seed's vertical chain
survives the trim — but `_STRUCTURAL` stops at the protein (no `CATALYSES`), and
the pin only affects the trim, not the discovery guard.

## Decision

Guarantee the **seed's own vertical omics chain — including its directly
catalysed metabolites — to full depth**, independent of the breadth fan-out. This
is a *floor*, not a balancer: we do **not** reach horizontally to surface
metabolites that belong to other genes.

### What this means per seed type (the deliberate, surprising part)

- **Enzyme / metabolic gene seed (LDHA):** its protein catalyses metabolites that
  are on its *own* chain → now guaranteed present. This closes the LDHA bug.
- **Pure transcription-factor seed (TP53):** the TP53 protein catalyses **nothing**
  — there is no metabolic backbone to guarantee, so a TP53 view **correctly shows
  0 metabolites**. Surfacing metabolites for a pure TF would require a horizontal
  reach-through (TP53 → regulated gene → *that* gene's protein → `CATALYSES`); those
  metabolites belong to the regulated genes, not to TP53. We **explicitly reject**
  that for now — it is heavier and semantically muddier (see Rejected below).

### Mechanism — backbone pre-pass

1. **Phase 1 (pre-pass).** From each seed, traverse **only** structural edges
   (`PRODUCES` / `TRANSLATES_TO` / `ENCODES`) to full depth, then exactly **one**
   `CATALYSES` hop from each reached protein to its metabolites. Mark every node on
   this chain `structural_only` (pinned) and pre-load it into the visited set /
   signal map. The chain is short (≤ ~4 hops, a handful of nodes), so the pre-pass
   is cheap.
2. **Phase 2 (breadth).** The existing ring-batched signal-decay BFS runs for the
   remaining budget. Pinned backbone nodes already survive the `max_nodes` trim
   (ADR-0005), so the guarantee holds regardless of fan-out.

### Metabolites are leaves

A metabolite **expands only when it is a seed** (it sits in the initial frontier).
Any metabolite *discovered mid-traversal* is excluded from `next_frontier` — it is
a terminal display node. This is the single rule that:

- prevents the **cofactor flood** — ATP / NAD⁺ / H₂O are catalysed by *thousands*
  of proteins; if a peripheral metabolite expanded, one such node would pull in the
  entire metabolic network;
- preserves **metabolite-seeded views** — a metabolite *seed* still expands once in
  ring 1 (it is in the initial frontier, not `next_frontier`), so `L-Lactic acid`
  → proteins → their genes/metabolites is unchanged.

`CATALYSES` therefore stays **out of** `_DENSE_CAPPED` — with metabolites as
leaves the only node that expands `CATALYSES` is the seed's own protein (bounded by
its real enzymatic degree) or a metabolite *seed*.

### Tunable

`BACKBONE_MAX_METABOLITES_PER_PROTEIN` (env, default `25`): safety cap on how many
metabolites a single pinned protein contributes to the backbone, ranked
deterministically by metabolite key. Guards against a promiscuous enzyme pinning
hundreds of metabolites past the `max_nodes` budget. Env-driven per project rule
(never hardcode thresholds).

## Consequences

- Enzyme/metabolic-gene seeds now reach their metabolites within `max_nodes`;
  the molecular backbone is no longer starved by the regulatory/variant fan-out.
- Pure-TF seeds still show no metabolites **by design** — documented here so a
  future reader does not "fix" it as a bug.
- The `structural_only` pin gains a depth-bounded `CATALYSES` extension; the trim
  and dense-cap logic are otherwise unchanged.
- One extra (cheap, bounded) Cypher per seed for the pre-pass.

## Rejected alternatives

- **Metabolite presence for *any* seed (horizontal reach-through).** Would surface
  metabolites for pure TFs, but they belong to other genes (muddier semantics) and
  require deep horizontal+vertical reach with its own budget. Deferred; can be added
  later as an opt-in pass without revisiting this floor.
- **Per-layer minimum budget at trim time.** Cannot reserve nodes that the
  discovery guard never discovered (cause #1). Rejected as insufficient on its own.
- **Metabolites as capped bridges** (expand to top-k co-catalysing proteins).
  Richer "shared-substrate" connectivity, but needs `CATALYSES` dense-capping *and*
  an ongoing cofactor-exclusion list. Deferred in favour of the simpler leaf rule.

## Known limitation

Seeding a hub **cofactor** (e.g. ATP) still floods ring 1 — a cofactor seed
expands `CATALYSES` to thousands of proteins. This is pre-existing (uncapped
`CATALYSES` from a seed) and bounded by `max_nodes`, so it degrades to a
less-useful capped view rather than an explosion. Out of scope here; revisit with
the capped-bridges option if cofactor seeds become a real use case.
