# OmniGraph — Data Architecture & Catalog

The authoritative reference for the graph's data model: the layered omics
structure, the ETL ingestion patterns, and complete field-level provenance for
every node type, edge type, source, index, and agent write. Use this for data
traceback, manual review, and understanding what each field means and where it
came from.

Architectural rationale for individual decisions lives in
[`docs/adr/`](adr/). Product scope and design decisions live in
[`vision-and-mvp.md`](vision-and-mvp.md). Current build state and future work live
in [`roadmap.md`](roadmap.md).

---

## 1. The layered model

OmniGraph is a multi-omics knowledge graph of human biology. Nodes are biological
entities; edges are directed, typed, evidence-scored relationships. The graph is
visualised as stacked planes (a "graphite" structure):

```
[ Phenotype       ]  ← Disease            layer_z = 1200   (frontend y = 900)
[ Metabolomics    ]  ← Metabolite         layer_z = 900    (frontend y = 600)
[ Proteomics      ]  ← Protein            layer_z = 600    (frontend y = 300)
[ Transcriptomics ]  ← Transcript         layer_z = 300    (frontend y = 0)
[ Genomics        ]  ← Gene, Variant      layer_z = 0      (frontend y = -300)
```

- **Vertical (inter-layer) edges** are the molecular backbone: `PRODUCES`
  (Gene→Transcript), `TRANSLATES_TO` (Transcript→Protein), `ENCODES`
  (Gene→Protein, fallback), `CATALYSES` (Protein→Metabolite). A transcription
  factor is a **Protein** that acts *downward* on a gene via `REGULATES`
  (Protein→Gene) — not a gene→gene edge ([ADR-0004](adr/0004-transcription-factors-as-proteins.md)).
- **Horizontal (intra-layer) edges** connect entities within a plane:
  `INTERACTS_WITH` (Protein↔Protein, STRING PPI).
- **Cross-cutting edges** link to phenotype: `ASSOCIATED_WITH` (Variant→Disease),
  `IMPLICATED_IN` (Gene→Disease), `DIFFERENTIALLY_EXPRESSED` (Gene→Disease).

Layer Z metadata is owned by backend constants (`DISEASE_LAYER_Z=1200`,
`METABOLITE_LAYER_Z=900`); the shift that inserted metabolomics as the 4th plane
is [ADR-0009](adr/0009-metabolomics-layer-4.md). Tissue is **not** a layer or a
traversal input — it is a frontend opacity channel
([ADR-0006](adr/0006-tissue-as-visual-channel.md)).

---

## 2. ETL extraction patterns

Two patterns govern all ingestion. **Never mix them — topology comes from files,
enrichment comes from APIs.**

### Pattern 1 — Bulk download + local parse (topology)
Everything that defines *which nodes and edges to create* arrives as a flat file,
downloaded once by `etl/00_download.sh` into `data/raw/`, then parsed locally
(pandas / scipy). Idempotent: files already present are skipped. Re-download only
when a source publishes a new version.

### Pattern 2 — REST API per entity (enrichment only)
Used only to enrich *nodes already in the graph*, or when there is no bulk file
worth downloading. Always runs **after** the topology scripts so the nodes exist
first. Examples: NCBI Gene summaries, UniProt function text, OpenRouter
embeddings.

**Rule of thumb: topology = bulk download; enrichment = API.** An API is never
called to discover what nodes and edges to create.

Every ETL script prints all source column names at startup and aborts with a clear
error if expected columns are missing — a source schema change fails loudly rather
than loading silently-wrong data. All scripts are `MERGE`-based (idempotent) and
log a `DataSource` node with version + record count.

---

## 3. Data Sources

| # | Source | Format | Approx size | License / Access | ETL script |
|---|--------|--------|-------------|-----------------|------------|
| 1 | HGNC complete set | TSV (gzip) | ~10 MB | CC0 public | `etl/01_hgnc.py` |
| 2 | GENCODE v46 (human) | GTF (gzip) | ~1.5 GB | CC0 public | `etl/02_gencode.py` |
| 3 | GTEx v10 expression | RDS (gene medians) | ~50 MB | dbGaP open | `etl/03_gtex.py` |
| 4 | DoRothEA hs (confidence A+B) | RDA | ~5 MB | Apache 2.0 | `etl/04_dorothea.py` |
| 5 | UniProt Swiss-Prot (human) | REST API | API calls | CC BY 4.0 | `etl/05_proteins.py`, `06_uniprot_enrich.py` |
| 6 | STRING v12 (human) | TSV (gzip) | ~1 GB | CC BY 4.0 | `etl/07_string.py` |
| 7 | GWAS Catalog | ZIP (TSV) | ~200 MB | CC0 public | `etl/08_gwas.py` |
| 8 | ClinVar variant summary | TSV (gzip) | ~300 MB | Public domain | `etl/09_clinvar.py` |
| 9 | NCBI Gene summaries | E-utilities REST | API calls | Public domain | `etl/10_ncbi_summaries.py` |
| 10 | gnomAD v4 constraint | TSV | ~20 MB | CC0 public | `etl/11_gnomad.py` |
| 11 | COSMIC Cancer Gene Census v104 | CSV/TSV | ~1 MB | CC BY-NC-SA (public tier) | `etl/12_cosmic.py` |
| 12 | TCGA Pan-Cancer (UCSC Xena) | TSV.gz × 2 | ~2 GB | Public (no auth) | `etl/13_tcga.py` |
| 13 | Recon3D v3.04 (`.mat` COBRA model) | MATLAB | ~60 MB | CC BY 4.0 | `etl/14_metabolomics.py` |
| 14 | HMDB metabolite IDs | ZIP (XML) | ~6.4 GB unzipped | Free for academic | `etl/14_metabolomics.py` |
| 15 | ENCODE cCRE Registry V4 | BED.gz + TSV | ~30 MB BED | CC0 | `etl/15_encode.py` *(gated — see roadmap)* |

---

## 4. ETL Pipeline DAG

Run order is enforced by `etl/run_pipeline.py`. Each script must complete without
error before the next starts.

```
01_hgnc          → Gene nodes (scaffold for all downstream)
02_gencode       → Transcript nodes + PRODUCES edges
03_gtex          → enriches PRODUCES with tw_* expression weights
04_dorothea      → REGULATES edges (TF Protein → Gene; migrated from Gene→Gene)
05_proteins      → FULL proteome (~20k) Protein nodes + TRANSLATES_TO/ENCODES
                   + TF-subtype tag + REGULATES migration (ADR-0010)
06_uniprot_enrich→ enriches Protein nodes (subtype, summary_text, go_terms, loc)
07_string        → INTERACTS_WITH edges (Protein–Protein)
08_gwas          → Variant + Disease nodes + ASSOCIATED_WITH + IN_GENE + IMPLICATED_IN
09_clinvar       → enriches Variant nodes (clinical_significance)
10_ncbi_summaries→ enriches Gene nodes (summary_text)
11_gnomad        → enriches Gene nodes (pli_score)
12_cosmic        → enriches Gene nodes (cancer_gene, cosmic_tier)
13_tcga          → DIFFERENTIALLY_EXPRESSED edges (Gene → Disease)
14_metabolomics  → Metabolite nodes + CATALYSES edges (Protein → Metabolite)
15_encode        → cCRE nodes + BINDS + REGULATES_VIA   ← GATED: AuraDB only

Background agents (run independently of the ETL pipeline):
  EmbeddingAgent   → reads *.summary_text/description, writes *.embedding
  CitationAgent    → reads edges with pmids=[], writes pmids
```

**Dependency rules:**
- `02_gencode` after `01_hgnc` (PRODUCES needs Gene nodes).
- `03_gtex` after `02_gencode` (enriches PRODUCES).
- `04_dorothea` before `05_proteins` (proteins.py migrates REGULATES Gene→Gene to Protein→Gene).
- `05_proteins` after `01_hgnc` + `02_gencode` (needs HGNC uniprot_ids, GENCODE SwissProt metadata).
- `07_string` after `05_proteins` (needs UniProt IDs to map STRING ENSP→UniProt).
- `08_gwas` depends only on `01_hgnc` for Gene existence.
- `09_clinvar` after `08_gwas` (enrichment — Variant nodes must exist).
- `13_tcga` after `08_gwas` (needs Disease nodes) and `03_gtex`.
- `14_metabolomics` after `05_proteins` (needs the full proteome for the CATALYSES source — ADR-0010).
- `15_encode` runs on AuraDB only (gated by node count > 500k).

---

## 5. Node Field Provenance

### Gene
| Field | Source | Column / API field | Transformation |
|-------|--------|-------------------|----------------|
| `ensembl_id` | `hgnc_complete_set.txt` | `ensembl_gene_id` | Strip version suffix (`.N`) |
| `hgnc_symbol` | `hgnc_complete_set.txt` | `symbol` | None |
| `hgnc_id` | `hgnc_complete_set.txt` | `hgnc_id` | None (e.g. `HGNC:11998`) |
| `description` | `hgnc_complete_set.txt` | `name` | None |
| `chromosome` | `hgnc_complete_set.txt` | `chromosome`; fallback parse `location` | Regex `^(\d{1,2}\|X\|Y\|MT)` |
| `summary_text` | NCBI E-utilities `esummary` | `result[uid].summary` | Batch 500/req; 3 req/s (10/s with key) |
| `pli_score` | `gnomad_v4_constraint.tsv` | `lof.pLI` | Prefer MANE Select; matched by `hgnc_symbol` |
| `cancer_gene` | COSMIC CGC CSV | `Gene Symbol` (presence) | `True` if in Census; never `False` (null = not checked) |
| `cosmic_tier` | COSMIC CGC CSV | `Tier` | `"1"` or `"2"` |
| `embedding` | EmbeddingAgent | `summary_text` | `text-embedding-3-small`, 1536-dim |
| `source_db` | `01_hgnc.py` | — | `"HGNC"` |

**Key:** `ensembl_id` is the traversal key used by all downstream ETL.

### Transcript
| Field | Source | Column / API field | Transformation |
|-------|--------|-------------------|----------------|
| `ensembl_tx_id` | GENCODE v46 GTF | `transcript_id` | Strip version suffix |
| `hgnc_symbol` | GENCODE v46 GTF | `transcript_name` | None (e.g. `TP53-201`) |
| `biotype` | GENCODE v46 GTF | `transcript_type` | None |
| `length_bp` | GENCODE v46 GTF | `end - start + 1` | Integer arithmetic |
| `source_db` | `02_gencode.py` | — | `"GENCODE_v46"` |

**Key:** `ensembl_tx_id` (ENST, version stripped).

### Protein
| Field | Source | Column / API field | Transformation |
|-------|--------|-------------------|----------------|
| `uniprot_id` | HGNC `uniprot_ids` | IdMapper via `05_proteins.py` | First Swiss-Prot accession |
| `hgnc_symbol` | source Gene node | — | Copied at creation |
| `subtype` | `05_proteins.py` / `06_uniprot_enrich.py` | UniProt GO cross-refs | See GO subtype map below |
| `summary_text` | UniProt REST | `comments[FUNCTION].texts[0].value` | First functional description; 1 req/s |
| `subcellular_loc` | UniProt REST | `comments[SUBCELLULAR LOCATION]…location.value` | First location |
| `go_terms` | UniProt REST | `crossReferences[GO].id` | List of GO IDs |
| `molecular_weight` | UniProt REST | `sequence.molWeight` | Integer, Daltons |
| `embedding` | EmbeddingAgent | `summary_text` | 1536-dim |
| `source_db` | `05_proteins.py` | — | `"UniProt_Swiss-Prot"` |

**Subtype GO map** (in `06_uniprot_enrich.py`): `GO:0003700`→transcription_factor,
`GO:0016301`→kinase, `GO:0005198`→structural, `GO:0003824`→enzyme.
`transcription_factor` is also set by `05_proteins.py` for any protein targeted by
a DoRothEA REGULATES edge. **Key:** `uniprot_id` (e.g. `P04637`).

### Variant
| Field | Source | Column / API field | Transformation |
|-------|--------|-------------------|----------------|
| `rsid` | GWAS Catalog TSV | `SNPS` | First `rs\d+` token; fallback `chr{CHR_ID}:{CHR_POS}:NA:NA` |
| `chromosome` | GWAS Catalog TSV | `CHR_ID` | String |
| `position_grch38` | GWAS Catalog TSV | `CHR_POS` | Integer |
| `clinical_significance` | ClinVar summary TSV | `ClinicalSignificance` | Enrichment; matched by `rsid` |
| `source_db` | `08_gwas.py` | — | `"GWAS_Catalog"` |

**Key:** `rsid` (fallback `chr:pos:NA:NA`). Variant-level gnomAD allele frequency
is a known gap (not populated). Variants are not in the fulltext index.

### Disease
| Field | Source | Column / API field | Transformation |
|-------|--------|-------------------|----------------|
| `ontology_id` | GWAS Catalog TSV | `MAPPED_TRAIT_URI` | Extract EFO ID from URI |
| `name` | GWAS Catalog TSV | `MAPPED_TRAIT` | First `;`-separated value |
| `description` | EFO ontology (`efo.json`) | `description` | Input for EmbeddingAgent |
| `embedding` | EmbeddingAgent | `description` | 1536-dim |
| `source_db` | `08_gwas.py` | — | `"EFO_via_GWAS_Catalog"` |

**Key:** `ontology_id` (e.g. `EFO_0001360`).

### Metabolite
| Field | Source | Column / API field | Transformation |
|-------|--------|-------------------|----------------|
| `hmdb_id` | Recon3D `.mat` `metHMDBID` | — | Normalised to `HMDB0000000` form; primary key |
| `chebi_id` | Recon3D `.mat` `metCHEBIID` | — | Fallback when `hmdb_id` absent |
| `name` | Recon3D `.mat` `metNames` (HMDB canonical override) | — | String |
| `formula` | Recon3D `.mat` `metFormulas` | — | e.g. `C6H12O6` |
| `charge` | Recon3D `.mat` `metCharges` | — | Integer |
| `layer_z` | `14_metabolomics.py` | — | `900` (ADR-0009) |
| `source_db` / `source_version` | `14_metabolomics.py` | — | `"Recon3D"` / `"3.01_mat"` |

**Key:** `hmdb_id` (e.g. `HMDB0000122`); fallback `chebi_id` (e.g. `CHEBI:4167`).

### cCRE *(gated — see roadmap)*
`encode_id` (key), `chromosome`, `start_grch38`, `end_grch38`, `ccre_type`
(`PLS`/`pELS`/`dELS`/`CTCF-only`/`DNase-H3K4me3`), `layer_z=0`, `source_db="ENCODE"`,
`source_version="V4"`. Sourced from the ENCODE cCRE BED. Not loaded on Community
Edition (1.7M nodes → OOM).

---

## 6. Edge Field Provenance

### PRODUCES (Gene → Transcript)
`tw_whole_blood` / `tw_liver` / `tw_brain_prefrontal_cortex` from GTEx v10 RDS
(columns `Whole_Blood`, `Liver`, `Brain_Frontal_Cortex_BA9`), normalised 0–1 by
99th-percentile per tissue; gene-level weight propagated to all transcripts.
`source_db="GTEx_v10"`.

### TRANSLATES_TO (Transcript → Protein)
`source_db="GENCODE_SwissProt"`. Created from the 3-column GENCODE SwissProt
metadata TSV (versioned ENST → UniProt accession); ENST version suffix stripped
before matching.

### ENCODES (Gene → Protein)
`source_db="HGNC"`. Fallback when the canonical transcript is absent from the
GENCODE SwissProt file; `TRANSLATES_TO` is preferred where available.

### REGULATES (Protein → Gene)
| Field | Source | Notes |
|-------|--------|-------|
| `confidence` | DoRothEA `dorothea_hs.rda` `confidence` | `"A"`–`"E"`; only A+B loaded by default |
| `mode` | DoRothEA `mor` | `"activator"` / `"repressor"` |
| `weight` | derived | A=1.0, B=0.85, C=0.7, D=0.5, E=0.25 |
| `source_db` | `04_dorothea.py` | `"DoRothEA"` |

Default filter `DOROTHEA_MIN_CONFIDENCE=A,B`.

### INTERACTS_WITH (Protein ↔ Protein)
`combined_score` / `experimental_score` / `coexpression_score` from STRING v12
(divided by 1000 → 0–1). `source_db="STRING_v12"`. ENSP→UniProt mapped via IdMapper
(~11% of high-confidence pairs unmapped and skipped — a GTF-chain coverage gap).
Threshold `STRING_MIN_CONFIDENCE × 1000` (default `0.95 × 1000 = 950`, raised from
0.9 once the full ~20k proteome was minted — [ADR-0010](adr/0010-full-proteome.md)).
**At 0.95 over the full proteome: ~101k edges** (vs ~640 with the TF-only slice).
Traversal cap: `STRING_MAX_EXPAND_PER_NODE` (default 10), top-k by `combined_score`.

### ASSOCIATED_WITH (Variant → Disease)
`p_value` (filters at `GWAS_MIN_SIGNIFICANCE=5e-8`), `pmids`, `beta`,
`source_db="GWAS_Catalog"`. Conductance = `-log10(p_value)` normalised 0–1.

### IN_GENE (Variant → Gene)
`consequence_type` (e.g. `missense_variant`), `source_db="GWAS_Catalog"`.

### IMPLICATED_IN (Gene → Disease)
Roll-up created when a Variant is both `IN_GENE` for a gene and `ASSOCIATED_WITH` a
disease. `source_db="GWAS_Catalog"`.

### DIFFERENTIALLY_EXPRESSED (Gene → Disease)
| Field | Source | Notes |
|-------|--------|-------|
| `log2fc` | `13_tcga.py` | `median_tumour − median_normal` per cohort (Toil matrix is already `log2(fpkm+0.001)`) |
| `direction` | `13_tcga.py` | `"up"` if log2fc>0 else `"down"` |
| `tumor_type` | `13_tcga.py` | TCGA cancer code (e.g. `LUAD`, `BRCA`) |
| `n_tumor` / `n_normal` | `13_tcga.py` | sample counts per cohort |
| `source_db` / `source_version` | `13_tcga.py` | `"TCGA_XENA"` / `"toil_rsem_fpkm"` |

Conductance = `min(1.0, abs(log2fc)/4.0)`. Threshold `abs(log2fc) >= TCGA_MIN_LOG2FC`
(default 1.0). Tumour = sample_type_id 01–09, normal = 10–19; cohorts with fewer
than `TCGA_MIN_NORMALS` adjacent normals are skipped (no fabricated baseline).
Cohort→disease via the curated, graph-verified crosswalk
`etl/reference/tcga_disease_to_efo.tsv` (28/33 cohorts resolve to a present
Disease). **Caveat:** median cohort DE is simplified (subtype changes wash out;
publication-grade work needs DESeq2/edgeR on counts) — sufficient for directional
signal traversal.

### CATALYSES (Protein → Metabolite)
| Field | Source | Notes |
|-------|--------|-------|
| `role` | Recon3D `.mat` `S` matrix sign | `"substrate"` (S<0) / `"product"` (S>0) |
| `reaction_id` | Recon3D `.mat` `rxns` | e.g. `10FTHF5GLUtl` |
| `source_db` / `source_version` | `14_metabolomics.py` | `"Recon3D"` / `"3.01_mat"` |

Conductance = 0.7 (fixed; enzymatic). Recon3D genes are **Entrez ids**, crosswalked
to Ensembl via HGNC, then matched `(g:Gene)-[:ENCODES|PRODUCES|TRANSLATES_TO*1..2]->(p:Protein)`.
Gated on the proteome: with the TF-only slice only 8 edges formed; with the full
proteome (ADR-0010) **CATALYSES = 24,545 edges, 94% of metabolites connected**.
`CATALYSES` is **not** dense-capped; metabolites are treated as terminal leaves in
traversal ([ADR-0011](adr/0011-backbone-guaranteed-traversal.md)).

### BINDS (Protein → cCRE) / REGULATES_VIA (cCRE → Gene) *(gated)*
ENCODE-sourced; see roadmap. `BINDS` conductance = `chip_score` (TF proteins only);
`REGULATES_VIA` ≈ 1.0 structural proximity (≤500 kb, max 3 nearest genes per cCRE).

---

## 7. Signal-decay traversal & conductance

Neighbourhood queries are bounded by a **decaying signal** (confidence-gated
spreading activation), not a fixed hop count
([ADR-0005](adr/0005-signal-decay-traversal.md)). Signal starts at 1.0 at the seed;
crossing an edge: `signal_next = signal_cur × decay × conductance(edge)`. Expansion
is ring-batched (one Cypher per hop), trimmed to `max_nodes` by signal. The seed's
own vertical backbone — and the metabolites its protein catalyses — is guaranteed
present via a backbone pre-pass ([ADR-0011](adr/0011-backbone-guaranteed-traversal.md)).

| Edge | Conductance `c(edge)` | Notes |
|------|-----------------------|-------|
| `REGULATES` | DoRothEA `confidence` (0–1) | dense-capped at `REGULATES_MAX_EXPAND_PER_NODE` |
| `PRODUCES` | structural ~0.9 | tissue is visual only (ADR-0006) |
| `TRANSLATES_TO` / `ENCODES` | ~1.0 | near-certain structural |
| `INTERACTS_WITH` | STRING `combined_score` (0–1) | dense-capped at `STRING_MAX_EXPAND_PER_NODE` |
| `ASSOCIATED_WITH` | `-log10(p_value)` normalised 0–1 | p=5×10⁻⁸→~0.4; p=10⁻³⁰→~1.0; dense-capped |
| `IN_GENE` / `IMPLICATED_IN` | ~1.0 / 0.5 | structural / rollup; dense-capped |
| `DIFFERENTIALLY_EXPRESSED` | `min(1.0, abs(log2fc)/4.0)` | dense-capped |
| `CATALYSES` | 0.7 (fixed) | not capped; metabolites are leaves (ADR-0011) |

---

## 8. Neo4j Index Catalog

Created at backend startup via `create_indexes()` in
`backend/db/neo4j_client.py`.

| Index | Type | Labels / properties | Purpose |
|-------|------|---------------------|---------|
| `gene_ensembl_idx` | B-tree | `Gene.ensembl_id` | Primary traversal key |
| `gene_symbol_idx` | B-tree | `Gene.hgnc_symbol` | Symbol lookup |
| `transcript_idx` | B-tree | `Transcript.ensembl_tx_id` | Transcript lookup |
| `protein_uniprot_idx` | B-tree | `Protein.uniprot_id` | Protein key |
| `variant_rsid_idx` | B-tree | `Variant.rsid` | Variant lookup |
| `disease_efo_idx` | B-tree | `Disease.ontology_id` | Disease key |
| `metabolite_hmdb_idx` | B-tree | `Metabolite.hmdb_id` | Metabolite key |
| `metabolite_chebi_idx` | B-tree | `Metabolite.chebi_id` | ChEBI fallback |
| `ccre_encode_idx` | B-tree | `cCRE.encode_id` | cCRE key *(gated)* |
| `node_search` | Fulltext | `Gene\|Transcript\|Protein\|Disease\|Metabolite` on `[hgnc_symbol, description, summary_text, name, formula]` | Autocomplete, EntityBrowser search |
| `gene_embedding_idx` | Vector (cosine, 1536) | `Gene.embedding` | Semantic gene search |
| `protein_embedding_idx` | Vector (cosine, 1536) | `Protein.embedding` | Semantic protein search |
| `disease_embedding_idx` | Vector (cosine, 1536) | `Disease.embedding` | Semantic disease search |

Vector indexes use Neo4j native `db.index.vector` ([ADR-0008](adr/0008-neo4j-native-vector-indexing.md)).
The fulltext index is dropped and recreated when Metabolite is added (label lists
are immutable under `CREATE FULLTEXT ... IF NOT EXISTS`).

---

## 9. Tunable Parameters

All are environment variables with defaults in `.env.example`. **Never hardcode
any of these in ETL or traversal code.**

| Env var | Default | Controls |
|---------|---------|----------|
| `DOROTHEA_MIN_CONFIDENCE` | `A,B` | DoRothEA confidence tiers loaded |
| `STRING_MIN_CONFIDENCE` | `0.95` | STRING PPI threshold (0.95 ≈ 101k edges over full proteome; 0.9 roughly doubles it) |
| `STRING_MAX_EXPAND_PER_NODE` | `10` | Hub-protein traversal cap per frontier step |
| `REGULATES_MAX_EXPAND_PER_NODE` | `25` | Hub-TF traversal cap per frontier step (ADR-0010) |
| `BACKBONE_MAX_METABOLITES_PER_PROTEIN` | `25` | Cap on pinned backbone metabolites per protein (ADR-0011) |
| `GWAS_MIN_SIGNIFICANCE` | `5e-8` | GWAS p-value cutoff for ASSOCIATED_WITH |
| `TRAVERSAL_DECAY` (d) | `0.7` | Per-hop signal decay multiplier |
| `TRAVERSAL_MIN_SIGNAL` (ε) | `0.05` | Signal-decay floor |
| `TRAVERSAL_MAX_NODES` | `300` | Hard subgraph size cap (raised 150→300, ADR-0010) |
| `NCBI_API_KEY` | (empty) | NCBI E-utilities rate limit: 3→10 req/s with key |
| `TCGA_MIN_LOG2FC` | `1.0` | Min abs log2FC for DIFFERENTIALLY_EXPRESSED |
| `TCGA_MAX_ADJ_PVALUE` | `0.05` | Adjusted p-value cutoff (reserved) |
| `METABOLOMICS_MIN_REACTIONS` | `1` | Min reactions a Metabolite must appear in |
| `ENCODE_BATCH_SIZE` | `5000` | cCRE nodes per UNWIND batch *(gated)* |
| `ENCODE_FORCE_LOAD` | `false` | Override AuraDB gate — **never** on Community Edition |
| `EMBEDDING_AGENT_BATCH_SIZE` | `50` | nodes per embedding agent run |

---

## 10. Agent Write Catalog

Background agents read from the graph, call external APIs, and write back to
specific node properties. Provenance convention: **agent writes carry
`source_agent` / `agent_version` / `run_timestamp`; deterministic ETL uses
`source_db` / `source_version`.**

### EmbeddingAgent
- **Reads:** `Gene` / `Protein` / `Disease` where `summary_text IS NOT NULL AND embedding IS NULL`.
- **Writes:** `*.embedding` (1536-dim), `*.embedding_model`, `*.embedding_at`, `*.source_agent`, `*.agent_version`.
- **API:** OpenRouter `text-embedding-3-small`. Nightly batch or manual.
- **Skips:** `Transcript`, `Variant`, `Metabolite` (no meaningful free text).
- **Safety:** writes only embedding/provenance properties — never creates nodes/edges or modifies biological fields.

### CitationAgent
- **Reads:** edges with `pmids = []`. **Writes:** validated PMIDs onto the edge.
- **Never** creates topology — PMID enrichment only.

### Text2Cypher (query-time, not batch)
- Reads `apoc.meta.schema()` (cached per process). Generates Cypher from natural
  language; `validate_cypher()` blocks any write keyword (MERGE/CREATE/DELETE/SET);
  executes read-only.

---

## 11. Data Quality Notes & Known Issues

- **HGNC symbol matching** — downstream ETL (COSMIC, GWAS, STRING, gnomAD) matches
  on `hgnc_symbol`; a renamed gene silently fails to match and is skipped (count
  always logged). HGNC snapshot date stored on the `DataSource` node.
- **STRING ENSP→UniProt** — ~3–11% of interactions involve proteins outside the
  human Swiss-Prot set and are skipped (logged).
- **TCGA DE method** — matched tumour-vs-adjacent-normal log2FC (replaced an earlier
  dimensionally-invalid GTEx-whole-blood proxy); cohorts with no adjacent normals
  are dropped. Directional, not statistically calibrated.
- **Recon3D Boolean gene associations** — complex enzyme expressions
  (`ENSG1 and ENSG2 or ENSG3`) are split on OR, first valid Ensembl per OR-group;
  AND logic (isozyme complexes) ignored. Conservative — may undercount catalytic
  edges (count logged).
- **HMDB streaming** — `hmdb_metabolites.zip` (~6.4 GB XML) is streamed with
  `iterparse` (bounded memory); a minimal lookup TSV is written and the unzipped
  XML deleted.
- **Variant gnomAD allele frequency** — not populated (gnomAD ETL covers gene-level
  pLI only). Deferred.
- **ENCODE** — 1.7M cCRE nodes OOM on Community Edition; `15_encode.py` is
  hard-gated (refuses to start unless >500k nodes present or `ENCODE_FORCE_LOAD=true`).
  Never force-load on Community.
