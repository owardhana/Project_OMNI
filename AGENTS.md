# OmicGraph — Agent Definitions

Agents are autonomous processes that read/write the graph or respond to user queries.
Each agent has a defined scope, trigger, and hard constraints.

---

## Agent taxonomy

```
MVP agents (built)
├── CitationAgent    — PubMed PMID enrichment, nightly
├── EmbeddingAgent   — semantic-search embeddings (cron opt-in, default off; run on demand)
├── ChatAgent        — agentic tool-loop over the graph, streaming, per-request
│                      (the query surface; replaced the single-shot Text2Cypher endpoint)
└── ExtractionAgent  — literature -> CandidateEdge proposals (Feature 2; OFF by default,
                       admin-gated; nightly forward cron + always-on historical backfill
                       via date cursors; staging only, promotion is P2)

v2 agents (post-demo)
├── ValidationAgent  — promotion gate: scores + promotes CandidateEdges (Feature 2 P2)
└── FreshnessAgent   — monitors source DB versions, triggers ETL
```

---

## MVP Agents

> Natural-language → Cypher querying is no longer a standalone agent. The former
> single-shot **QueryAgent** (`POST /api/query`, Text2Cypher) was removed once the
> **ChatAgent** subsumed it: `run_cypher` inside the chat tool-loop does the same
> NL→Cypher job in-context, validator-gated. See ChatAgent below.

### 1. CitationAgent

**Role:** Enrich existing graph edges with supporting PubMed PMIDs. Never creates new edges or nodes.

**Trigger:** Nightly cron (00:00 UTC). Also triggerable manually via `POST /admin/agents/citation/run`.

**Input:** `REGULATES` edges where `pmids = []`, batched 100/run. (Post-ADR-0004
these are `(:Protein)-[:REGULATES]->(:Gene)`; the source's `hgnc_symbol` still
drives the PubMed query, so the flow below is unchanged — only the node-label
match in `_fetch_uncited_edges` moves from `:Gene` to `:Protein`.)

**Flow:**
```
fetch batch of edges with no citations
  → for each edge:
      build PubMed query: "{source.hgnc_symbol} {target.hgnc_symbol} regulation"
      → NCBI E-utilities search (max 10 results)
      → fetch abstracts
      → filter: abstract must mention BOTH entity names
      → optional: Claude API to confirm relevance (1 sentence check)
      → attach validated PMIDs to edge
      → log: edge_id, pmids_added, timestamp
  → update DataSource log node
```

**Output:** PMIDs written to edge property `pmids: [...]`. Run log written to `CitationRun` node.

**Constraints:**
- NEVER creates new edges or nodes
- NEVER stores full text — PMIDs only
- NEVER trusts LLM to assert biological facts — only to confirm entity co-mention
- Rate limit: 3 NCBI requests/second (free tier)
- Skip edge if already has ≥3 PMIDs
- Mark edge `citation_attempted: true` even if 0 results (prevent re-querying)

**Tools:** NCBI E-utilities API, Neo4j driver, OpenRouter API (optional relevance check, haiku model)

**Files:** `backend/agents/citation_agent.py`

---

### 2. ChatAgent

**Role:** Conversational, agentic assistant over the graph. Multi-turn, streaming,
tool-using — the analyst-facing query surface (replaced the former single-shot
Text2Cypher endpoint).

**Trigger:** Per user request (HTTP `POST /api/chat/stream`, Server-Sent Events).

**Flow:**
```
load prior turns (conversational memory) → [system, ...history, user]
  → stream an LLM turn (OpenRouter, SYNTHESIS_MODEL) advertising 5 read-only tools
  → if it requested tools: run them, append results, loop (max 6 iterations)
  → else: the streamed text is the final answer
  → forced final no-tools turn if the tool budget is exhausted
  → persist the user + assistant turns
```

**Tools (all READ-ONLY, no write path):** `search_graph` (resolve name→id, full-text),
`semantic_search` (find entities by meaning — embeds the query, then vector-searches
Gene/Protein/Disease; ADR-0008 — the query-time consumer of the EmbeddingAgent's
vectors), `get_subgraph` (signal-decay neighbourhood), `shortest_path` (explain how two
entities connect), `run_cypher` (read-only aggregations — routed through
`validate_cypher`, a single-MATCH read-only guard).

**Memory:** prior user/assistant *text* turns stored in Neo4j as
`(:ChatSession {id})-[:HAS_TURN]->(:ChatTurn {role, content, seq, ts})`. Tool calls are
ephemeral (re-run on demand), never persisted. Operational nodes, never biological topology.

**Constraints:**
- Never writes to the graph — every tool is read-only; `run_cypher` is validator-gated.
- Tool loop is bounded (`_MAX_TOOL_ITERS`=6); errors surface as a clean event, not a 500.
- Tool results are compacted (trimmed fields, capped lists) to bound context + token cost.

**Tools:** OpenRouter API (streaming + tool-calling), Neo4j driver, Cypher validator.

**Files:** `backend/agents/chat_agent.py`, `backend/agents/tools.py`,
`backend/db/queries/chat.py`, `backend/api/routes/chat.py`.

---

### 3. ExtractionAgent

**Role:** Read PubMed and **propose** node↔node relationships as `CandidateEdge`
staging nodes — the first agent to propose topology. Closed-world (links only to
existing graph nodes), abstracts only, MVP edge types `INTERACTS_WITH` + `IMPLICATED_IN`.

**Trigger:** three gated entry points (all refuse unless `EXTRACTION_AGENT_ENABLED=true`;
LLM defaults to a **free** OpenRouter slug so only NCBI is metered):
- `POST /admin/agents/extraction/run` — one-shot `reldate` delta (manual).
- **Nightly forward catch-up cron** (`EXTRACTION_BACKFILL_CRON_HOUR`) — walks a persisted
  date cursor forward to the frontier (`today − lag`), replacing the stateless delta so a
  crashed night resumes the next.
- `POST /admin/agents/extraction/backfill/start` — the always-on **historical backfill**,
  walking a second cursor backward to `EXTRACTION_BACKFILL_FLOOR_DATE` (2005). Pausable
  (`/pause`, `/resume`), resumes on restart; `/backfill/status` reports both cursors.

**Flow:** build gazetteer once → PubMed publication-date window (E-utils, paginated) → per
sentence with ≥2 distinct linked entities → cheap LLM verdict per in-vocab pair (polarity:
affirm/negate/hedge), verdicts run **bounded-concurrently** then stage **serially** →
`stage_verdict`: enrich existing trusted edge, else upsert a `CandidateEdge`
(+`CandidateEvidence` per PMID, tagged with the actual `model`; confidence =
independent-PMID agreement).

**Interruption safety:** progress is a persisted date on a singleton `:ExtractionCursor`
(one per direction), advanced only after a whole chunk completes — a crash mid-chunk just
redoes it (idempotent MERGE). A sustained-throttle chunk is **retried, not skipped**
(cursor not advanced) so no data is lost; after `EXTRACTION_HTTP_MAX_RETRIES` stalls it
advances with a loud log so one bad window can't wedge the pipeline.

**Constraints (ADR-0013):** NEVER writes trusted topology. Candidates are operational
labels with endpoint ids as **string properties** (not relationships) → invisible to
traversal/search/counts. Promoted edges (P2) will carry `provenance_tier='literature'`.

**Files:** `backend/agents/extraction_agent.py`, `backend/extraction/{dictionary,ingest,relation,stage,cursor,backfill}.py`,
`backend/llm/prompts/extraction.py`. Design: `docs/design/feature-2-literature-extraction.md`.

---

## v2 Agents (define now, build later)

> **LiteratureAgent is BUILT** as the **ExtractionAgent** (MVP §3 above, Feature 2 P1).
> Its original sketch here is superseded by [ADR-0013](docs/adr/0013-literature-extraction-trust-model.md):
> staging label is `CandidateEdge`/`CandidateEvidence` (not `EdgeCandidate`), and
> promoted edges carry `provenance_tier='literature'` (not `source:agent_extracted`).
> What remains for v2 is the **promotion gate** below.

### ValidationAgent (Feature 2 P2 — promotion gate) — BUILT (mechanism)

**Role:** Promote a `CandidateEdge` into a REAL typed edge tagged
`provenance_tier='literature'` + `source_db='literature_extracted'` + supporting
`pmids` — the only path that writes trusted topology. Re-checks `trusted_edge_exists`
at promote time (canonical edge appeared since staging → enrich, don't duplicate).
Idempotent MERGE. `reject` keeps the candidate flagged, never re-proposed.

**Trigger:** Manual `POST /admin/candidates/{triple_key}/{approve,reject}` (the safe
path). An auto-promote pass (`POST /admin/agents/validation/run`) exists but is
**default-OFF** (`VALIDATION_AUTO_PROMOTE_ENABLED`) — auto-promote is **uncalibrated**
until the precision harness (`RUN_EXTRACTION_EVAL`) produces a number. All writes gated
on the feature master switch `EXTRACTION_AGENT_ENABLED`.

**Files:** `backend/agents/validation_agent.py`. Auto-promote policy: confidence ≥
`VALIDATION_AUTO_PROMOTE_CONFIDENCE` AND `n_affirm` ≥ `VALIDATION_MIN_INDEPENDENT_PMIDS`
AND no contradicting evidence. Promoted edges carry the supporting `pmids` and are
rendered distinctly in the UI ("proposed", literature tier).

---

### FreshnessAgent (v2 — not built)

**Role:** Monitor upstream data sources for new versions. Alert when source DB version changes.

**Trigger:** Monthly cron.

**Sources monitored:**
- GENCODE — check latest release vs loaded version
- GTEx — check latest release
- DoRothEA — check GitHub release
- HGNC — monthly diff

**Flow:**
```
for each source:
  → fetch current version from source API/page
  → compare to DataSource node in graph (loaded_version)
  → if newer version available:
      → log FreshnessAlert node
      → send notification (email / webhook)
      → optionally trigger ETL script (manual approval required)
```

**Constraints:**
- Never auto-runs ETL — human approval required
- Notification only — no graph writes except FreshnessAlert log node

**Files:** `backend/agents/freshness_agent.py`

---

## Agent communication pattern

Agents do not call each other directly. Coordination via Neo4j graph nodes:

```
CitationAgent    reads  → (:Edge {pmids: []})
CitationAgent    writes → (:Edge {pmids: [...], citation_attempted: true})

ExtractionAgent  writes → (:CandidateEdge {status: "pending"})
ValidationAgent  reads  → (:CandidateEdge {status: "pending"})
ValidationAgent  writes → a real typed edge (provenance_tier='literature') or
                           (:CandidateEdge {status: "rejected"})

FreshnessAgent   writes → (:FreshnessAlert)
ETL scripts      reads  → (:FreshnessAlert) [manual trigger]
```

Graph = shared state / message bus. No inter-agent HTTP calls.

---

## Agent safety rules (all agents)

1. **No hallucinated topology** — agents never assert biological relationships from LLM output alone
2. **Cite everything** — every agent-written property must trace to a PMID or source DB
3. **Idempotent** — re-running any agent produces same result, no duplicate writes
4. **Labeled provenance** — every agent-written edge/node carries `source_agent`, `agent_version`, `run_timestamp`
5. **Fail loud** — agent errors written to `AgentRunLog` node, surfaced in admin UI
6. **Scope locked** — each agent touches only its defined node/edge types, enforced at code level

---

## Admin endpoints (FastAPI)

```
POST /admin/agents/citation/run           → trigger CitationAgent manually
POST /admin/agents/embedding/run          → trigger EmbeddingAgent manually (one batch)
GET  /admin/agents/{citation,embedding}/log → last N run-log nodes
POST /admin/agents/extraction/run         → trigger ExtractionAgent delta (gated: EXTRACTION_AGENT_ENABLED)
GET  /admin/agents/extraction/candidates  → pending CandidateEdges ≥ confidence floor
GET  /admin/agents/extraction/log         → last N ExtractionRun nodes
POST /admin/agents/extraction/backfill/start   → arm cursors + launch historical backfill (gated)
POST /admin/agents/extraction/backfill/pause   → pause the backfill at the next chunk boundary
POST /admin/agents/extraction/backfill/resume  → resume a paused backfill (gated)
GET  /admin/agents/extraction/backfill/status  → both cursors' dates/status/counters
POST /admin/agents/validation/run         → auto-promote pass (gated: VALIDATION_AUTO_PROMOTE_ENABLED, off by default)
GET  /admin/agents/validation/log         → last N ValidationRun nodes
GET  /admin/candidates?status=            → review-dashboard queue (P3) — NOT confidence-floor-gated, unlike above
GET  /admin/candidates/{triple_key}       → candidate detail: resolved endpoint names, evidence chain, would_be_action preview (P3)
POST /admin/candidates/{triple_key}/approve → promote CandidateEdge (ValidationAgent, P2)
POST /admin/candidates/{triple_key}/reject  → reject CandidateEdge (P2)
POST /admin/candidates/{triple_key}/revert  → undo a promotion, exact-delta (P3, ADR-0014)
GET  /admin/freshness                     → FreshnessAlert nodes (v2)
```
