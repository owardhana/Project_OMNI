# OmniGraph

A multi-omics knowledge graph for human biology — a navigable, tissue-segmented,
literature-cited map of molecular causality from TF binding through transcription,
translation, and protein interaction to metabolic output and disease. Queryable in
plain English, explorable as a 3D layered graph.

```
[ Phenotype       ]  ← Disease                  (EFO)
[ Metabolomics    ]  ← Metabolite               (HMDB / ChEBI)
[ Proteomics      ]  ← Protein                  (UniProt; TFs are a subtype)
[ Transcriptomics ]  ← Transcript               (Ensembl ENST)
[ Genomics        ]  ← Gene, Variant            (Ensembl ENSG / rsid)
```

Vertical edges are the molecular backbone (`PRODUCES`, `TRANSLATES_TO`/`ENCODES`,
`CATALYSES`); a transcription factor is a **Protein** that acts downward on a gene
via `REGULATES`. Tissue is a visual opacity channel, not a separate graph.

**~622k nodes · ~2.04M relationships** — full ~20k proteome, STRING PPIs, GWAS
variants, EFO diseases, TCGA differential expression, and a connected Recon3D
metabolite layer. See [`docs/roadmap.md`](docs/roadmap.md) for the live tally.

Query it two ways: a single-shot **Text2Cypher** box, or an **agentic chat assistant**
(`ChatAgent`) that streams answers while calling read-only graph tools (search,
subgraph, shortest-path, read-only Cypher) with conversational memory. Runs locally,
or **24/7 in production** on a free Oracle Cloud VM — see
[`docs/deploy/oracle-runbook.md`](docs/deploy/oracle-runbook.md).

---

## Architecture

```
Frontend  React + TypeScript + Vite + react-force-graph-3d (Three.js)   :3000
Backend   FastAPI (Python 3.11+, async)                                  :8000
Graph DB  Neo4j Community 5.x (Docker)                          :7474 / :7687
LLM       OpenRouter (Text2Cypher, synthesis, embeddings, citations)
ETL       Python (pandas / scipy / neo4j driver), DAG runner
```

Module dependency rules (no circular deps): `frontend → backend API only`;
`backend/api → db + agents + llm`; `backend/db → Neo4j only`; `etl → Neo4j only`
(never imports backend). ETL is one-shot ingestion; agents run inside the backend.

## Repository layout

```
Project_OMNI/
├── README.md                  ← this file
├── docker-compose.yml         ← Neo4j (+ backend/frontend) — local dev
├── docker-compose.prod.yml    ← production stack (Neo4j + backend + Caddy)
├── .env.example               ← env var template (no secrets committed)
├── CONTEXT.md                 ← domain glossary (canonical terms)
├── AGENTS.md                  ← agent definitions + safety rules
│
├── docs/
│   ├── vision-and-mvp.md      ← why it exists, scope, design decisions
│   ├── data-architecture.md   ← data model + full field-level provenance catalog
│   ├── roadmap.md             ← current state + future/gated work
│   ├── adr/                   ← Architecture Decision Records (0001–0013)
│   ├── deploy/                ← Oracle Cloud production runbook
│   └── design/                ← forward-looking design docs (Feature 2, cloud migration)
│
├── etl/                       ← ingestion scripts, run in DAG order (01→16)
│   ├── 00_download.sh         ← fetch raw sources into data/raw/
│   ├── 01_hgnc.py … 16_gnomad_af.py
│   ├── run_pipeline.py        ← Python DAG runner (declares order, logs DataSource)
│   └── reference/             ← curated crosswalks (e.g. tcga_disease_to_efo.tsv)
│
├── backend/
│   ├── main.py                ← FastAPI entry point
│   ├── config.py              ← env-driven settings (never hardcode thresholds)
│   ├── api/{routes,models}    ← endpoints + Pydantic schemas
│   ├── db/{neo4j_client, queries}  ← connection pool + Cypher modules
│   ├── agents/                ← query, citation, embedding + chat agents (+ chat tools)
│   ├── llm/{client,prompts}   ← OpenRouter wrapper + versioned prompts
│   └── tests/                 ← pytest (Cypher correctness, agent safety)
│
├── frontend/src/{components,hooks,api,types,styles}
├── deploy/                     ← Dockerfile.web + Caddyfile + .env.prod.example
├── scripts/                    ← dump_graph.sh / restore_graph.sh (graph transfer)
├── data/{raw,processed,neo4j}  ← gitignored (large files + DB volume)
└── hpc/                        ← HPC / Singularity scaffolding
```

## Documentation map

| Doc | What it covers |
|-----|----------------|
| [`docs/vision-and-mvp.md`](docs/vision-and-mvp.md) | Vision, prior art, scope, and the finalized product/design decisions |
| [`docs/data-architecture.md`](docs/data-architecture.md) | Layer model, ETL patterns, **full provenance catalog**, indexes, conductance, tunables, agent writes |
| [`docs/roadmap.md`](docs/roadmap.md) | Current graph state, what's done, what's deferred/gated |
| [`docs/adr/`](docs/adr/) | Architecture Decision Records — the *why* behind irreversible choices |
| [`docs/deploy/`](docs/deploy/oracle-runbook.md) | Production deployment runbook (Oracle Cloud free tier) — provision → firewall → graph transfer → run → operate |
| [`docs/design/`](docs/design/) | Forward-looking design docs (literature-extraction agent, cloud migration rationale) |
| [`CONTEXT.md`](CONTEXT.md) | Domain glossary (canonical terms) |
| [`AGENTS.md`](AGENTS.md) | Agent definitions + safety rules |

Key ADRs: [0004](docs/adr/0004-transcription-factors-as-proteins.md) (TFs as
proteins) · [0005](docs/adr/0005-signal-decay-traversal.md) (signal-decay traversal)
· [0006](docs/adr/0006-tissue-as-visual-channel.md) (tissue as opacity) ·
[0009](docs/adr/0009-metabolomics-layer-4.md) (metabolomics layer) ·
[0010](docs/adr/0010-full-proteome.md) (full proteome) ·
[0011](docs/adr/0011-backbone-guaranteed-traversal.md) (backbone-guaranteed traversal) ·
[0012](docs/adr/0012-metabolite-bridge-connectivity.md) (metabolite bridge — opt-in) ·
[0013](docs/adr/0013-literature-extraction-trust-model.md) (literature-extraction trust model).

---

## Quickstart (local)

```bash
# 1. Start Neo4j (named volume — bind mounts EDEADLK on macOS)
docker compose up neo4j -d

# 2. Configure secrets
cp .env.example .env        # then fill in OPENROUTER_API_KEY, NEO4J_PASSWORD, etc.

# 3. Load data (one-time; topology = bulk files, enrichment = APIs)
bash etl/00_download.sh                       # fetch raw sources into data/raw/
etl/.venv/bin/python etl/run_pipeline.py      # runs 01→16 in dependency order

# 4. Backend
backend/.venv/bin/uvicorn backend.main:app --reload   # http://localhost:8000

# 5. Frontend
cd frontend && npm install && npm run dev              # http://localhost:3000
```

The ETL runner (`etl/run_pipeline.py`) enforces load order and logs each step to a
`DataSource` node. All scripts are idempotent (`MERGE`-based) — safe to re-run.

### Environment variables
See [`.env.example`](.env.example) for the full list. Secrets
(`OPENROUTER_API_KEY`, `NEO4J_PASSWORD`, `NCBI_API_KEY`) live in `.env`, which is
**gitignored and never committed**. All tunable thresholds (STRING/GWAS confidence,
traversal decay/floor/caps, TCGA log2FC, …) are env-driven — never hardcode them in
ETL or traversal code. Full table in
[`docs/data-architecture.md` §9](docs/data-architecture.md#9-tunable-parameters).

## Tests

```bash
backend/.venv/bin/python -m pytest backend/tests/ -q
```

`test_queries.py` checks Cypher correctness against the live Neo4j; `test_agents.py`
asserts the citation agent writes PMIDs only (never topology); `test_text2cypher.py`
checks benchmark questions produce valid read-only Cypher; `test_traversal_bridge.py`
locks the ADR-0011/0012 golden traversal values. (Note: pytest import is slow when the
repo lives under an iCloud-synced directory; data gates can also be confirmed via
direct Cypher.)

## Deployment (production)

OmniGraph runs 24/7 on a single free-tier **Oracle Cloud Ampere A1** VM via
[`docker-compose.prod.yml`](docker-compose.prod.yml): Neo4j (private, loopback-bound),
the FastAPI backend (private), and **Caddy** as the only public service — it serves the
built frontend and reverse-proxies `/api` (with SSE streaming for chat), auto-HTTPS when
pointed at a real domain.

```bash
# graph transfer (laptop → server): offline dump, not re-ETL
bash scripts/dump_graph.sh        # → dumps/neo4j.dump
# on the server, after copying the dump up:
docker compose -f docker-compose.prod.yml --env-file deploy/.env.prod up -d neo4j
bash scripts/restore_graph.sh
docker compose -f docker-compose.prod.yml --env-file deploy/.env.prod up -d --build
```

Full step-by-step (provision → two-layer firewall → graph transfer → run → operate →
troubleshoot) in **[`docs/deploy/oracle-runbook.md`](docs/deploy/oracle-runbook.md)**;
architecture rationale in [`docs/design/cloud-migration.md`](docs/design/cloud-migration.md).
The public chat endpoint is unauthenticated and spends against your OpenRouter key — set
a spend cap or add an auth/rate-limit gate before advertising the URL.
