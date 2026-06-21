# OmniGraph — Data Vision

> ✅ **Phase 2 complete** (2026-06-21, branch `phase-2-tests-and-review-fixes`, merged into main).
> Sections marked **[Phase 2]** describe what was implemented. Sections marked
> **[Phase 3 — deferred]** are the open backlog for the next build cycle.
> This document is a data engineering map — not a spec.
> Implementation details live in ADRs and ETL scripts.

---

## Phase 2 direction (implemented)

Phase 2 answered two biological question classes the MVP could not:
1. **Protein signaling chains** — how does a signal propagate from a TF through
   the protein interaction network?
2. **Disease mechanisms** — which genes/proteins/variants are implicated in a
   given disease, and how do they connect?

---

## Layer expansion [Phase 2 — implemented]

### Proteomics layer — full proteome ✅
Expanded from TF-only slice (~1,500 proteins) to all protein-coding genes
(~20k proteins via `etl/06_uniprot_enrich.py`). Machine ID = UniProt accession.

### Genomics layer — Variant nodes ✅
`(:Variant)` added as a new node type within the genomics layer (teal, sub-gene
resolution). Source: GWAS Catalog (p < 5×10⁻⁸) + ClinVar. ~30–50k unique nodes.
ENCODE regulatory elements (1.7M cCREs) remain deferred — infrastructure decision.

### Phenotype layer — Disease nodes ✅
`(:Disease)` as first-class graph nodes (4th layer, hot pink, Y=600 in 3D scene).
Machine ID = EFO ontology ID. Source: GWAS Catalog ontology terms. See ADR-0007.

---

## Node schemas [Phase 2 — implemented]

### Protein (expanded — currently TF slice only)
```
uniprot_id          string   canonical key (existing)
hgnc_symbol         string   display (existing)
entity_kind         string   "protein" (existing)
subtype             string   "transcription_factor" | "kinase" | ... (existing)
summary_text        string   UniProt function comment (NEW — for embedding)
molecular_weight    int      amino acids (NEW — from UniProt)
subcellular_loc     string   e.g. "nucleus", "cytoplasm" (NEW — from UniProt)
go_terms            [string] GO IDs as list (NEW — from UniProt/QuickGO)
embedding           [float]  1536-dim, text-embedding-3-small (NEW — agent)
```

### Gene (expanded)
```
ensembl_id          string   canonical key (existing)
hgnc_symbol         string   display (existing)
description         string   HGNC short label (existing)
chromosome          string   (existing)
biotype             string   (existing)
summary_text        string   NCBI Gene summary paragraph (NEW — for embedding)
pli_score           float    gnomAD loss-of-function intolerance (NEW)
cancer_gene         bool     flag from COSMIC/OncoKB (NEW)
embedding           [float]  1536-dim (NEW — agent)
```

### Variant (new node type)
```
rsid                string   canonical key (e.g. "rs7903146"); fallback: "chr:pos:ref:alt"
                             (GRCh38) for variants without rsids — same primary+fallback
                             pattern as TRANSLATES_TO/ENCODES for proteins
chromosome          string
position_grch38     int      GRCh38 coordinate
ref_allele          string
alt_allele          string
consequence_type    string   VEP consequence (e.g. "missense_variant") — from GWAS Catalog
cadd_score          float    deleteriousness score (0–40+)
gnomad_af           float    allele frequency (general population)
clinical_significance string  "pathogenic" | "likely_pathogenic" | "VUS" | "benign"
clinvar_id          string   optional
```
_Load filter: GWAS Catalog associations at p < 5×10⁻⁸ (genome-wide significance).
~30–50k unique Variant nodes expected. Controlled by `GWAS_MIN_SIGNIFICANCE` env var
(tunable scaling parameter — same pattern as `STRING_MIN_CONFIDENCE`)._

### Disease (new node type)
```
ontology_id         string   canonical key — EFO ID (e.g. "EFO_0001360")
name                string   display (e.g. "type 2 diabetes")
category            string   e.g. "metabolic disease"
omim_id             string   optional crosslink
description         string   trait description (NEW — for embedding)
embedding           [float]  1536-dim (NEW — agent)
```

---

## New edge types

| Edge | Label | Meaning | Source |
|------|-------|---------|--------|
| Protein → Protein | `INTERACTS_WITH` | Physical binding/interaction | STRING |
| Variant → Gene | `IN_GENE` | Variant maps to gene locus | GWAS Catalog, Ensembl VEP |
| Variant → Disease | `ASSOCIATED_WITH` | GWAS hit or ClinVar classification | GWAS Catalog, ClinVar |
| Gene → Disease | `IMPLICATED_IN` | Gene-level disease association | GWAS rollup / OpenTargets |

### Edge properties (new)

**INTERACTS_WITH (STRING):**
```
combined_score      float    STRING combined confidence (0–1)
experimental_score  float    experimental evidence sub-score
coexpression_score  float    co-expression sub-score
source_db           string   "STRING"
source_version      string
```
_Load threshold: `combined_score > 0.9` (~50k edges). Controlled by `STRING_MIN_CONFIDENCE`
env var alongside the existing `DOROTHEA_MIN_CONFIDENCE` — same tunable-scaling pattern._

**ASSOCIATED_WITH (Variant → Disease):**
```
p_value             float    GWAS p-value
beta                float    effect size (optional)
odds_ratio          float    odds ratio (optional)
source_db           string   "GWAS_Catalog" | "ClinVar"
pmids               [string]
```

---

## Data sources [Phase 2 — implemented]

| Source | Feeds | Format | New ETL script |
|--------|-------|--------|----------------|
| UniProt REST API | Protein `summary_text`, `go_terms`, `subcellular_loc` | JSON | `etl/06_uniprot_enrich.py` |
| STRING v12 (human) | `INTERACTS_WITH` edges | TSV | `etl/07_string.py` — initial load at `combined_score > 900` (~50k edges); configurable via `STRING_MIN_CONFIDENCE` env var (tunable scaling parameter — expand to >700 for ~130k edges or >400 for ~700k edges as graph matures) |
| GWAS Catalog | Variant nodes + `ASSOCIATED_WITH` edges | TSV | `etl/08_gwas.py` |
| ClinVar | Variant `clinical_significance` enrichment | VCF/TSV | `etl/09_clinvar.py` |
| NCBI Gene (E-utilities) | Gene `summary_text` | API | `etl/10_ncbi_summaries.py` |
| gnomAD (constraint) | Gene `pli_score` | TSV | `etl/11_gnomad.py` |

---

## Storage architecture

### Structured properties
All node/edge properties stored in Neo4j as primitive types or arrays of
primitives (consistent with ADR-0001).

### Semantic embeddings
- Model: `openai/text-embedding-3-small` via OpenRouter (1536-dim float32)
- Storage: `embedding: [float]` property on Gene, Protein, Disease nodes
- Index: Neo4j native vector index (`db.index.vector`) on each embedded label
- Scope: only nodes where `summary_text IS NOT NULL`
- Transcripts: **not embedded** (no meaningful free text)
- Population: background embedding agent (see below)

### Neo4j sizing
Current Docker config is tuned for MVP (~250k nodes). Next phase requires:
- Heap: 4G (up from 2G)
- Page cache: 4G (up from 1G)
Migration trigger to AuraDB Professional (~$65/month): ENCODE cCREs added,
OR production reliability required, OR multi-user access control needed.

---

## ETL extraction patterns

Two distinct patterns govern all data ingestion. Never mix them — topology comes
from files, enrichment comes from APIs.

### Pattern 1 — Bulk download + local parse (topology)
All data that defines *which nodes and edges to create* arrives as a flat file
downloaded once by `etl/00_download.sh` via `curl` into `data/raw/`. ETL scripts
read these local files using pandas. Idempotent — files already present are
skipped. Re-run `00_download.sh` only when a source publishes a new version.

| Source | File type | Approx size | Added to `00_download.sh` |
|--------|-----------|-------------|--------------------------|
| HGNC | TSV | ~10 MB | ✅ existing |
| GENCODE v46 GTF | GTF.gz | ~1 GB | ✅ existing |
| GENCODE v46 SwissProt metadata | TSV.gz | ~1 MB | ✅ existing |
| GTEx v10 median TPM | GCT.gz | ~80 MB | ✅ existing |
| DoRothEA | .rda | ~1 MB | ✅ existing |
| **STRING v12 human** | TSV.gz | ~400 MB | new |
| **GWAS Catalog full** | TSV | ~200 MB | new |
| **ClinVar variant summary** | TSV.gz | ~500 MB | new |
| **gnomAD constraint** | TSV | ~50 MB | new |
| **EFO disease ontology** | OBO/JSON | ~10 MB | new |

### Pattern 2 — REST API per entity (enrichment only)
Used only when there is no bulk file worth downloading, or when you want to
enrich *only the nodes already in the graph* (avoiding downloading gigabytes
to extract tens of thousands of records). Always runs **after** the topology
ETL scripts so the nodes exist before enrichment.

| Source | API | Notes |
|--------|-----|-------|
| NCBI Gene summaries | E-utilities `esummary.fcgi?db=gene` | Same endpoint as citation agent; batched 500 genes/request |
| UniProt function text | `rest.uniprot.org/uniprotkb/{accession}.json` | ~20k TF Proteins initially; full proteome in later iterations |
| OpenRouter embeddings | `text-embedding-3-small` | Called by embedding agent on nodes with `summary_text IS NOT NULL AND embedding IS NULL` |

**Rule of thumb: topology = bulk download; enrichment = API.** An API is never
called to discover what nodes and edges to create.

---

## Pipeline architecture

### ETL pipeline runner ✅
`etl/run_pipeline.py` implemented — Python DAG runner with DataSource logging.

**Load order (Phase 2, implemented):**
```
01_hgnc → 02_gencode → 03_gtex → 05_proteins → 04_dorothea   ← Phase 1
→ 06_uniprot_enrich → 07_string → 08_gwas → 09_clinvar
→ 10_ncbi_summaries → 11_gnomad                               ← Phase 2
```

### Background agents
Three scheduled agents, all following the citation agent pattern (batch,
process N items per run where a trigger condition is met):

| Agent | Trigger condition | Batch size | Schedule | Status |
|-------|------------------|------------|----------|--------|
| Citation agent | `pmids = [] AND citation_attempted = false` | 100 edges | Nightly | ✅ Phase 1 |
| Embedding agent | `summary_text IS NOT NULL AND embedding IS NULL` | 50 nodes | Nightly / post-ETL | ✅ Phase 2 |
| Extraction agent | New papers in bioRxiv/PubMed | TBD | Weekly | Phase 3 — deferred |

Orchestrator (Prefect/Dagster) deferred until all three agents need
independent scheduling — at that point the overhead pays for itself.

---

## Tunable scaling parameters

All parameters below are env vars — change without touching code. Initial
values are conservative; expand as the graph matures and hardware scales.

| Parameter | Default | What it controls | Expand to |
|-----------|---------|-----------------|-----------|
| `DOROTHEA_MIN_CONFIDENCE` | `A,B` | DoRothEA TF→Gene tier filter | Add C for ~13k edges |
| `STRING_MIN_CONFIDENCE` | `0.9` | STRING PPI combined score threshold (~50k edges) | `0.7` for ~130k, `0.4` for ~700k |
| `STRING_MAX_EXPAND_PER_NODE` | `10` | Max `INTERACTS_WITH` neighbours expanded per node per traversal frontier step | Raise if hub proteins underrepresented |
| `GWAS_MIN_SIGNIFICANCE` | `5e-8` | GWAS Catalog p-value cutoff (genome-wide significance) | Lower cautiously — increases noise |
| `min_signal` (ε) | `0.05` | Signal-decay floor; traversal stops below this | Lower to reach more distant nodes |
| `decay` (d) | `0.7` | Per-hop global decay multiplier | Raise for tighter neighbourhoods |
| `max_nodes` | `150` | Hard cap on subgraph size returned per query | Raise for dense disease traversals |

### Conductance formula per edge type (signal-decay traversal)

| Edge | Conductance `c(edge)` | Notes |
|------|-----------------------|-------|
| `REGULATES` | DoRothEA `confidence` (0–1) | Biological regulatory strength |
| `PRODUCES` | Structural constant ~0.9 | Always structural; tissue is visual only (ADR-0006) |
| `TRANSLATES_TO` / `ENCODES` | ~1.0 | Near-certain structural link |
| `INTERACTS_WITH` | STRING `combined_score` (0–1) | Physical PPI strength |
| `ASSOCIATED_WITH` | `-log10(p_value)` normalised 0–1 | p=5×10⁻⁸ → ~0.4; p=10⁻³⁰ → ~1.0 |
| `IN_GENE` | ~1.0 | Structural mapping, no uncertainty |

---

## Frontend: Entity Browser + Multi-select (Phase 2 addition)

### Entity browser

**Layout:** Collapsible left panel — slides over the 3D viewer (viewer does not
resize, preserving graph layout stability). Collapsed = 24px handle at left edge.
Standard pattern from IGV/JBrowse.

**Data strategy:** Server-side search with virtualized list (Option A). Debounced
API calls to an expanded `/api/search` endpoint that accepts filter params and
returns paginated results. Only visible rows render in DOM. Scales to 500k+ nodes.

New backend endpoint: `GET /api/search` (expanded from current MVP implementation):
```
params:
  q          string   text search (hgnc_symbol, name, description)
  type       string   "gene" | "protein" | "variant" | "disease" | "transcript" | "all"
  chromosome string   filter genes/variants by chromosome
  biotype    string   filter genes/transcripts by biotype
  clinical   string   filter variants by clinical_significance
  pli_min    float    filter genes by minimum pLI score
  limit      int      default 50
  offset     int      pagination offset
```
Returns: `{results: [...], total: int, has_more: bool}`

**Multi-select:** Checkbox per row. Selection state persists while the panel is
open. "Load selected (N)" button at panel bottom triggers `POST /api/graph/multi`.
"Clear graph" button resets viewer to empty (not default TP53 — user controls state).

### Multi-seed graph loading
`POST /api/graph/multi` — takes a list of seed node IDs (any type: Gene, Protein,
Variant, Disease), runs signal-decay traversal from each in parallel
(`asyncio.gather`), merges results by machine ID (deduplicating on `ensembl_id`,
`uniprot_id`, `rsid`, `ontology_id`), returns one `GraphResponse`.

Merge behaviour with existing viewer: **additive** — selected entities are added
to the current graph, not replacing it. A "Clear" button resets to empty (not
default TP53). User controls accumulation explicitly.

### Disconnected island handling (3 layers)

**Layer 1 — Notification:** If the merged graph has >1 connected component, surface
a banner: *"N of M selected entities form separate clusters — they may not be
directly connected at this signal threshold."*

**Layer 2 — Seed tinting:** Each seed entity and its exclusive subgraph nodes
receive a faint per-seed colour ring/hull. Shared nodes (bridges) get no tint.
Makes "TP53's cluster" vs "BRCA2's cluster" legible at a glance.

**Layer 3 — Shortest-path finder:**
`GET /api/graph/path?from={id_a}&to={id_b}&max_hops=6`

Neo4j `shortestPath((a)-[*..6]-(b))`. Returns:

```json
{
  "path_found": bool,
  "hop_count": int | null,
  "path_quality": "direct" | "moderate" | "weak" | "no_path",
  "nodes": [...],
  "edges": [...],
  "warning": string | null
}
```

Quality tiers and UI treatment:
| Hops | Quality | UI |
|------|---------|-----|
| 1–2 | `direct` | Show path normally |
| 3–4 | `moderate` | Show with subtle warning |
| 5–6 | `weak` | Show with clear warning: "Long chain — may not be biologically direct" |
| No path within 6 hops | `no_path` | Show: "No path found within 6 hops. These entities may not be connected at current data resolution." Never silently return empty results. |

Hard cap: never search beyond 6 hops — paths longer than 6 are biologically meaningless.
The max_hops cap is enforced in Cypher (`shortestPath([*..6])`), not just in response filtering.

---

## Frontend: visual + camera (Phase 2 additions)

### Node & edge colour palette (Phase 2 revised)

Colors remain the primary differentiator — shapes are not used (too hard to
distinguish at force-graph node sizes).

**Nodes:**
| Node | Colour | Hex |
|------|--------|-----|
| Gene | green | `#4ade80` |
| Transcript | blue | `#60a5fa` |
| Protein (all subtypes) | violet | `#c084fc` |
| Protein — TF subtype accent | amber (larger node) | `#f59e0b` |
| Variant | teal | `#2dd4bf` |
| Disease | hot pink | `#f472b6` |

Layer-hue logic: green = genomics, blue = transcriptomics, violet/amber =
proteomics, pink = phenotype. Teal for variant stays in the genomics plane
without competing with gene green.

**Edges:**
| Edge | Colour | Hex |
|------|--------|-----|
| REGULATES activator | green | `#22c55e` |
| REGULATES repressor | red | `#ef4444` |
| PRODUCES | indigo | `#818cf8` |
| TRANSLATES_TO / ENCODES | violet | `#c084fc` |
| INTERACTS_WITH | slate | `#64748b` |
| ASSOCIATED_WITH | hot pink | `#f472b6` |
| IN_GENE | teal | `#2dd4bf` |
| IMPLICATED_IN | orange | `#fb923c` |

### Scene background
Minimal dark — `#050508` background colour, no stars, no gradients. Graph nodes
are the only visual interest. Clean, no distractions.

### Camera modes
Toggle between two modes via `F` key or toolbar button:
- **Orbit** (default): current Three.js `OrbitControls` — orbit around graph centre, scroll to zoom
- **Fly**: `FlyControls` — `W/S` forward/back, `A/D` strafe, `Q/E` up/down, mouse = look direction, `Esc`/`F` = back to Orbit
Small HUD indicator shows active mode. No pointer lock required.

### Clear button
Resets viewer to **empty** (not default TP53). User controls accumulation explicitly
via entity browser multi-select. "Load selected (N)" adds to empty canvas.

---

## Phase 3 backlog — deferred decisions

Items below were explicitly deferred from Phase 2. They are pre-grilled (no new
design session required) but not yet implemented.

### Data sources (high priority)

| Item | Why deferred | Pre-decided approach |
|------|-------------|---------------------|
| **TCGA cancer differential expression** | Needed full proteome + Disease nodes first (now done) | TCGA-PANCAN cohort; `CO_EXPRESSED_WITH` and tumor-vs-normal `EXPRESSED_IN` edges; `cancer_gene` flag (COSMIC/OncoKB) on Gene already modelled |
| **GTEx tissue panel expansion** | Low urgency vs disease data | Add kidney, heart, lung, pancreas, adipose to `PRODUCES` tissue weight props; same pattern as existing 3 tissues |
| **ENCODE regulatory elements (cCREs)** | Infrastructure decision — 1.7M nodes requires AuraDB | Only after migration to AuraDB Professional; adds `(:cCRE)` node type in genomics plane |

### Agents (Phase 3)

| Agent | Trigger | Notes |
|-------|---------|-------|
| **Literature extraction agent** | New bioRxiv/PubMed papers | Proposes new edges from text; validation queue before write; see `Extraction agent (v3+)` in AGENTS.md |

### Infrastructure triggers

| Trigger | Action |
|---------|--------|
| ENCODE cCREs added | Migrate to AuraDB Professional (~$65/month) |
| >500k embedded nodes OR >500ms ANN latency | Revisit Neo4j vector index vs external store (ADR-0008) |
| ≥3 agents need independent schedules | Add Prefect/Dagster orchestrator |

### Biological questions not yet answerable

- Tumor vs normal expression differences (requires TCGA)
- Cell-type resolution (requires CellxGene / single-cell integration)
- Metabolite layer (KEGG/Recon3D) — layer 5 in the future stack
- Co-expression networks (`CO_EXPRESSED_WITH` — GTEx/TCGA)
- Regulatory element → Gene links (`BINDS` — ENCODE ChIP-seq)
