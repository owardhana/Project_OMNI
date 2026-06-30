# ADR 0012 — Metabolite "bridge" connectivity (opt-in, degree-gated)

Status: Accepted (2026-06-30)

Amends [ADR-0011](0011-backbone-guaranteed-traversal.md) — specifically its
"Rejected alternatives → Metabolites as capped bridges". Extends
[ADR-0005](0005-signal-decay-traversal.md) (signal-decay traversal) and
[ADR-0009](0009-metabolomics-layer-4.md) (metabolomics layer).

## Context

ADR-0011 made a metabolite **discovered mid-traversal** a terminal **leaf** (never
re-expanded). This was the single rule preventing the *cofactor flood*: ATP / NAD⁺ /
H₂O are catalysed by *thousands* of proteins, so if one peripheral metabolite
expanded its `CATALYSES` edges it would pull in the entire metabolic network.

ADR-0011 explicitly *deferred* the richer "shared-substrate" behaviour — letting a
metabolite reach the **other** enzymes that catalyse it — for two stated reasons:

1. it needs `CATALYSES` **dense-capping** (a metabolite can have a high enzymatic
   degree), and
2. it needs an **ongoing cofactor-exclusion list** — a hand-maintained list of hub
   metabolites to suppress, which is a standing maintenance burden.

Reason (2) was the real blocker: a hand-curated cofactor list rots and is never
complete. This ADR removes that objection.

## Decision

Add metabolite-bridge connectivity as an **opt-in, default-OFF** traversal feature
(`METABOLITE_BRIDGE_ENABLED`, default `false`). When ON, a metabolite discovered
mid-traversal may expand **once** to its co-catalysing proteins, **gated by a
data-driven cofactor signal** rather than a hand list.

### The cofactor gate is data-driven, not hand-maintained

`14_metabolomics.py` persists `Metabolite.catalyses_degree` — the graph degree of
incoming `CATALYSES` (how many proteins catalyse the metabolite) — in a post-pass,
re-derived on **every ETL run**. The bridge never-expands a metabolite whose degree
exceeds `METABOLITE_MAX_CATALYSES_DEGREE` (default `30`). Cofactors (ATP/NAD⁺/H₂O)
have degrees in the hundreds-to-thousands and are excluded automatically; a specific
metabolic intermediate (a handful of enzymes) passes. **This directly neutralises
ADR-0011's reason (2):** the exclusion set maintains itself.

A tiny hard-exclude name floor (`_COFACTOR_HARD_EXCLUDE`: water/ATP/NAD⁺/CoA/…) is
kept only as a belt-and-suspenders backstop for graphs whose `catalyses_degree` is
absent (e.g. predating the post-pass). It is **not** the primary mechanism.

### Mechanism

When `METABOLITE_BRIDGE_ENABLED` is on, two things change in
`backend/db/queries/traversal.py`, both confined to Phase 2 (breadth BFS):

1. **`CATALYSES` joins the dense-capped set.** A metabolite's expansion to proteins
   is capped per ring at `CATALYSES_MAX_EXPAND_PER_NODE` (default `8`), ranked
   deterministically by neighbour key. This also bounds ADR-0011's documented
   "cofactor-as-seed floods ring 1" known limitation (a cofactor *seed*'s `CATALYSES`
   fan-out is now capped, degrading to a bounded view rather than an explosion).
2. **The leaf rule is relaxed for non-cofactor metabolites.** A discovered
   metabolite enters `next_frontier` iff `_metabolite_can_bridge` holds (flag on,
   degree ≤ threshold, not in the hard floor). Otherwise it remains a leaf exactly
   as ADR-0011 specified.

The backbone pre-pass (Phase 1) is **unchanged** — the seed's own vertical chain and
its pinned metabolites are independent of the bridge.

### Tunables (env-driven; never hardcode — project rule)

| Setting | Default | Meaning |
|---------|---------|---------|
| `METABOLITE_BRIDGE_ENABLED` | `false` | Master opt-in for the bridge |
| `CATALYSES_MAX_EXPAND_PER_NODE` | `8` | Co-catalysing proteins a metabolite expands to per ring |
| `METABOLITE_MAX_CATALYSES_DEGREE` | `30` | Metabolites above this `catalyses_degree` never expand |

## Consequences

- **Default behaviour is byte-for-byte ADR-0011.** With the flag off,
  `_metabolite_can_bridge` short-circuits to `False` and `CATALYSES` is not in the
  capped set, so discovered metabolites stay leaves and `CATALYSES` stays uncapped.
- **Opt-in shared-substrate exploration** becomes possible without a cofactor flood:
  from a metabolic intermediate you can reach the other enzymes that act on it.
- The cofactor-exclusion burden ADR-0011 feared is gone — the gate is the graph's own
  `catalyses_degree`, recomputed each load.

### Regression guarantees (flag OFF must reproduce ADR-0011 exactly)

Enforced by `backend/tests/test_traversal_bridge.py`:

- `TP53@600` → **0 metabolites** (pure TF, no metabolic backbone)
- `LDHA@800` → **15 metabolites** (its own catalysed chain)
- `L-Lactic acid` seed → **110 metabolites, 77 proteins** (metabolite-seeded view)

With the flag ON, the same suite asserts a metabolic-gene seed surfaces a **bounded**
set of co-catalysing proteins and that seeding/transiting a cofactor does **not**
flood (node count stays ≤ `max_nodes`).

## Rejected alternatives

- **Hand-maintained cofactor-exclusion list** (the ADR-0011 sketch). Rejected: it
  rots and is never complete. The data-driven degree gate replaces it.
- **Bridge on by default.** Rejected: ADR-0011's leaf rule is the carefully-tuned
  default; the bridge is additive and must not silently change existing views.
- **Bidirectional/unbounded metabolite expansion.** Rejected: re-introduces the
  cofactor flood. Expansion is single-hop per discovery, dense-capped, degree-gated.
