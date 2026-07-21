# Design — Literature Extraction Agent (Feature 2)

Status: **P1 + P2 BUILT (2026-07-02) + P3 admin review dashboard BUILT (2026-07-03), OFF
by default. Rest of P3 remaining.** A grill-with-docs session (2026-07-01) locked 9
decisions; P1 (extraction→staging) and P2 (promotion + tier discount + "proposed"
rendering) were then built on branch `feat/literature-extraction-mvp`, followed by the
admin review dashboard (ADR-0014). The whole feature is gated OFF
(`EXTRACTION_AGENT_ENABLED=false`) — nothing spends or writes without opting in. Trust
model: [ADR-0013](../adr/0013-literature-extraction-trust-model.md). See **Phasing** for
what's built vs. remaining (P3).

## Goal

A scheduled agent that reads biomedical papers (nightly PubMed delta + a deferred
historical backfill), decides whether any node↔node relationship in OmicGraph's
vocabulary is *asserted*, extracts it with provenance, and **proposes** it as a
candidate — never a trusted edge. After extraction the paper text is discarded (only
PMID + supporting sentence span kept), so storage stays bounded.

## The hard part is trust, not plumbing

OmicGraph's credibility rests on one rule: **agents never hallucinate biology.**
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

## Cost / compute

Nightly is near-free (E-utils + local filter + a few hundred LLM calls). The **backfill**
(millions of papers) was the "expensive tier" — now solved by inference cost, not scale
tricks: `EXTRACTION_MODEL` defaults to a **free** OpenRouter slug, so both nightly and
backfill are $0 on the LLM side (only NCBI is metered, and it's free). The originally-planned
cheap-screen→strong-confirm two-tier and a quantized local CPU model (llama.cpp, ARM) are
kept as **fallbacks** if the free tier's yield/latency disappoint. Measured on the default
free model (Nemotron 3 Ultra, `reasoning.exclude`): clean constrained-JSON verdicts, but
**latency is high and variable** (tens of seconds/call, occasional long queueing) — hence
the bounded per-verdict timeout, backoff/retry, and the "always running, throttling costs
time not data" operating model. If backfill *speed* matters more than $0, point
`EXTRACTION_MODEL` at a paid slug. See [cloud-migration.md](cloud-migration.md).

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
3. **P3 — backfill + more edge types + host. 🟡 PARTIALLY BUILT.**
   - **Calibrate auto-promote first:** run `RUN_EXTRACTION_EVAL` on an expanded labelled
     set (30–50, disease-linking-heavy), set `VALIDATION_AUTO_PROMOTE_CONFIDENCE` from
     measured precision, then consider enabling `VALIDATION_AUTO_PROMOTE_ENABLED`. ⏳
   - **Date-cursor pipeline — ✅ BUILT (see "Historical backfill & nightly cursor" below).**
     The throttled historical pull (2005→now) + an interruption-safe nightly forward
     catch-up, both walking a persisted `:ExtractionCursor`. The expensive-tier cost is
     solved differently than first sketched: instead of a cheap-screen→strong-confirm
     two-tier, the relation model defaults to a **free** OpenRouter slug (NVIDIA Nemotron
     3 Ultra), so the always-on backfill is $0 and "just leave it running" is viable. The
     two-tier + quantized-local-model option is retained as a fallback if the free tier's
     yield/latency proves inadequate.
   - **More edge types (scope: 5 of 9, not all).** The extractor targets edges that are
     *claims a paper asserts*, never facts we already compute authoritatively. MVP does 2
     (`INTERACTS_WITH`, `IMPLICATED_IN`); the fast-follows are `CATALYSES`
     (protein→metabolite), `REGULATES` (needs a "which entity is the TF" sub-check — the
     graph already knows the TF subtype), and `ASSOCIATED_WITH` (needs rsid mentions,
     sparse in abstracts) → **5 extractable total.** Each is a new `edge_type_for`
     kind-pair + relation-desc + `_KIND_MAP`/direction entry.

     The other **4 are deliberately NOT extraction targets** — structural/derived facts
     with an authoritative structured source, where reading them from prose only adds
     hallucination surface for no gain: `IN_GENE` (variant→gene, a genomic coordinate),
     `TRANSLATES_TO`/`ENCODES` (transcript→protein, GENCODE/HGNC annotation), `PRODUCES`
     (gene→transcript, annotation), `DIFFERENTIALLY_EXPRESSED` (gene→tumour, TCGA
     quantitative log2FC + cohort — a paper's "upregulated in X" is a weaker qualitative
     restatement of what we already have). This is a scope decision, not a backlog gap.
   - **Host + cron — ✅ BUILT.** The nightly forward catch-up is an APScheduler cron in the
     FastAPI backend (`EXTRACTION_BACKFILL_CRON_HOUR`), gated on `EXTRACTION_AGENT_ENABLED`
     — no extra scheduler service (consistent with the citation/embedding crons). Enable it
     on the Oracle box per the runbook. See [cloud-migration.md](cloud-migration.md).
   - **Admin review dashboard — ✅ BUILT (2026-07-03).** The human-in-the-loop promotion
     surface (auto-promote is uncalibrated + OFF, so manual review is the only safe
     promotion path). Two-pane queue at `#/admin`, `ADMIN_TOKEN`-gated; approve/reject/
     revert with exact-delta revert. Full spec + build notes below (**Admin Review
     Dashboard**); trust decisions in
     [ADR-0014](../adr/0014-literature-review-dashboard.md).
   - **Expand the disease-generic gate** to be data-driven (single-token `Disease.name`
     audit) rather than the hardcoded `GENERIC_TERMS` floor.

## Historical backfill & nightly cursor (P3)

**BUILT.** Turns the one-shot `reldate` delta into two always-on, interruption-safe walks
over PubMed publication dates. Free-model inference (default `EXTRACTION_MODEL =
nvidia/nemotron-3-ultra-550b-a55b:free`) makes "just leave it running" the operating model.

Windows walk PubMed by **entry date** (`EXTRACTION_DATE_TYPE=edat`, the date a record was
added), not publication date. `pdat` defaults year-only pub dates to Jan 1, so a single
`YYYY/01/01` returns ~123k records (measured) — over both the chunk cap and the esearch
9,999 no-history cap, silently truncating the backfill for exactly the older literature it
targets. `edat` is a clean per-record partition (~4k on the same day) and also catches
late-indexed papers. The real publication date is still read from each article's metadata.

**State = one singleton node per direction**, not one node per scanned paper (a
`:ScannedArticle`-per-PMID scheme would mint tens of millions of bookkeeping nodes over
2005→now — the same Community-edition volume wall that gates ENCODE). Progress is a date:

```
(:ExtractionCursor {name, direction, cursor_date, floor_date?, status,
                    chunks_done, pmids_processed, candidates_staged, last_window, ...})
```

- **`forward-catchup`** walks `[A+1 .. today−lag]` upward — the nightly cron. Its frontier
  trails `today` by `EXTRACTION_FORWARD_LAG_DAYS` so PubMed's indexing lag can't strand
  recently-indexed papers (the failure the old `reldate` delta had: a crashed night's PMIDs
  fell permanently out of the next night's relative window).
- **`backward-historical`** walks `[floor .. A]` downward to `EXTRACTION_BACKFILL_FLOOR_DATE`
  (default `2005-01-01`) — the always-on backfill. Pausable; resumes on restart.
- Anchored together at `A = today − lag` on `start`, so the two coverage regions meet with
  no seam and no overlap. The date-window arithmetic is pure and unit-tested
  (`test_backfill_cursor.py`: every day in range covered exactly once, no gaps).

**Resumability is at chunk granularity.** The loop advances the cursor **only after a whole
window completes**, so a crash (or the common one — a `git pull && up --build` redeploy)
mid-chunk just redoes that window on restart. Redo is safe because `stage_verdict` MERGEs
are idempotent. On startup, any cursor persisted as `RUNNING` is relaunched; `PAUSED`/`DONE`
are left alone so operator intent survives a restart.

**Throughput.** Windows are `esearch`-probed and halved until under
`EXTRACTION_MAX_PMIDS_PER_CHUNK` (so a dense week can't blow up one chunk); the retstart page
loop then fetches the window (no history-server/WebEnv needed under the 9,999 cap). Within an
efetch batch, the per-(sentence,pair) verdicts run under an
`asyncio.Semaphore(EXTRACTION_LLM_CONCURRENCY)` and are then **staged serially** — the LLM
call is seconds, the Neo4j write milliseconds, so this keeps the throughput while avoiding
MERGE contention on the same `CandidateEdge`.

**Graceful backlog / rate-limiting** (the free tier throttles, and that's accepted): NCBI +
LLM calls retry with exponential backoff honouring `429 Retry-After`. If a chunk still shows
LLM errors, the cursor is **not advanced** — the loop backs off and retries the *same*
window, so throttling costs time, never data. Only after `EXTRACTION_HTTP_MAX_RETRIES`
consecutive stalls does it advance with a loud log, so one pathological window can't wedge
the pipeline. A verdict whose output is merely *unparseable* is dropped (recall cost only) —
distinct from a *failed* call (retried); this split is what keeps a worse/free model safe.

**Concurrency safety.** Forward + backward cursors run at once, so `MERGE (ce:CandidateEdge
{triple_key})` can now race. `create_indexes()` declares a **uniqueness constraint** on
`CandidateEdge.triple_key` (+ `CandidateEvidence(triple_key,pmid)`), without which concurrent
MERGE would create duplicate nodes and corrupt the `n_affirm`/confidence recompute; staging
also retries on Neo4j `TransientError` (lock deadlock). Launches are deduped in-process so
start/resume/cron/startup can't spawn a second loop for one cursor.

**Provenance.** `CandidateEvidence.model` records the *actual* model per verdict (threaded
through `RelationVerdict`), so backfill (free Nemotron) vs. a paid nightly run stay
distinguishable in the review dashboard without a separate flag.

**Files:** `backend/extraction/{cursor,backfill}.py`, `ingest.count_pmids_in_range` /
`fetch_pmids_in_range`, `ExtractionAgent.process_window`, admin
`/agents/extraction/backfill/{start,pause,resume,status}`, `main.py` cron + startup resume.
**Operate it** via the runbook (`docs/deploy/oracle-runbook.md` → "Enable the literature
backfill").

## Admin Review Dashboard (P3)

The human-in-the-loop promotion surface. **BUILT 2026-07-03** (grill-with-docs +
implementation); trust decisions in [ADR-0014](../adr/0014-literature-review-dashboard.md).
Access is gated by a single `ADMIN_TOKEN` header + the existing `EXTRACTION_AGENT_ENABLED`
master gate on every write route (Caddy basic-auth in front on the Oracle host). Reached at
`#/admin` in the frontend. The two cross-nav conveniences (a "Load endpoints in viewer"
button and an `EdgeDetailPanel`→admin deep-link) are **deferred** — the minted edge now
carries `r.triple_key` so the deep-link is trivial to add later without unused UI now.

### Using the dashboard (operator guide)

1. **Enable + open.** Set `EXTRACTION_AGENT_ENABLED=true` (writes are gated on it) and, on
   any shared host, `ADMIN_TOKEN=<secret>`. Open **`/#/admin`** (locally
   `http://localhost:3000/#/admin`). If a token is set, paste it once when prompted — it is
   stored in `localStorage` and sent as `X-Admin-Token`.
2. **Pick a tab.** **Pending** is the work queue (sortable by confidence, affirming-paper
   count, or recency — and it is *not* confidence-gated, so low-confidence proposals are
   visible). **Promoted** and **Rejected** are the audit trail.
3. **Open a candidate** (left list → detail pane). Read the evidence before deciding — there
   is no action on the list rows by design. The detail pane shows:
   - the proposed edge (`SUBJECT ↔/→ OBJECT rel_type`) and a **would-it-MINT-or-ENRICH**
     badge — MINT = a brand-new literature-tier edge; ENRICH = appends citations to an
     existing canonical edge (advisory; re-checked at click time);
   - **evidence**, one row per PMID (PubMed link, polarity, cited sentence, model +
     confidence) with **contradicting papers surfaced first**;
   - **endpoint context** (each node's degree + summary) and **agent profiling**.
4. **Act** (detail pane, each confirms first):
   - **Approve** → promotes: MINT a new `provenance_tier='literature'` edge (renders as
     "proposed" in the graph) or ENRICH the canonical edge. Bypasses the auto-promote
     threshold (manual = deliberate).
   - **Reject** → flags the candidate (kept, never re-proposed). *Terminal — no in-UI
     un-reject* (see Known limitations).
   - **Revert** (promoted candidates) → undoes the promotion: deletes the minted edge, or
     strips exactly the PMIDs it added (canonical citations preserved), and returns the
     candidate to Pending.
5. **Try it with demo data:** `scripts/seed_demo_candidates.py` seeds a mix across all three
   tabs; `--clear` removes it (see **Playground data** below).

### Reviewer workflow

Three verbs only — **approve · reject · revert** — no edit-before-approve (the reviewer
*gates* biology, never *authors* it; ADR-0014 §1). Actions live **only in the detail pane**:
you must open a candidate and see its evidence before deciding (no blind approve from the
list). Status tabs: **pending** (default, the work) · **promoted** · **rejected** (audit).

```
open candidate → read evidence + agent profiling → approve | reject
   approve → _promote_one: MINT new literature-tier edge, or ENRICH existing canonical edge
   reject  → status='rejected' (kept + flagged, never re-proposed, never deleted)
   revert  → undo a promotion (see ADR-0014 §2 — exact-delta, fully reversible)
```

### New backend endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /admin/candidates?status=pending\|promoted\|rejected&sort=confidence\|n_affirm` | Review queue. **Not confidence-gated** (ADR-0014 §4 — unlike `list_candidates`); confidence is a sortable column. Returns per-row summary + evidence counts. |
| `GET /admin/candidates/{triple_key}` | **Detail payload** (below) — resolved endpoints, evidence chain, `would_be_action`, agent profiling. |
| `POST /admin/candidates/{triple_key}/approve` | Reused (exists). Promote/enrich. |
| `POST /admin/candidates/{triple_key}/reject` | Reused (exists). |
| `POST /admin/candidates/{triple_key}/revert` | **New.** Undo a promotion (ADR-0014 §2). |

### Detail payload — `GET /admin/candidates/{triple_key}`

```
proposed_change:
  rel_type, symmetric?, direction
  subject: {id, kind, name}      ← RESOLVED: look up ENSG/UniProt/EFO → "TP53"/"breast cancer"
  object:  {id, kind, name}         (ids are stored as raw string props, not relationships)
  would_be_action: "MINT" | "ENRICH"   ← ADVISORY preview via trusted_edge_exists;
                                          _promote_one re-checks at click time (authoritative)
scoring:
  confidence, n_affirm, n_negate, status, first_seen, last_seen
evidence[]:                        ← the :SUPPORTS chain, one row per PMID
  {pmid (→ pubmed link), sentence_span (model's cited span, surfaces highlighted),
   polarity, model_conf, model, extracted_at}
  · negate rows surfaced prominently (contradicting papers shown, not just counted)
endpoint_context:                  ← helps judge the entity
  subject/object: {existing degree, summary_text snippet}
agent_profiling:
  source_agent, agent_version, run_timestamp, model slug, originating ExtractionRun
```

### Revert semantics (ADR-0014 §2 — exact-delta, no canonical corruption)

Promotion records the exact set it changed so revert can undo either branch cleanly:

- **ENRICH** — promotion writes `ce.enriched_pmids = [x IN affirming WHERE NOT x IN existing]`
  (the precise delta appended). Revert removes exactly that set from the canonical edge's
  `pmids[]`, clears `lit_enriched` iff no literature PMIDs remain, resets candidate →
  `pending`. Canonical `source_db` + pre-existing citations untouched.
- **MINT** — revert deletes the promoted edge **iff `provenance_tier='literature'`** (guard:
  can never delete a canonical edge), resets candidate → `pending`.

### Frontend

`/admin` route in the existing React app (ADMIN_TOKEN-gated, hidden without it). **Two-pane
review queue:** left = status-tabbed list (confidence-sortable); right = detail pane (payload
above + action buttons). Reuse the viewer instead of a bespoke mini-graph: a **"Load
endpoints in viewer"** button seeds the 3D graph via `POST /api/graph/multi`; conversely,
clicking a literature-tier edge's `EdgeDetailPanel` "⚠ Proposed" badge deep-links to that
candidate in `/admin`. Optional cheap keyboard nav (`j`/`k`/`Enter`); **no** approve/reject
hotkeys (deliberate clicks only).

### Prerequisite stage.py patch

`stage.py` currently persists `polarity`, `model_conf`, `sentence_span` per
`:CandidateEvidence`. Add `model` (slug) + `extracted_at` (design-doc schema already promises
them) so the profiling panel can report which model/version proposed each piece of evidence.

### Playground data (dev)

[`scripts/seed_demo_candidates.py`](../../scripts/seed_demo_candidates.py) seeds a spread of
**mock** candidates (pending/promoted/rejected, both edge types, sub-floor→high confidence,
some with contradicting evidence) referencing real nodes so names resolve. Every mock row is
tagged `mock: true`; teardown is one command and also removes any literature edge a mock was
promoted into (matched by `r.triple_key`), so it never leaves residue:

```
PYTHONPATH=. python scripts/seed_demo_candidates.py          # create
PYTHONPATH=. python scripts/seed_demo_candidates.py --clear  # remove ALL mocks
```

### Known limitations (dashboard)

- **Reject is terminal — no in-UI un-reject.** `revert` is defined for *promotions* only
  (ADR-0014 §2); a rejected candidate is flagged and never re-proposed, but a mis-clicked
  Reject has no dashboard recovery (it can only be un-rejected by editing `ce.status` in
  Cypher). Accepted for MVP (reject confirms before acting; un-reject is out of the locked
  scope). Revisit if it bites in real use.
- **Deployment:** the prod Caddy config must reverse-proxy `/admin/*` to the backend (added
  to [`deploy/Caddyfile`](../../deploy/Caddyfile)); without it the dashboard's same-origin
  `/admin` calls fall through to the SPA and 404. Wrap that block in `basic_auth` for a
  second layer on a public host (ADR-0014 §3).

### Build order

1. `stage.py` evidence-field patch (`model`, `extracted_at`) — smallest, unblocks profiling.
2. `_promote_one` ENRICH branch records `ce.enriched_pmids`; add `ValidationAgent.revert`.
3. Backend: `GET /admin/candidates?status=`, `GET /admin/candidates/{tk}`,
   `POST …/revert`. Endpoint-id→name resolution reuses the existing entity queries.
4. Frontend `/admin` two-pane queue + EdgeDetailPanel deep-link.
5. Tests: revert round-trip (MINT delete-guard + ENRICH exact-delta, assert canonical
   `pmids` uncorrupted); detail-payload resolution; list not confidence-gated.

### Known limitation to revisit when the feature is enabled

A promoted literature `INTERACTS_WITH` has no `combined_score`, so `_edge_rank` (which
ranks the dense-capped `INTERACTS_WITH` frontier by `combined_score`) may cap it out of
a traversal — meaning the conductance discount could rarely fire on real views. Not a
correctness bug (staging/promotion are correct); it's a "does the proposed edge surface"
question to check once real literature edges exist.
