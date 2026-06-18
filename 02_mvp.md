# OmniGraph — MVP Specification

> ✅ **Phase 1 complete.** This spec is implemented and live on `main`.
> Phase 2 (full proteome, STRING PPIs, Variants, Disease nodes, semantic search)
> is planned in [06_data_vision.md](06_data_vision.md) and implemented via
> [07_phase2_build_prompt.md](07_phase2_build_prompt.md) on branch
> `phase-2-multiomics-expansion`. Do not use this file as a guide for new work —
> use the Phase 2 documents instead.

## Goal

Working demo for computational biology collaborators at ~3 months.
Success = user queries a gene, sees 3D graph of TF regulators + transcripts across 3 tissues, clicks an edge and sees citations.

---

## Scope

### In scope (MVP)

- Gene nodes (Ensembl ID, HGNC symbol, description)
- Transcript nodes (Ensembl TX ID, biotype)
- **Protein nodes — transcription-factor slice only** (UniProt ID; `entity_kind=protein`, `subtype=transcription_factor`). See [ADR-0004](docs/adr/0004-transcription-factors-as-proteins.md).
- **Protein(TF) → Gene** `REGULATES` edges (DoRothEA A-B confidence, directed, downward proteomics→genomics)
- Gene → Transcript `PRODUCES` edges (GENCODE structure + GTEx tissue weights)
- **Transcript → Protein `TRANSLATES_TO`** (primary) / **Gene → Protein `ENCODES`** (fallback) — tie a TF protein to its molecule
- 3 tissues: whole blood, liver, brain (prefrontal cortex)
- 3D layered visualization — **three layers**: genomics, transcriptomics, proteomics (exact Z/colors set by UI tasks)
- Gene/TF search by HGNC symbol
- **Signal-decay traversal** (confidence-gated, user hard cap) — replaces fixed 1-2 hop bound. See [ADR-0005](docs/adr/0005-signal-decay-traversal.md).
- Edge detail panel (type, confidence, tissue weights, PMIDs)
- Text2Cypher query (natural language → Cypher → result)
- Citation agent (PubMed PMID attachment to existing edges)

### Out of scope (MVP)

- **Full proteome** (non-TF proteins) / metabolite layer
- Protein → Protein and Protein → Metabolite edges
- Cancer or perturbation data
- New edge extraction by agent (topology creation)
- Vector RAG
- User accounts / saved queries
- API access for programmatic queries
- Cell-type resolution

---

## Data sources

| Source | Data loaded | Format | Script |
|--------|------------|--------|--------|
| HGNC | Gene symbols + ID mapping + `uniprot_ids` (→ Protein) | TSV (download) | `etl/01_hgnc.py` |
| GENCODE v46 | Gene + transcript structure | GTF | `etl/02_gencode.py` |
| GENCODE v46 SwissProt metadata | ENST → UniProt (`TRANSLATES_TO`) | `metadata.SwissProt.gz` | `etl/05_proteins.py` |
| GTEx v10 | Tissue expression weights (blood/liver/brain) | median TPM TSV | `etl/03_gtex.py` |
| DoRothEA A-B | TF (protein) → target edges + confidence | `.rda` (pyreadr) | `etl/04_dorothea.py` |
| PubMed (citation agent) | PMIDs for edge enrichment | API | `agents/citation_agent.py` |

Load order: HGNC → GENCODE → GTEx → **proteins (`05_proteins.py`)** → DoRothEA.
Proteins must be minted before `04_dorothea.py`, whose `REGULATES` source is now a
`:Protein` matched by TF symbol (ADR-0004).

---

## Graph schema (Neo4j)

### Node: Gene
```cypher
(:Gene {
  ensembl_id: "ENSG00000139618",   // canonical key
  hgnc_symbol: "BRCA2",            // display + search
  hgnc_id: "HGNC:1101",
  description: "BRCA2 DNA repair...",
  chromosome: "13",
  biotype: "protein_coding"
})
```

### Node: Transcript
```cypher
(:Transcript {
  ensembl_tx_id: "ENST00000380152",
  hgnc_symbol: "BRCA2-201",
  biotype: "protein_coding",
  length_bp: 10257
})
```

### Node: Protein (TF slice only — ADR-0004)
```cypher
(:Protein {
  uniprot_id: "P04637",           // canonical key
  hgnc_symbol: "TP53",            // display + REGULATES match + citation search
  entity_kind: "protein",
  subtype: "transcription_factor" // only subtype in MVP
})
```

### Edge: REGULATES (Protein[TF] → Gene)
```cypher
(:Protein)-[:REGULATES {          // was (:Gene)->(:Gene); now protein-sourced, downward
  mode: "activator" | "repressor" | "unknown",
  confidence: 0.92,               // DoRothEA score
  confidence_tier: "A",           // A or B
  source_db: "DoRothEA",
  source_version: "v1.0",
  pmids: ["12345678", "23456789"]
}]->(:Gene)
```

### Edge: TRANSLATES_TO (Transcript → Protein) / ENCODES (Gene → Protein, fallback)
```cypher
(:Transcript)-[:TRANSLATES_TO { source_db: "GENCODE_SwissProt" }]->(:Protein)
(:Gene)-[:ENCODES { source_db: "HGNC" }]->(:Protein)   // only when no transcript link
```

### Edge: PRODUCES (Gene → Transcript)
```cypher
(:Gene)-[:PRODUCES {
  tissue_weights: {
    whole_blood: 0.73,
    liver: 0.45,
    brain_prefrontal_cortex: 0.88
  },
  source_db: "GENCODE+GTEx",
  gencode_version: "v46",
  gtex_version: "v10",
  pmids: []
}]->(:Transcript)
```

### Node: DataSource (metadata)
```cypher
(:DataSource {
  name: "DoRothEA",
  version: "v1.0",
  loaded_at: "2026-06-15",
  record_count: 47823
})
```

---

## Tech stack

```
Frontend:   React + TypeScript + Vite + react-force-graph-3d (Three.js)
Backend:    FastAPI (Python 3.11+)
Graph DB:   Neo4j Community 5.x (self-hosted, Docker)
LLM:        OpenRouter API (OpenAI-compatible) — Text2Cypher + citation agent
ETL:        Python scripts (pandas, neo4j driver)
```

### Docker compose (local dev)
```yaml
services:
  neo4j:
    image: neo4j:5
    ports: ["7474:7474", "7687:7687"]
    environment:
      NEO4J_AUTH: neo4j/password
      NEO4J_PLUGINS: '["apoc"]'
    volumes: ["./data/neo4j:/data"]

  api:
    build: ./backend
    ports: ["8000:8000"]
    depends_on: [neo4j]

  frontend:
    build: ./frontend
    ports: ["3000:3000"]
    depends_on: [api]
```

---

## 3D Visualization

### Layer structure (graphite model)

**Three** stacked layers now (ADR-0004). Bottom→top: genomics, transcriptomics,
proteomics. Genes sit in genomics, transcripts in transcriptomics, **TF proteins
in proteomics**. Exact Z coordinates, plane colors, node colors/shapes, and force
spread are set by the **UI restyle (task #1)** and **layout (task #5)** decisions —
not fixed here.

```
[ PROTEOMICS    ]  ← Protein (TF) nodes      (NEW)
[ TRANSCRIPTOMICS]  ← Transcript nodes
[ GENOMICS      ]  ← Gene nodes
```

Node color (⚠ palette superseded by UI restyle, task #1 — values below are the
current/legacy scheme; TF is now a Protein subtype, not a gene):
- Gene: `#4ade80` (green)
- TF protein: `#f59e0b` (amber)
- Transcript: `#60a5fa` (blue)
- _(other protein subtypes: future)_

Edge color:
- REGULATES (activator): `#22c55e`
- REGULATES (repressor): `#ef4444`
- PRODUCES: `#a78bfa`
- TRANSLATES_TO / ENCODES: _(to be set, task #1)_

Layer planes rendered as transparent `PlaneGeometry` in Three.js.
Node Z-position fixed by type, X/Y free-simulated (force layout within layer).

### Tissue filter
Toggle: All / Blood / Liver / Brain.
**Continuous opacity, never removal** (ADR-0006): the active tissue scales
PRODUCES-edge + transcript opacity by `tw_<tissue>` (weakly-expressed fade, never
disappear). "All" = full opacity. Tissue is a visual channel only — it does not
drop nodes/edges and does not feed traversal signal.

---

## Backend API

```
GET  /api/gene/{hgnc_symbol}          → gene node + immediate neighbors
GET  /api/gene/{hgnc_symbol}/graph    → subgraph via signal-decay traversal
        params: tissue, min_signal (ε), decay (d), max_nodes  (replaces max_hops — ADR-0005)
GET  /api/transcript/{ensembl_tx_id}  → transcript node
GET  /api/search?q={symbol}           → fuzzy HGNC symbol search
POST /api/query                       → { "question": "..." } → Text2Cypher result
GET  /api/edge/{id}                   → edge detail + pmids
```

---

## Text2Cypher (RAG layer)

### Flow
```
User: "What TFs repress TP53 in liver?"
  ↓
System prompt: schema description + Cypher examples
  ↓
Claude API → generates Cypher
  ↓
FastAPI validates + executes against Neo4j
  ↓
Result formatted → Claude API synthesizes natural language answer
  ↓
Citations (PMIDs) attached to response
```

### Cypher generation prompt (skeleton)
```
You are a Neo4j Cypher expert for a multi-omics knowledge graph.

Schema:
- (:Gene {ensembl_id, hgnc_symbol, biotype})
- (:Transcript {ensembl_tx_id, hgnc_symbol, biotype})
- (:Protein {uniprot_id, hgnc_symbol, subtype})   // TFs; subtype='transcription_factor'
- (:Protein)-[:REGULATES {mode, confidence, confidence_tier, pmids}]->(:Gene)
- (:Gene)-[:PRODUCES {tw_<tissue>, pmids}]->(:Transcript)
- (:Transcript)-[:TRANSLATES_TO]->(:Protein) / (:Gene)-[:ENCODES]->(:Protein)

Rules:
- A transcription factor is a (:Protein); REGULATES is Protein→Gene, never Gene→Gene
- Always filter by confidence_tier IN ['A','B'] for REGULATES edges
- Use hgnc_symbol for protein/gene lookup
- Return pmids on edges
- For tissue queries, check r.tw_<tissue> > 0.3 (flat props, not a map — ADR-0001)

Question: {user_question}
Return only the Cypher query, no explanation.
```

---

## Citation Agent

### Trigger
Runs nightly (cron). Processes edges with `pmids: []`.

### Flow
```python
for edge in graph.get_edges_without_citations(limit=100):
    entity_a = edge.source.hgnc_symbol
    entity_b = edge.target.hgnc_symbol
    pmids = pubmed_search(f"{entity_a} {entity_b} regulation", max_results=5)
    validated = [p for p in pmids if abstract_mentions_both(p, entity_a, entity_b)]
    graph.attach_pmids(edge.id, validated)
```

### PubMed search
- API: NCBI E-utilities (free, no key needed for low volume)
- Validate: fetch abstract, check both entity names appear in title/abstract
- Store: PMID list only — no full text, no LLM-generated claims

---

## ETL pipeline

### Run order
```bash
python etl/01_hgnc.py          # ~2 min, loads gene ID mapping (+ uniprot_ids)
python etl/02_gencode.py       # ~10 min, loads gene + transcript nodes
python etl/03_gtex.py          # ~15 min, loads tissue weights onto PRODUCES edges
python etl/05_proteins.py      # mints TF Protein nodes + TRANSLATES_TO/ENCODES
python etl/04_dorothea.py      # ~5 min, loads Protein(TF)→Gene REGULATES edges
```
Note: `05_proteins.py` runs before `04_dorothea.py` — REGULATES now starts at a
`:Protein` (ADR-0004). (Script number ≠ run order; kept for git history.)

### Idempotency
All scripts use `MERGE` (not `CREATE`) in Cypher — safe to re-run on updates.
Each run logs to `DataSource` node with timestamp + record count.

---

## Build timeline

### Month 1 — Data foundation
- Week 1: Neo4j Docker setup + schema design
- Week 2: ETL scripts (HGNC + GENCODE)
- Week 3: ETL scripts (GTEx + DoRothEA)
- Week 4: FastAPI skeleton + basic graph queries working

### Month 2 — Backend + LLM
- Week 1: REST API endpoints
- Week 2: Text2Cypher integration (Claude API)
- Week 3: Citation agent (PubMed E-utilities)
- Week 4: Query testing + edge case handling

### Month 3 — Frontend + demo polish
- Week 1: React scaffold + react-force-graph-3d basic render
- Week 2: Graphite layer viz (fixed Z positions, layer planes)
- Week 3: Search UI + edge detail panel + tissue filter
- Week 4: Demo polish + collaborator testing

---

## MVP success criteria

- [ ] Graph loads: >40k gene nodes, >200k transcript nodes, ~6.4k Protein(TF)→Gene REGULATES edges (DoRothEA A-B; the old ">50k" gate is a spec error — ADR-0003), + a Protein node per DoRothEA TF
- [ ] Query "TP53" → 3D graph renders in <3s
- [ ] Tissue filter changes edge/transcript **opacity** correctly (nothing disappears — ADR-0006)
- [ ] Text2Cypher answers 5 benchmark questions correctly
- [ ] Each edge shows at least 1 PMID (citation agent)
- [ ] Demo walkthrough <10 min for new user

---

## Known risks

| Risk | Mitigation |
|------|-----------|
| DoRothEA A-B edges sparse for some TFs | Add B-tier, lower threshold if needed |
| react-force-graph-3d slow >10k nodes | Implement node culling + LOD |
| Text2Cypher generates invalid Cypher | Validate + retry loop, fallback error message |
| GTEx tissue weights missing for some transcripts | Null-safe edge properties, show "no data" in UI |
| Neo4j Community memory limits | Index only queried properties, use APOC for bulk ops |

---

## v2 roadmap (post-demo)

- Protein layer (UniProt + STRING PPIs)
- Cancer data scope (TCGA differential expression)
- Vector RAG (hybrid query mode)
- Agent topology extraction (new edge proposals + validation queue)
- API access for programmatic queries
- Cell-type resolution (CellxGene integration)
- Metabolomics layer (KEGG/Recon3D)
