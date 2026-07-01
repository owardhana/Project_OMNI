# ADR 0013 — Literature-extraction trust model (staging + provenance tier)

Status: Accepted (2026-07-01)

Governs [Feature 2](../design/feature-2-literature-extraction.md) (literature
extraction agent). Extends the provenance discipline of
[ADR-0003](0003-data-source-urls.md) (data-source provenance) and the operational-node
pattern already used by `:ChatSession`/`:ChatTurn`/`:CitationRun`.

## Context

Every prior agent obeys one rule: **agents never hallucinate biology.** `CitationAgent`
only attaches PMIDs to existing edges; `EmbeddingAgent` only writes vectors. The
literature-extraction agent is the first to **propose new topology** from unstructured
text. Without a firewall, one bad entity-link or one misread sentence poisons the
consortium-grade graph (GTEx / STRING / UniProt / Recon3D / GWAS) that is the app's
entire credibility.

The design is closed-world (link only to entities already in the graph) and stages
proposals rather than merging them. This ADR fixes the two parts of that firewall that
are **hard to reverse** once data exists: the storage/visibility model, and how a
promoted literature edge is distinguished from — and weighted against — canonical truth.

## Decision

### 1. Candidates live in operational labels, never as biological topology

Extracted relationships are stored as `(:CandidateEdge)` with `(:CandidateEvidence)`
supporting nodes (schema in the design doc). Endpoint ids are **string properties**
(`subject_id`, `object_id`), **not relationships** to real `:Gene`/`:Protein`/… nodes.
A candidate therefore participates in **zero** biological topology until promotion.

### 2. The firewall invariant is "every user-facing read path filters by label" — broader than traversal

It is **not** sufficient that `signal_decay_subgraph` walks only the 9 typed biological
edges (it does, so candidates can't appear in a traversal). The invariant must hold on
**all** user-facing read paths:

| Read path | Requirement |
|-----------|-------------|
| `signal_decay_subgraph` / graph views | Walk typed biological edges only → candidates invisible (already true). |
| `search_graph` tool | Match biological node kinds only → never surface `subject_label`/`object_label` from candidates. |
| **UI node/edge counts** | **Must exclude operational labels** (`CandidateEdge`, `CandidateEvidence`, `ChatSession`, `ChatTurn`, `CitationRun`). Otherwise the headline `622,813` silently inflates and misleads. |
| `run_cypher` (chat escape hatch) | Write-blocked but **not** label-filtered — a power user's raw `MATCH (n)` *can* see candidates. **Accepted**: this is a deliberate analyst escape hatch, candidates are label-distinct and clearly `provenance_tier='literature'`, same status as existing operational nodes. |

The success test for the pipeline asserts traversal **and** `search_graph` **and** the
node/edge counts are unchanged before/after an extraction run.

### 3. Promoted edges are permanently tagged `provenance_tier`

Promotion (P2, not MVP) never grants a consortium `source_db`. A promoted edge carries:

- `source_db = 'literature_extracted'`
- **`provenance_tier = 'literature'`** (canonical consortium edges are `'canonical'`)
- `agent_version`, `pmids[]` (the supporting evidence)

A single Cypher predicate (`provenance_tier`) cleanly separates proposed biology from
consortium truth **forever**. This is the irreversible bit: promoting without a tier
means the two populations can never be told apart afterward.

### 4. Literature edges carry a discounted conductance (resolves the `IMPLICATED_IN` clash)

`IMPLICATED_IN` **already exists** with different semantics: a GWAS-aggregated
gene→disease rollup, given a flat `0.5` conductance in `traversal.py:_conductance`. A
literature `IMPLICATED_IN` is a *qualitative textual claim* with no p-value. Sharing the
label under one `provenance_tier` would let a single-sentence assertion carry the same
traversal signal as an aggregated GWAS rollup.

Decision: **`_conductance` applies a `provenance_tier='literature'` discount** — a
literature edge conducts strictly less than the canonical edge of the same type
(tunable `LITERATURE_CONDUCTANCE_FACTOR`, default e.g. `0.5`, applied multiplicatively).
The label stays shared (so existing query shapes are unchanged), but literature-tier
edges influence signal-decay less than consortium edges. This keeps `INTERACTS_WITH`,
`IMPLICATED_IN`, etc. as single labels while making promoted literature evidence a
*weaker* signal, matching its epistemic status.

*Applies at P2 (promotion). At MVP (staging only, no promotion) no candidate reaches
`_conductance` at all — but the rule is fixed now so promotion can't bake in an
undefined weight later.*

### 5. Enrichment is additive and never reclassifies

When a paper supports an **existing trusted** edge, the PMID is appended to that edge's
`pmids[]` (with `source_agent`), and the edge's canonical `source_db` / `provenance_tier`
are **never overwritten**. Enrichment annotates truth; it does not downgrade it. (Note:
the existing `CitationAgent` covers `REGULATES` only — enrichment for
`INTERACTS_WITH`/`IMPLICATED_IN` is new write code, not a free reuse.)

## Consequences

- Candidates are queryable, auditable, and multi-paper-aggregatable, yet structurally
  incapable of appearing as trusted biology in any graph view or count.
- The `provenance_tier` field becomes a permanent, first-class axis: every consortium
  edge is backfilled to `'canonical'` (or treated as canonical by absence-default) so
  the predicate is total.
- The frontend gains a visible half of the firewall: literature-tier edges render
  differently (dashed / distinct color / "proposed" badge in `EdgeDetailPanel`) so a
  user never mistakes a machine proposal for consortium truth. (P2.)
- A new tunable `LITERATURE_CONDUCTANCE_FACTOR` joins the env-driven settings.

## Rejected alternatives

- **Merge extractions straight into typed edges** (with just a `source_db` tag).
  Rejected: breaks "agents never hallucinate biology"; one bad link poisons trusted data
  with no staging/review firewall.
- **A distinct edge label per literature relation** (e.g. `LITERATURE_IMPLICATED_IN`).
  Rejected: forks every query shape and traversal rule in two; `provenance_tier` +
  conductance discount achieves the semantic split without a label explosion.
- **Trust the model's self-reported confidence as the promotion signal.** Rejected:
  uncalibrated. Confidence is driven by independent-PMID agreement; the model verdict is
  only the gate.
- **Rely on traversal-only invisibility for the firewall.** Rejected: `run_cypher`,
  `search_graph`, and node counts are separate read paths; the invariant must be stated
  over all of them.
