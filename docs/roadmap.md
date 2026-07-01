# OmniGraph — Current State & Roadmap

Where the build is, what's loaded, and what's deferred. For the data model see
[`data-architecture.md`](data-architecture.md); for product scope and design see
[`vision-and-mvp.md`](vision-and-mvp.md).

---

## Current graph state

Live in Neo4j Community 5.x (Docker, named volume).

| Metric | Value |
|--------|-------|
| Total nodes | ~622,800 |
| Total relationships | ~2.04M |
| Protein | **20,077** (full proteome — ADR-0010; 117 TFs keep `subtype`) |
| Variant / Transcript / Gene / Disease | 325,052 / ~221k / ~42k / 13,203 |
| Metabolite | 1,345 (HMDB-enriched; `catalyses_degree` persisted for the bridge gate) |
| `INTERACTS_WITH` | 101,464 (STRING @ 0.95) |
| `CATALYSES` | 24,560 (94% of metabolites connected) |
| `DIFFERENTIALLY_EXPRESSED` | 128,714 (16 TCGA cohorts) |
| `Variant.gnomad_af` | backfilling on the production box — ~325k rs-variants via Ensembl REST (16_gnomad_af; ~11% at cutover, resumable) |
| `cancer_gene` flags | 752 (580 tier-1, 172 tier-2; COSMIC v104) |

---

## Done

- **Phases 1–2 (genomics → proteomics → disease):** Gene/Transcript/Protein/Variant/
  Disease nodes; REGULATES, PRODUCES, TRANSLATES_TO/ENCODES, INTERACTS_WITH, IN_GENE,
  ASSOCIATED_WITH, IMPLICATED_IN. Text2Cypher, citation + embedding agents, semantic
  search, 3D viz, Entity Browser, shortest-path.
- **Full proteome (ADR-0010):** Protein 117 → 20,077, minted directly by
  `05_proteins.py` (TRANSLATES_TO + ENCODES, TF-subtype tag, REGULATES migration).
  This connected the metabolite layer (CATALYSES 8 → 24,545). STRING re-run at the
  raised 0.95 threshold (INTERACTS_WITH 642 → 101,684).
- **Cancer / differential expression:** COSMIC CGC v104 flags; TCGA matched
  tumour-vs-adjacent-normal log2FC DE edges.
- **Metabolomics (ADR-0009):** Recon3D `.mat` (scipy) → 1,281 Metabolite nodes +
  CATALYSES; HMDB streamed for canonical names. Layer-Z shift (Disease 900→1200,
  metabolomics plane at 900). Frontend metabolomics layer + colour deconfliction
  (metabolite cyan, not TF amber).
- **Backbone-guaranteed traversal (ADR-0011):** a seed's own vertical omics chain
  — including the metabolites its protein catalyses — is guaranteed present via a
  pre-pass; metabolites are terminal leaves (no cofactor flood). This resolved the
  former "gene seeds show no metabolites / few non-TF proteins" gap. Verified:
  LDHA → 15 metabolites (was 0); TP53 → 0 (correct, no metabolic backbone);
  metabolite-seeded views unchanged.
- **Metabolite "bridge" connectivity (ADR-0012):** opt-in (default OFF) — a discovered
  metabolite can expand to its co-catalysing proteins, gated by a *data-driven*
  cofactor signal (`Metabolite.catalyses_degree`, persisted by 14_metabolomics) instead
  of a hand list. Flag-OFF reproduces ADR-0011 exactly (verified TP53→0, LDHA→15,
  L-Lactic→110/77); flag-ON is dense-capped + degree-gated (Proton/Water/ATP self-exclude).
- **Variant-level gnomAD allele frequency:** new `16_gnomad_af.py` sets
  `Variant.gnomad_af` from Ensembl REST (`pops=1`, gnomADg/e:ALL), resumable,
  ClinVar-variants first. Long backfill (~325k rs-variants) runs detached.
- **Agentic chatbot (Feature 1):** `ChatAgent` tool-loop over read-only graph tools
  (search / subgraph / shortest-path / read-only Cypher) with SSE streaming + Neo4j
  conversational memory (`:ChatSession`/`:ChatTurn`). Endpoint `/api/chat/stream` (SSE);
  frontend `ChatPanel`. Verified live (TP53↔EGFR path, LDHA metabolites).
- **ETL index self-sufficiency:** `run_pipeline.ensure_indexes()` creates MERGE-key
  B-tree indexes before load — a bare rebuild (no backend) was previously index-free and
  hung on quadratic MERGEs.
- **Cloud deployment — 24/7 production (shipped 2026-07-01):** self-hosted on a free
  Oracle Cloud Ampere A1 VM via [`docker-compose.prod.yml`](../docker-compose.prod.yml)
  — Neo4j (private, loopback-bound) + FastAPI backend (private) + **Caddy** (the only
  public service; serves the built frontend, proxies `/api` with SSE, auto-HTTPS-ready).
  Graph moved by offline dump → scp → restore (`scripts/dump_graph.sh` /
  `restore_graph.sh`), not re-ETL; the enrichment crawls finish on the always-on box.
  Verified live end-to-end (frontend + `/api/gene/TP53` both HTTP 200 from the public
  internet). Runbook: [`docs/deploy/oracle-runbook.md`](deploy/oracle-runbook.md).

### Verification notes
- Layer-Z: `METABOLITE_LAYER_Z=900`, `DISEASE_LAYER_Z=1200` (constants, audited;
  no stray hardcoded `900`).
- `is_tf` derivation requires `subtype='transcription_factor'` post-full-proteome
  (else every protein-coding gene flags); fixed in genes/graph/traversal queries.
- pytest runs against live Neo4j; module import through the iCloud-synced project
  dir is pathologically slow, so data gates are also confirmed via direct Cypher.

---

## In progress (running on the production box)

The enrichment crawls now run 24/7 on the deployed Oracle A1 VM — they kept dying under
laptop contention, so the always-on box is their proper home. Both are resumable +
`IS NULL`-guarded; watch coverage climb per the runbook's Phase 8.

- **`06_uniprot_enrich`** — function text over the full ~20k proteome (~85% loaded at
  cutover). Semantic protein search completes once the embedding backfill drains the
  newly-enriched proteins (embeddings ~0% at cutover — needs the *driven* backfill, not
  the nightly batch=50 agent; runbook Phase 7).
- **gnomAD AF backfill** — `16_gnomad_af.py` populating `Variant.gnomad_af` over ~325k
  rs-variants (~11% at cutover; the long pole, ~1–1.5 days; ClinVar-significant first).

## Deferred / optional

- **GTEx tissue panel expansion**, **co-expression networks**
  (`CO_EXPRESSED_WITH`, needs TCGA+GTEx counts in one pipeline), **cell-type
  resolution** (indefinitely deferred — data too noisy vs tissue level).
- **Literature extraction agent** — new-edge proposals. Design **locked** (grill
  session 2026-07-01): closed-world, dictionary-linked, 2 edge types
  (`INTERACTS_WITH`+`IMPLICATED_IN`), cheap-LLM relation gate, `:CandidateEdge`
  staging, local-only MVP. Plan:
  [`docs/design/feature-2-literature-extraction.md`](design/feature-2-literature-extraction.md);
  trust model: [ADR-0013](adr/0013-literature-extraction-trust-model.md). Build is the
  next session (P1 = extraction-to-staging; promotion gate + backfill are P2/P3).
- **Horizontal metabolite reach-through for pure-TF seeds** — surfacing
  metabolites that belong to a TF's regulated genes. Explicitly rejected as the
  current floor (semantically muddier; ADR-0011 "Rejected alternatives"); the
  ADR-0012 bridge covers the simpler shared-substrate case instead.

---

## Gated — ENCODE / cCREs (Phase 9)

ENCODE regulatory elements (`cCRE` nodes, `BINDS` Protein→cCRE, `REGULATES_VIA`
cCRE→Gene) are **intentionally not started.** The 1.7M cCRE nodes OOM on Neo4j
Community Edition; `15_encode.py` is hard-gated and refuses to start unless >500k
nodes are present (indicating an AuraDB migration has occurred) or
`ENCODE_FORCE_LOAD=true` is set. **Never force-load on Community.**

**Migration triggers to AuraDB Professional (~$65/month):** node count
materially exceeds Community headroom *for cCRE volume specifically* / pagecache
miss rate > 30% / production reliability or multi-user RBAC required. The current
~622k total nodes is above the 500k figure, but that figure is an ENCODE-cCRE
volume gate, not a hard node cap — Phase 9 stays user-driven.

Other infra triggers: >500 ms ANN vector-search latency → revisit the native
vector index (ADR-0008); ≥3 agents needing independent schedules → add a
Prefect/Dagster orchestrator (deferred until then).
