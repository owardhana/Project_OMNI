# Design plan — Server / cloud migration

Status: **Plan / deferred.** Not started; no ADR yet. Captures the target architecture
for moving OmniGraph off the local iCloud-synced laptop onto a free 24/7 host, and the
open decisions (Oracle vs a managed sidecar, API vs local model). Perishable free-tier
facts are mirrored in the `project_oracle_migration_deferred` memory note — re-verify
before acting.

## Why migrate

The graph + backend currently run on a macOS laptop under an iCloud-synced directory,
where tooling (tsc/pytest/uvicorn) and the ETL crawl. A dedicated 24/7 box gets the
project off the laptop and lets long jobs (the `06` UniProt crawl, a future literature
backfill) run unattended.

## Architecture: keep Neo4j the core; sidecar only if needed

OmniGraph is a **graph**. Cypher traversal (signal-decay, shortest-path, the backbone
pre-pass) is the product. A relational/NoSQL store (Supabase=Postgres, Firebase=NoSQL
doc) **cannot replace Neo4j** without rewriting traversal as recursive CTEs and losing
the whole model. So:

- **Graph core → self-hosted Neo4j on Oracle Cloud Always-Free Ampere A1.** It is the
  only always-free tier large enough: AuraDB Free caps at ~200k nodes / 400k rels and
  OmniGraph is ~622k / 2.04M (3–5× too big); every other cloud's always-free VM is ~1 GB
  RAM while Neo4j here wants 8 GB+. A1 is also architecture-identical to the dev laptop
  (both arm64), so porting risk is near-zero.
- **Supabase/Firebase = optional relational *sidecar*, not the graph host.** They fit
  adjacent, relational needs if/when they arrive: user accounts/auth, saved queries,
  chat history (Feature 1), the `:CandidateEdge` staging + review queue (Feature 2).
  **But none of that is needed yet** — chat history already lives in Neo4j
  (`:ChatSession`/`:ChatTurn`), and Feature 2 staging can be `:CandidateEdge` in Neo4j.
  A single datastore is materially simpler to operate on one constrained free box.
  **Recommendation: stay Neo4j-only now; add Supabase only when real multi-tenant user
  accounts land.** It is not an either/or with Oracle — Supabase would run on its own
  managed free tier, not the A1 VM.

## Oracle A1 free-tier facts (verified 2026-06-30 — perishable)

- **Always-Free A1 was cut June 2026: 4 OCPU / 24 GB → 2 OCPU / 12 GB** for free-tier
  accounts. The full 4 OCPU / 24 GB is free only under **Pay-As-You-Go** (stays $0
  within Always-Free limits).
- **Idle reclamation:** Always-Free compute is reclaimed after a ~7-day idle window
  (95th-pct CPU <10% + low network). **PAYG accounts are exempt.** So a single PAYG
  upgrade fixes both the RAM cap *and* the 24/7-reliability risk — recommended.
- **Capacity friction:** free ARM A1 instances are frequently "out of capacity" at
  provision time in popular regions; expect retries / alternate ADs.
- **Sizing:** 12 GB free is workable for ~622k nodes (heap 4G + pagecache 4G + OS +
  backend, tight); 24 GB (PAYG) is comfortable and leaves headroom. Make Neo4j memory
  env-configurable so the same compose works at either tier.

## API vs local model (compute)

Split by feature — this is the real fork:

- **Feature 1 (chat):** interactive, low volume. OpenRouter API cost is pennies/query.
  **Keep API. No GPU.** (Already built that way.)
- **Feature 2 (literature extractor):** high volume (nightly + millions-paper backfill).
  API at that scale is real money, and **Oracle A1 has no free GPU** (ARM CPU only; GPU
  instances are paid — ignore "free GPU" blog clickbait, that's the $300 trial credit).
  **Hybrid:** a quantized local LLM on the A1 CPU (llama.cpp) for bulk NER + a cheap
  first relation pass (free, fine for nightly batch throughput), with tiered API (cheap
  model) only for disambiguation/relation extraction on surviving candidates. Backfill =
  throttled batch or a one-time budgeted spend. See
  [`feature-2-literature-extraction.md`](feature-2-literature-extraction.md).

## Migration mechanics (when pursued)

1. **Provision** an A1 instance (Ubuntu/Oracle Linux, arm64); upgrade to PAYG to dodge
   idle reclamation + unlock 24 GB.
2. **Firewall — two layers** (the classic Oracle gotcha where SSH works but nothing
   else does): open ports in BOTH the OCI **Security List / NSG** *and* the instance's
   **iptables/firewalld**. Expose only what's needed; put Caddy/Nginx with TLS in front.
3. **Move the graph:** prefer `neo4j-admin database dump`/`load` from a known-good local
   graph (fast, exact) over a full ETL rebuild (deterministic but needs raw data + keys
   + hours, and has the 04↔05 cold-bootstrap wrinkle). Keep ETL-rebuild as the fallback.
4. **Run** Neo4j + FastAPI via the prod compose (env-driven memory); `vite build` the
   frontend to static and serve via Caddy. Secrets via env, never committed.
5. **Backups:** scheduled `neo4j-admin dump` to A1 block storage / object storage —
   self-hosting means you own reliability (vs AuraDB's managed backups).

## Relationship to the AuraDB trigger (don't conflate)

Oracle A1 and AuraDB Professional (~$65/mo) are **orthogonal axes**:
- A1 changes *where the free tier runs* (self-hosted, 24/7).
- AuraDB Pro is the *Community→Enterprise capability* trigger (ENCODE/cCRE volume, RBAC,
  managed backups), unchanged by an A1 move.

If pursued, write it up as a future ADR placing Oracle A1 *between* "local MVP" and
"AuraDB Pro" — a new free-tier point, not a replacement for the AuraDB trigger.

## Risks

- Idle reclamation (→ PAYG), A1 capacity at provision (→ retries/region), self-managed
  reliability/backups (→ scheduled dumps), 12 GB tightness on free tier (→ PAYG 24 GB).
