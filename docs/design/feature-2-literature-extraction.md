# Design — Literature Extraction Agent (Feature 2)

Status: **P1 + P2 BUILT (2026-07-02), OFF by default. P3 remaining.** A grill-with-docs
session (2026-07-01) locked 9 decisions; P1 (extraction→staging) and P2 (promotion +
tier discount + "proposed" rendering) were then built on branch
`feat/literature-extraction-mvp`. The whole feature is gated OFF
(`EXTRACTION_AGENT_ENABLED=false`) — nothing spends or writes without opting in. Trust
model: [ADR-0013](../adr/0013-literature-extraction-trust-model.md). See **Phasing** for
what's built vs. remaining (P3).

## Goal

A scheduled agent that reads biomedical papers (nightly PubMed delta + a deferred
historical backfill), decides whether any node↔node relationship in OmniGraph's
vocabulary is *asserted*, extracts it with provenance, and **proposes** it as a
candidate — never a trusted edge. After extraction the paper text is discarded (only
PMID + supporting sentence span kept), so storage stays bounded.

## The hard part is trust, not plumbing

OmniGraph's credibility rests on one rule: **agents never hallucinate biology.**
`CitationAgent` only attaches PMIDs to *existing* edges; `EmbeddingAgent` only writes
vectors. Neither invents topology. A literature extractor *does* propose new topology,
which breaks that rule unless firewalled:

> Extracted relationships are **candidates**, never trusted edges. They live in a
> separate staging space (`:CandidateEdge`) with provenance + confidence. Only a
> **promotion gate** (human review, or a calibrated auto-promote policy) ever moves
> them into the consortium-grade graph, and even then they are permanently tagged
> `provenance_tier='literature'` — distinguishable from canonical truth forever.

See [ADR-0013](../adr/0013-literature-extraction-trust-model.md) for the firewall.

---

## Locked decisions (grill session 2026-07-01)

| # | Decision | Choice | Why |
|---|----------|--------|-----|
| 1 | World model | **Closed-world** | Graph already holds every canonical id (20k proteins, genes, diseases, metabolites). Entity-linking collapses from "hardest step" to a bounded lookup against our own nodes. A mention that doesn't resolve to an existing node is **dropped**, never minted. Open-world node-minting is P3+. |
| 2 | NER + linking | **Dictionary/gazetteer** (aho-corasick), not statistical NER | Closed-world makes a trained model redundant — we never need to *discover* spans for entities we can't link. Deterministic, auditable, zero-GPU (matters for eventual ARM host). Riders: **load aliases first** + an **ambiguity stoplist**. |
| 3 | Corpus | **Broad `reldate` delta + local ≥2-entity co-mention filter** | E-utils ingest ≈ $0. The "≥2 linked entities **in the same sentence**" gate drops 99% of new papers before any LLM call — the real cost firewall. Backfill (millions) deferred to P3. |
| 4 | Edge types (MVP) | **`INTERACTS_WITH` + `IMPLICATED_IN`** only | Both densely stated in abstracts and **direction-free** (endpoint kinds pin direction). Lowest extraction-error surface. `CATALYSES` → `REGULATES` → `ASSOCIATED_WITH` are fast-follows. |
| 5 | Test/dev env | **Local Docker Neo4j + branch + labelled fixtures** | Community edition = single user DB (no 2nd named DB); the `:CandidateEdge` label *is* the data firewall, not a separate instance. Prod graph is source-of-truth — kept off. Fixtures are the real quality gate. |
| 6 | Relation model | **One cheap deterministic LLM call per (sentence, pair)**; no two-tier for nightly | Post-filter volume is tiny (hundreds of sentences/day). Constrained JSON, temp 0, explicit polarity. Two-tier cheap→strong is a **backfill** concern. |
| 7 | Confidence | **Independent-PMID agreement**, not model self-report | Model verdict is the *gate*; # independent affirming papers − # contradicting is the *score*. |
| 8 | Storage | **Two-tier `:CandidateEdge` + `:CandidateEvidence`** | Multi-paper agreement is a real edge count, ids are string properties (traversal-invisible), MERGE-on-triple_key = idempotent nightly. |
| 9 | Host (MVP) | **Local-only** | No Oracle/ARM/GPU contention with the running enrichment crawls. Real cron + prod host deferred with [cloud-migration](cloud-migration.md). |

---

## Pipeline (MVP)

1. **Dictionary build.** Export from the *local* graph: `Gene.hgnc_symbol` (+ new
   `Gene.aliases[]`), `Protein` display name + UniProt, `Disease` label, into an
   aho-corasick matcher. Each entry → `(surface_form → {id, kind})`. Guaranteed
   in-graph by construction. **Prerequisite ETL patch:** `01_hgnc.py` currently drops
   the `alias_symbol` / `prev_symbol` columns of `hgnc_complete_set.txt`; load them
   into `Gene.aliases[]` or recall craters ("p53" in a paper ≠ `TP53` canonical).
2. **Ingest.** `esearch` with `reldate=<N days>` (nightly delta) → `efetch` abstracts
   (reuse `citation_agent.py`'s E-utils client, rate-limit, provenance). Full text
   (PMC OA) is **out** for MVP — abstracts only.
3. **Match + gate.** Sentence-split each abstract, run the matcher, keep **only
   sentences with ≥2 distinct linked entities of compatible kinds** (protein–protein →
   `INTERACTS_WITH`; gene–disease → `IMPLICATED_IN`). This is the cost firewall — 99%
   drop here, free.
4. **Extract.** One cheap LLM call per surviving (sentence, entity-pair). Endpoint
   kinds pre-select the edge type, so the model answers a near-binary:
   `{asserted: bool, polarity: affirm|negate|hedge, confidence: 0-1, evidence_span}`.
   Temp 0, constrained JSON, model slug from OpenRouter config (ADR-0002).
   `polarity ∈ {negate, hedge}` → dropped or floored (this is where naive extractors
   poison graphs).
5. **Dedup + stage.** Before minting:
   - **Enrichment check** — if a **trusted** edge of that type already exists between
     the two real nodes → this paper is *enrichment*, not a candidate: append the PMID
     (see "Enrichment path" caveat below). Do **not** create a candidate.
   - **Symmetric normalization** — `INTERACTS_WITH` A–B == B–A; canonicalize endpoint
     order into `triple_key` or the same interaction double-counts.
   - `MERGE (:CandidateEdge {triple_key})`, `CREATE (:CandidateEvidence {pmid,…})` iff
     that PMID isn't already recorded for the triple (idempotent nightly), recompute
     `n_affirm`/`n_negate`/`confidence`.
6. **Discard paper.** Keep PMID + `sentence_span`; drop full text.

## Schema (staging)

```
(:CandidateEdge {                          // one per unique triple — TRAVERSAL-INVISIBLE
   triple_key,                             // canonical: rel + direction/sorted endpoint ids
   rel_type, subject_id, subject_kind, object_id, object_kind,
   status: 'pending'|'promoted'|'rejected',
   n_affirm, n_negate, confidence,
   provenance_tier: 'literature',          // NEVER 'canonical'
   source_agent, agent_version, first_seen, last_seen
})
   ↑ [:SUPPORTS]
(:CandidateEvidence {                       // one per PMID
   pmid, sentence_span, polarity, model, model_conf, extracted_at
})
```

`subject_id`/`object_id` are **string properties, not relationships to real nodes** —
a CandidateEdge touches zero biological topology until promotion. Same operational
class as `:ChatSession`/`:CitationRun`.

## MVP boundary

**IN:** `backend/agents/extraction_agent.py` + runnable CLI/admin entrypoint (not cron);
dictionary builder + `01_hgnc.py` alias patch; the pipeline above for `INTERACTS_WITH`
+ `IMPLICATED_IN`; 30–50 labelled fixture abstracts + pytest precision/**recall**
measurement; config tunables (never hardcode: reldate window, batch sizes, confidence
floor, min-token-length stoplist, model slug).

**OUT (scope guard):** promotion gate / auto-promote / review UI (**P2**); backfill
(**P3**); real cron + Oracle host (deferred w/ migration); PMC full text; `REGULATES` /
`CATALYSES` / `ASSOCIATED_WITH` (fast-follow); open-world node minting (P3+).

**Success criterion:** measured precision **and recall** on the fixture set (precision
target ≥0.8) for both edge types with negation correctly dropped; a delta run yields N
CandidateEdges with full provenance; and an assertion that **no candidate appears in
traversal, `search_graph`, or the UI node/edge counts** before/after a run (proves zero
topology leak — see firewall invariant in ADR-0013).

---

## Risks / caveats (fold into the plan, not ignore)

- **`IMPLICATED_IN` has a pre-existing, different meaning.** CONTEXT.md defines it as a
  *GWAS-aggregated* gene→disease rollup; `traversal.py:62` gives it a flat `0.5`
  conductance. A literature "gene X implicated in Y" is a *qualitative textual claim*.
  `provenance_tier` separates the source but not the semantics. **RESOLVED (ADR-0013,
  built P2):** `_conductance` applies a `LITERATURE_CONDUCTANCE_FACTOR` discount keyed on
  `provenance_tier`, so a promoted literature `IMPLICATED_IN` conducts less than the
  GWAS-aggregated one under one shared label.
- **Disease linking is materially harder than gene.** EFO labels ("type 2 diabetes
  mellitus") ≠ how papers phrase them ("T2D", "diabetic"). `IMPLICATED_IN` recall will
  lag `INTERACTS_WITH`. Fixtures **must** include disease-linking cases, and the metric
  reports **recall**, not just precision (staging makes low recall safe — but you must
  *see* it).
- **The enrichment path is not free.** `CitationAgent` cites **`REGULATES` only**
  (`citation_agent.py:9`). "Hand the PMID to the CitationAgent path" for an existing
  `INTERACTS_WITH`/`IMPLICATED_IN` edge is **not an existing path** — it's a direct
  `pmids[]`-append write (additive, never overwriting canonical `source_db`), or a
  CitationAgent generalization. Cost it as new work, not zero.
- **Read-path firewall is broader than traversal.** `run_cypher` (chat tool) is
  write-blocked but *not* label-filtered → a power user's raw `MATCH (n)` can see
  candidates (acceptable, same as existing operational nodes). But **UI node/edge
  counts must exclude operational labels** or `622,813` silently inflates. See ADR-0013.

## Cost / compute (backfill only)

Nightly is near-free (E-utils + local filter + a few hundred cheap LLM calls). The
expensive tier is **backfill** (millions of papers) — deferred to P3, where the
cheap-screen→strong-confirm two-tier and a possible quantized local model on the host
CPU (llama.cpp, ARM) actually matter. See [cloud-migration.md](cloud-migration.md).

## Phasing

1. **P1 — extraction-to-staging, local. ✅ BUILT.** `backend/extraction/{dictionary,
   ingest,relation,stage,eval}.py` + `agents/extraction_agent.py` + admin trigger.
   Nightly delta → `CandidateEdge`, no promotion. `01_hgnc` alias backfill done;
   `p53`→TP53 resolves live; leak assertion holds. Precision harness gated behind
   `RUN_EXTRACTION_EVAL` (uncalibrated — not yet run). ADR-0013 written.
2. **P2 — promotion gate. ✅ BUILT (mechanism; auto-promote OFF).**
   `agents/validation_agent.py`: promote `CandidateEdge` → real edge tagged
   `provenance_tier='literature'`; reuse `trusted_edge_exists` (enrich-not-duplicate);
   manual approve/reject (`POST /admin/candidates/{tk}/{approve,reject}`);
   `LITERATURE_CONDUCTANCE_FACTOR` discount in `_conductance`; frontend "proposed"
   rendering (pale-yellow faint/thin edge + EdgeDetailPanel badge + legend). Auto-promote
   default-OFF until the precision number exists.
3. **P3 — backfill + more edge types + host. ⏳ REMAINING.**
   - **Calibrate auto-promote first:** run `RUN_EXTRACTION_EVAL` on an expanded labelled
     set (30–50, disease-linking-heavy), set `VALIDATION_AUTO_PROMOTE_CONFIDENCE` from
     measured precision, then consider enabling `VALIDATION_AUTO_PROMOTE_ENABLED`.
   - **Backfill:** throttled historical pull (millions of papers). This is the expensive
     tier — add the cheap-screen→strong-confirm two-tier + possibly a quantized local
     model on the host CPU (llama.cpp, ARM). Nightly stays near-free.
   - **More edge types:** `CATALYSES` (protein→metabolite), `REGULATES` (needs a "which
     entity is the TF" sub-check — the graph already knows the TF subtype),
     `ASSOCIATED_WITH` (needs rsid mentions, sparse in abstracts). Each is a new
     `edge_type_for` kind-pair + relation-desc + `_KIND_MAP`/direction entry.
   - **Host + cron:** move the nightly run onto the Oracle box with a real scheduler
     (currently manual/local). See [cloud-migration.md](cloud-migration.md).
   - **Admin review UI:** a surface over `GET /admin/agents/extraction/candidates` for
     human approve/reject at scale (endpoints exist; no UI yet).
   - **Expand the disease-generic gate** to be data-driven (single-token `Disease.name`
     audit) rather than the hardcoded `GENERIC_TERMS` floor.

### Known limitation to revisit when the feature is enabled

A promoted literature `INTERACTS_WITH` has no `combined_score`, so `_edge_rank` (which
ranks the dense-capped `INTERACTS_WITH` frontier by `combined_score`) may cap it out of
a traversal — meaning the conductance discount could rarely fire on real views. Not a
correctness bug (staging/promotion are correct); it's a "does the proposed edge surface"
question to check once real literature edges exist.
