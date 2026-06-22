# OmniGraph — Data Engineering Catalog

Complete field-level provenance for every node type, edge type, data source,
index, and agent write in the graph. This is the authoritative reference for
data traceback, manual review, and understanding what each field means and
where it came from.

---

## 1. Data Sources

| # | Source | Format | Approx size | License / Access | Update freq | ETL script |
|---|--------|--------|-------------|-----------------|-------------|------------|
| 1 | HGNC complete set | TSV (gzip) | ~10 MB | CC0 public | Monthly | `etl/01_hgnc.py` |
| 2 | GENCODE v46 (human) | GTF (gzip) | ~1.5 GB | CC0 public | Per release | `etl/02_gencode.py` |
| 3 | GTEx v10 expression | RDS (gene medians) | ~50 MB | dbGaP open | Per release | `etl/03_gtex.py` |
| 4 | DoRothEA hs (confidence A+B) | RDA | ~5 MB | Apache 2.0 | Per release | `etl/04_dorothea.py` |
| 5 | UniProt Swiss-Prot (human) | Various (REST API) | API calls | CC BY 4.0 | Weekly | `etl/05_proteins.py`, `06_uniprot_enrich.py` |
| 6 | STRING v12 (human) | TSV (gzip) | ~1 GB | CC BY 4.0 | Per release | `etl/07_string.py` |
| 7 | GWAS Catalog | ZIP (TSV) | ~200 MB | CC0 public | Weekly | `etl/08_gwas.py` |
| 8 | ClinVar variant summary | TSV (gzip) | ~300 MB | Public domain | Monthly | `etl/09_clinvar.py` |
| 9 | NCBI Gene summaries | E-utilities REST | API calls | Public domain | Continuous | `etl/10_ncbi_summaries.py` |
| 10 | gnomAD v4 constraint | TSV | ~20 MB | CC0 public | Per release | `etl/11_gnomad.py` |
| 11 | COSMIC Cancer Gene Census v99 | CSV | ~1 MB | CC BY-NC-SA (public tier) | Per release | `etl/12_cosmic.py` *(Phase 3)* |
| 12 | TCGA Pan-Cancer (UCSC Xena) | TSV.gz × 2 | ~2 GB | Public (no auth) | Static | `etl/13_tcga.py` *(Phase 3)* |
| 13 | Recon3D v3.04 (SBML) | XML | ~60 MB | CC BY 4.0 | Per release | `etl/14_metabolomics.py` *(Phase 3)* |
| 14 | HMDB metabolite IDs | ZIP (XML) | ~1.5 GB unzipped | Free for academic | Per release | `etl/14_metabolomics.py` *(Phase 3)* |
| 15 | ENCODE cCRE Registry V4 | BED.gz + TSV | ~30 MB BED | CC0 | Per registry | `etl/15_encode.py` *(Phase 3, gated)* |

---

## 2. ETL Pipeline DAG

Run order is enforced by `etl/run_pipeline.py`. Each script must complete
without error before the next starts. All scripts are idempotent (MERGE-based).

```
Phase 1 & 2 (implemented):
  01_hgnc          → Gene nodes (scaffold for all downstream)
  02_gencode       → Transcript nodes + PRODUCES edges
  03_gtex          → enriches PRODUCES with tw_* expression weights
  04_dorothea      → REGULATES edges (TF Protein → Gene, migrated from Gene→Gene)
  05_proteins      → Protein nodes + TRANSLATES_TO / ENCODES edges
  06_uniprot_enrich→ enriches Protein nodes (subtype, summary_text, go_terms, subcellular_loc)
  07_string        → INTERACTS_WITH edges (Protein–Protein)
  08_gwas          → Variant nodes + Disease nodes + ASSOCIATED_WITH + IN_GENE + IMPLICATED_IN
  09_clinvar       → enriches Variant nodes (clinical_significance)
  10_ncbi_summaries→ enriches Gene nodes (summary_text, used by EmbeddingAgent)
  11_gnomad        → enriches Gene nodes (pli_score)

Phase 3 (planned):
  12_cosmic        → enriches Gene nodes (cancer_gene=true, cosmic_tier)
  13_tcga          → DIFFERENTIALLY_EXPRESSED edges (Gene → Disease)
  14_metabolomics  → Metabolite nodes + CATALYSES edges (Protein → Metabolite)
  15_encode        → cCRE nodes + BINDS edges + REGULATES_VIA edges  ← GATED: AuraDB only

Background agents (run independently of ETL pipeline):
  EmbeddingAgent   → reads Gene.summary_text / Protein.summary_text / Disease.description
                     writes Gene.embedding / Protein.embedding / Disease.embedding
```

**Dependency rules:**
- `02_gencode` must run after `01_hgnc` (PRODUCES edge requires Gene nodes).
- `03_gtex` must run after `02_gencode` (enriches PRODUCES edges).
- `04_dorothea` must run before `05_proteins` (proteins.py migrates REGULATES from Gene→Gene to Protein→Gene).
- `05_proteins` must run after `01_hgnc` and `02_gencode` (needs HGNC uniprot_ids, GENCODE SwissProt metadata).
- `07_string` must run after `05_proteins` (needs UniProt IDs to match STRING ENSP → UniProt).
- `08_gwas` is independent of protein scripts; depends only on `01_hgnc` for Gene node existence.
- `09_clinvar` must run after `08_gwas` (enrichment only — Variant nodes must exist).
- `13_tcga` must run after `08_gwas` (needs Disease nodes with EFO IDs) and `03_gtex` (uses tw_whole_blood proxy).
- `14_metabolomics` must run after `05_proteins` (needs Protein nodes for CATALYSES source).
- `15_encode` must run on AuraDB (gated by node count > 500k or pagecache miss > 30%).

---

## 3. Node Field Provenance

### Gene

| Field | Source file | Column / API field | Transformation |
|-------|-------------|-------------------|----------------|
| `ensembl_id` | `hgnc_complete_set.txt` | `ensembl_gene_id` | Strip version suffix (`.N`) |
| `hgnc_symbol` | `hgnc_complete_set.txt` | `symbol` | None |
| `hgnc_id` | `hgnc_complete_set.txt` | `hgnc_id` | None (e.g. `HGNC:11998`) |
| `description` | `hgnc_complete_set.txt` | `name` | None (gene full name) |
| `chromosome` | `hgnc_complete_set.txt` | `chromosome` column first; fallback: parse `location` field | Regex `^(\d{1,2}\|X\|Y\|MT)` from cytogenetic band |
| `summary_text` | NCBI E-utilities `esummary` | `result[uid].summary` | Batch 500/req; 3 req/s (10/s with `NCBI_API_KEY`) |
| `pli_score` | `gnomad_v4_constraint.tsv` | `lof.pLI` | Prefer MANE Select transcript (`mane_select` col); matched by `hgnc_symbol` |
| `cancer_gene` | COSMIC CGC v99 CSV | `Gene Symbol` (presence) | `True` if symbol in Census; never `False` (null = not checked) |
| `cosmic_tier` | COSMIC CGC v99 CSV | `Tier` | `"1"` or `"2"` |
| `embedding` | EmbeddingAgent (OpenRouter) | `summary_text` | `text-embedding-3-small`, 1536-dim; only populated when `summary_text IS NOT NULL AND embedding IS NULL` |
| `source_db` | Each ETL script | n/a | `"HGNC"` set by `01_hgnc.py` |

**Key:** HGNC `hgnc_id` (integer string, primary). Ensembl ID is the traversal key used by downstream ETL.

---

### Transcript

| Field | Source file | Column / API field | Transformation |
|-------|-------------|-------------------|----------------|
| `ensembl_tx_id` | GENCODE v46 GTF | `transcript_id` attribute | Strip version suffix (`.N`) |
| `hgnc_symbol` | GENCODE v46 GTF | `transcript_name` attribute | None (e.g. `TP53-201`) |
| `biotype` | GENCODE v46 GTF | `transcript_type` attribute | None (e.g. `protein_coding`, `lncRNA`) |
| `length_bp` | GENCODE v46 GTF | `end - start + 1` | Integer arithmetic on GTF coordinates |
| `source_db` | `02_gencode.py` | n/a | `"GENCODE_v46"` |

**Key:** `ensembl_tx_id` (ENST, version stripped).

---

### Protein

| Field | Source file | Column / API field | Transformation |
|-------|-------------|-------------------|----------------|
| `uniprot_id` | HGNC `uniprot_ids` col | IdMapper via `05_proteins.py` | First Swiss-Prot accession from HGNC field |
| `hgnc_symbol` | Inherited from source Gene node | n/a | Copied at Protein creation time |
| `subtype` | `05_proteins.py` (initial) / `06_uniprot_enrich.py` (override) | UniProt GO term cross-references | See GO subtype map below |
| `summary_text` | UniProt REST API | `comments[commentType='FUNCTION'].texts[0].value` | Truncated to first functional description; 1 req/s rate limit |
| `subcellular_loc` | UniProt REST API | `comments[commentType='SUBCELLULAR LOCATION'].subcellularLocations[0].location.value` | First location string |
| `go_terms` | UniProt REST API | `uniProtKBCrossReferences[database='GO'].id` | List of GO IDs (e.g. `["GO:0003700", ...]`) |
| `molecular_weight` | UniProt REST API | `sequence.molWeight` | Integer, Daltons |
| `embedding` | EmbeddingAgent | `summary_text` | Same as Gene; 1536-dim |
| `source_db` | `05_proteins.py` | n/a | `"UniProt_Swiss-Prot"` |

**Subtype GO map** (in `06_uniprot_enrich.py`):
```
GO:0003700 → transcription_factor
GO:0016301 → kinase
GO:0005198 → structural
GO:0003824 → enzyme
```
`transcription_factor` is also set by `05_proteins.py` for any protein already
targeted by DoRothEA REGULATES edges (earlier pass).

**Key:** `uniprot_id` (Swiss-Prot accession, e.g. `P04637`).

---

### Variant

| Field | Source file | Column / API field | Transformation |
|-------|-------------|-------------------|----------------|
| `rsid` | GWAS Catalog TSV | `SNPS` | First `;`-separated token matching `rs\d+`; fallback: `chr{CHR_ID}:{CHR_POS}:NA:NA` |
| `chromosome` | GWAS Catalog TSV | `CHR_ID` | String (e.g. `"7"`, `"X"`) |
| `position_grch38` | GWAS Catalog TSV | `CHR_POS` | Integer |
| `clinical_significance` | ClinVar variant summary TSV | `ClinicalSignificance` | Enrichment only; matched by `rsid`; comma-separated values (e.g. `"Pathogenic,Likely pathogenic"`) |
| `source_db` | `08_gwas.py` | n/a | `"GWAS_Catalog"` |

**Key:** `rsid` (primary). Fallback key `chr:pos:NA:NA` used when GWAS record has no rsid.

---

### Disease

| Field | Source file | Column / API field | Transformation |
|-------|-------------|-------------------|----------------|
| `ontology_id` | GWAS Catalog TSV | `MAPPED_TRAIT_URI` | Extract EFO ID from URI; e.g. `http://www.ebi.ac.uk/efo/EFO_0001360` → `EFO_0001360` |
| `name` | GWAS Catalog TSV | `MAPPED_TRAIT` | First `;`-separated value |
| `description` | EFO ontology (`efo.json`) | `description` field | Used as input for EmbeddingAgent |
| `embedding` | EmbeddingAgent | `description` | 1536-dim; populated when `description IS NOT NULL AND embedding IS NULL` |
| `source_db` | `08_gwas.py` | n/a | `"EFO_via_GWAS_Catalog"` |

**Key:** `ontology_id` (EFO ID string, e.g. `EFO_0001360`).

---

### Metabolite *(Phase 3)*

| Field | Source file | Column / API field | Transformation |
|-------|-------------|-------------------|----------------|
| `hmdb_id` | Recon3D SBML `<annotation>` MIRIAM URNs | `urn:miriam:hmdb:HMDBXXXXXXX` | Extracted from MIRIAM annotation; primary key |
| `chebi_id` | Recon3D SBML `<annotation>` MIRIAM URNs | `urn:miriam:chebi:CHEBI:XXXX` | Fallback when `hmdb_id` absent |
| `name` | Recon3D SBML `<species name=...>` | `name` attribute | String |
| `formula` | Recon3D SBML `<annotation>` | Chemical formula annotation | String (e.g. `C6H12O6`) |
| `charge` | Recon3D SBML `<annotation>` | Charge annotation | Integer |
| `layer_z` | Constant in `14_metabolomics.py` | n/a | `900` (Layer 4; set by ADR-0009) |
| `source_db` | `14_metabolomics.py` | n/a | `"Recon3D"` |
| `source_version` | `14_metabolomics.py` | n/a | `"3.04"` |

**Key:** `hmdb_id` (primary, e.g. `HMDB0000122`). If absent: `chebi_id` (e.g. `CHEBI:4167`).

---

### cCRE *(Phase 3, gated)*

| Field | Source file | Column / API field | Transformation |
|-------|-------------|-------------------|----------------|
| `encode_id` | ENCODE cCRE BED (`encode_ccre.bed.gz`) | Column 4 (name) | String (e.g. `EH38E1234567`) |
| `chromosome` | ENCODE cCRE BED | Column 1 (chrom) | String (e.g. `chr7`) |
| `start_grch38` | ENCODE cCRE BED | Column 2 (chromStart) | Integer, 0-based |
| `end_grch38` | ENCODE cCRE BED | Column 3 (chromEnd) | Integer |
| `ccre_type` | ENCODE cCRE BED | Column 6 (or BED9 name field) | `"PLS"` \| `"pELS"` \| `"dELS"` \| `"CTCF-only"` \| `"DNase-H3K4me3"` |
| `layer_z` | Constant in `15_encode.py` | n/a | `0` (genomics layer — DNA-level elements) |
| `source_db` | `15_encode.py` | n/a | `"ENCODE"` |
| `source_version` | `15_encode.py` | n/a | `"V4"` |

**Key:** `encode_id` (Registry V4 accession string).

---

## 4. Edge Field Provenance

### PRODUCES (Gene → Transcript)

| Field | Source | Column | Notes |
|-------|--------|--------|-------|
| `tw_whole_blood` | GTEx v10 RDS | `Whole_Blood` (column map key) | Normalised 0–1 by 99th-percentile per tissue across all genes; gene-level weight propagated to all transcripts |
| `tw_liver` | GTEx v10 RDS | `Liver` | Same normalisation |
| `tw_brain_prefrontal_cortex` | GTEx v10 RDS | `Brain_Frontal_Cortex_BA9` | Same normalisation |
| `source_db` | `03_gtex.py` | n/a | `"GTEx_v10"` |

GTEx raw column → graph key map (GTEX_COLUMN_MAP in `03_gtex.py`):
```
Whole_Blood                → tw_whole_blood
Liver                      → tw_liver
Brain_Frontal_Cortex_BA9   → tw_brain_prefrontal_cortex
```

---

### TRANSLATES_TO (Transcript → Protein)

| Field | Source | Column | Notes |
|-------|--------|--------|-------|
| `source_db` | `05_proteins.py` | n/a | `"GENCODE_SwissProt"` |

Created from GENCODE SwissProt metadata (3-col TSV bundled with GENCODE release):
  column 1 = versioned ENST (e.g. `ENST00000269305.9`)
  column 2 = UniProt accession (e.g. `P04637`)
  column 3 = versioned UniProt accession (ignored)
Version suffix stripped from ENST before matching.

---

### ENCODES (Gene → Protein)

| Field | Source | Notes |
|-------|--------|-------|
| `source_db` | `05_proteins.py` | `"HGNC"` — fallback when canonical transcript absent from GENCODE SwissProt file |

This is the fallback edge. `TRANSLATES_TO` is preferred where available.

---

### REGULATES (Protein → Gene)

| Field | Source | Column | Notes |
|-------|--------|--------|-------|
| `confidence` | DoRothEA `dorothea_hs.rda` | `confidence` | `"A"` through `"E"`; only A+B loaded by default |
| `mode` | DoRothEA `dorothea_hs.rda` | `mor` | `"activator"` or `"repressor"` |
| `weight` | DoRothEA `dorothea_hs.rda` | n/a | Derived from confidence: A=1.0, B=0.85, C=0.7, D=0.5, E=0.25 |
| `source_db` | `04_dorothea.py` | n/a | `"DoRothEA"` |

Confidence→weight mapping (CONFIDENCE_VALUES in `04_dorothea.py`):
```
A → 1.0   (literature curated)
B → 0.85  (ChIP-seq supported)
C → 0.7   (inferred)
D → 0.5   (low confidence)
E → 0.25  (very low confidence)
```

Default filter: only A and B loaded (`DOROTHEA_MIN_CONFIDENCE=A,B`).

---

### INTERACTS_WITH (Protein ↔ Protein)

| Field | Source | Column | Notes |
|-------|--------|--------|-------|
| `combined_score` | STRING v12 human links | `combined_score` | Divided by 1000 → 0–1 float |
| `experimental_score` | STRING v12 human links | `experimental_score` | Divided by 1000 → 0–1 float |
| `coexpression_score` | STRING v12 human links | `coexpression_score` | Divided by 1000 → 0–1 float |
| `source_db` | `07_string.py` | n/a | `"STRING_v12"` |

ENSP IDs are mapped to UniProt via IdMapper before edge creation. Threshold:
`STRING_MIN_CONFIDENCE * 1000` (default `0.9 * 1000 = 900`). At 0.9: ~50k edges.
At 0.7: ~130k edges.

**Traversal cap:** `STRING_MAX_EXPAND_PER_NODE` (default 10) — top-k by
`combined_score` per frontier step. Prevents hub-protein explosion.

---

### ASSOCIATED_WITH (Variant → Disease)

| Field | Source | Column | Notes |
|-------|--------|--------|-------|
| `p_value` | GWAS Catalog TSV | `P-VALUE` | Float; filters at `GWAS_MIN_SIGNIFICANCE=5e-8` |
| `pmids` | GWAS Catalog TSV | `PUBMEDID` | List of PubMed IDs |
| `beta` | GWAS Catalog TSV | `OR or BETA` | Effect size (may be OR or beta depending on study) |
| `source_db` | `08_gwas.py` | n/a | `"GWAS_Catalog"` |

**Conductance** in signal-decay traversal = `-log10(p_value)` normalised 0–1
against genome-wide significance floor (p=5×10⁻⁸).

---

### IN_GENE (Variant → Gene)

| Field | Source | Column | Notes |
|-------|--------|--------|-------|
| `consequence_type` | GWAS Catalog TSV | `CONTEXT` or VEP annotation | e.g. `"missense_variant"`, `"intergenic_variant"` |
| `source_db` | `08_gwas.py` | n/a | `"GWAS_Catalog"` |

---

### IMPLICATED_IN (Gene → Disease)

Roll-up edge created when a Variant is both IN_GENE for a gene and ASSOCIATED_WITH
a disease. Created entirely within `08_gwas.py` — no additional fields beyond
`source_db = "GWAS_Catalog"`.

---

### DIFFERENTIALLY_EXPRESSED (Gene → Disease) *(Phase 3)*

| Field | Source | Column | Notes |
|-------|--------|--------|-------|
| `log2fc` | `13_tcga.py` | Derived: `log2((tumor_median+0.01)/(gtex_proxy+0.01))` | Proxy log2FC using TCGA FPKM tumor median vs GTEx tw_whole_blood as normal |
| `direction` | `13_tcga.py` | n/a | `"up"` if log2fc > 0, `"down"` if < 0 |
| `tumor_type` | `13_tcga.py` | TCGA phenotype `_primary_disease` → cancer code (crosswalk) | e.g. `"LUAD"`, `"BRCA"` |
| `n_tumor` / `n_normal` | `13_tcga.py` | sample counts per cohort | provenance for the contrast |
| `source_db` | `13_tcga.py` | n/a | `"TCGA_XENA"` |
| `source_version` | `13_tcga.py` | n/a | `"toil_rsem_fpkm"` |

**Conductance** = `min(1.0, abs(log2fc) / 4.0)`.
Threshold: `abs(log2fc) >= TCGA_MIN_LOG2FC` (default 1.0).

**Method (matched-normal):** the Toil matrix (`data/raw/tcga_RSEM_gene_fpkm.gz`) is
already `log2(fpkm+0.001)`, so `log2fc = median_tumour − median_normal` per cohort,
where tumour = TCGA sample_type_id 01-09 and normal = 10-19 (from
`data/raw/TCGA_phenotype_denseDataOnlyDownload.tsv.gz`). Cohorts with fewer than
`TCGA_MIN_NORMALS` adjacent normals are skipped (no fabricated baseline). This
replaced an earlier GTEx-whole-blood "proxy normal" (dimensionally invalid).
**Caveat:** median cohort DE is still simplified (subtype-specific changes wash out;
publication-grade work needs DESeq2/edgeR on counts) — sufficient for directional
signal traversal.

Cohort name → disease ontology id comes from the curated, graph-verified crosswalk
`etl/reference/tcga_disease_to_efo.tsv` (28/33 cohorts resolve to a present Disease
node). The raw Open Targets `cancer2EFO_mappings.tsv` is its documented upstream but
is NOT read directly — its EFO ids covered only 4/33 of this graph's Disease set.

---

### CATALYSES (Protein → Metabolite) *(Phase 3)*

| Field | Source | Column | Notes |
|-------|--------|--------|-------|
| `role` | Recon3D `.mat` `S` matrix | sign of the stoichiometric coefficient (S<0 reactant, S>0 product) | `"substrate"` or `"product"` |
| `reaction_id` | Recon3D `.mat` `rxns` | reaction id | e.g. `10FTHF5GLUtl` |
| `source_db` | `14_metabolomics.py` | n/a | `"Recon3D"` |
| `source_version` | `14_metabolomics.py` | n/a | `"3.01_mat"` |

**Source format:** the distributed Recon3D archive ships a MATLAB COBRA model
(`Recon3D_301.mat`), NOT SBML. `14_metabolomics.py` reads it with `scipy.io.loadmat`:
metabolites from `mets`/`metHMDBID`/`metCHEBIID`/`metNames`/`metFormulas`/`metCharges`/
`metInChIString`, reaction→gene from `rxnGeneMat`, reaction→metabolite (+role) from `S`.
HMDB (`hmdb_metabolites.zip`, streamed) fills canonical name/inchikey for HMDB-keyed mets.

**Conductance** = 0.7 (fixed; enzymatic link). Not dense-capped.

Gene → Protein mapping: Recon3D genes are **Entrez ids** (e.g. `8639.1`), crosswalked
to Ensembl via the HGNC file, then `MATCH (g:Gene {ensembl_id:$eid})-[:ENCODES|PRODUCES|
TRANSLATES_TO*1..2]->(p:Protein)`. ⚠ CATALYSES is gated on the proteome in the graph:
with a partial proteome (Protein=117) the metabolite **nodes** load but CATALYSES is
sparse and the layer is largely disconnected until the full proteome is loaded.

---

### BINDS (Protein → cCRE) *(Phase 3, gated)*

| Field | Source | Column | Notes |
|-------|--------|--------|-------|
| `chip_score` | ENCODE ChIP-seq signal | ENCODE peak p-value normalised 0–1 | Signal strength from TF binding experiment |
| `experiment_accession` | ENCODE metadata | ENCODE experiment accession | e.g. `ENCSR000EVZ` |
| `source_db` | `15_encode.py` | n/a | `"ENCODE"` |
| `source_version` | `15_encode.py` | n/a | `"V4"` |

**Conductance** = `chip_score` (0–1). Only created for TF proteins (`subtype='transcription_factor'`).

---

### REGULATES_VIA (cCRE → Gene) *(Phase 3, gated)*

| Field | Source | Notes |
|-------|--------|-------|
| `distance_bp` | `15_encode.py` | Genomic distance in bp from cCRE midpoint to gene TSS |
| `source_db` | `15_encode.py` | `"ENCODE"` |

Structural proximity link: each cCRE assigned to nearest gene(s) within 500 kb.
Cap: max 3 nearest genes per cCRE to prevent hub-gene domination.
**Conductance** ≈ 1.0 (structural; the signal comes from the BINDS edge upstream).

---

## 5. Neo4j Index Catalog

All indexes are created at backend startup via `create_indexes()` in
`backend/db/neo4j_client.py`.

| Index name | Type | Labels / properties | Purpose |
|------------|------|---------------------|---------|
| `gene_ensembl_idx` | B-tree | `Gene.ensembl_id` | Primary traversal key for genes |
| `gene_symbol_idx` | B-tree | `Gene.hgnc_symbol` | Symbol lookup (API queries, ETL matches) |
| `transcript_idx` | B-tree | `Transcript.ensembl_tx_id` | GENCODE transcript lookup |
| `protein_uniprot_idx` | B-tree | `Protein.uniprot_id` | Primary key for proteins |
| `variant_rsid_idx` | B-tree | `Variant.rsid` | Variant lookup by rsid |
| `disease_efo_idx` | B-tree | `Disease.ontology_id` | EFO ID primary key for diseases |
| `metabolite_hmdb_idx` | B-tree | `Metabolite.hmdb_id` | HMDB ID primary key *(Phase 3)* |
| `metabolite_chebi_idx` | B-tree | `Metabolite.chebi_id` | ChEBI fallback lookup *(Phase 3)* |
| `ccre_encode_idx` | B-tree | `cCRE.encode_id` | cCRE primary key *(Phase 3, gated)* |
| `node_search` | Fulltext | `Gene\|Transcript\|Protein\|Disease` on `[hgnc_symbol, description, summary_text, name]` | Autocomplete, EntityBrowser search |
| `node_search` *(updated Phase 3)* | Fulltext | adds `Metabolite` on `[name, formula]` | Metabolite search in EntityBrowser |
| `gene_embedding_idx` | Vector (cosine, 1536-dim) | `Gene.embedding` | Semantic gene search |
| `protein_embedding_idx` | Vector (cosine, 1536-dim) | `Protein.embedding` | Semantic protein search |
| `disease_embedding_idx` | Vector (cosine, 1536-dim) | `Disease.embedding` | Semantic disease search |

**Notes:**
- Variants are NOT in the fulltext index (no free-text search field on variants).
- Vector indexes use Neo4j native `db.index.vector` (ADR-0008).
- The fulltext index is dropped and recreated when Metabolite is added (Phase 3)
  because `CREATE FULLTEXT ... IF NOT EXISTS` does not allow modifying label lists.

---

## 6. Data Quality Notes and Known Issues

### Column guards
Every ETL script prints all column names at startup and aborts with a clear error
if expected columns are missing. This pattern (from ADR-0003 / GTEx discipline) is
applied uniformly across all scripts. If a source file changes its schema, the
script fails loudly rather than loading silently wrong data.

### HGNC symbol matching
Downstream ETL (COSMIC, GWAS, STRING, gnomAD) matches on `hgnc_symbol`. If a gene
was renamed since the HGNC snapshot, the match silently fails — genes are skipped,
not errored. Count of skipped symbols is always logged. The HGNC snapshot date is
stored on the `DataSource` node (`source_version` property).

### GWAS Catalog mapped genes
`MAPPED_GENE` column is a pipe-separated string of HGNC symbols. `08_gwas.py`
splits on ` - ` and `; ` and attempts to match each to existing Gene nodes. Genes
not in the graph at time of GWAS load produce no IN_GENE edges. Running
`08_gwas.py` after `01_hgnc.py` (and after proteome ETL) maximises coverage.

### String ENSP → UniProt mapping
STRING uses Ensembl protein IDs (ENSP). `07_string.py` maps ENSP → UniProt via
IdMapper. ~3–5% of STRING interactions involve proteins not in the human UniProt
Swiss-Prot set and are skipped. Logged as "N interactions skipped — UniProt not found."

### GTEx expression proxy for TCGA
`13_tcga.py` uses `tw_whole_blood` as a proxy normal for all tumor types. This is
a known simplification:
- Tissue-mismatched comparison (e.g. lung cancer vs blood expression)
- FPKM is not count data; log2FC proxy is directional but not statistically calibrated
- Adequate for graph traversal signal; not adequate for clinical interpretation

### Recon3D Boolean gene associations
Some Recon3D reactions have complex Boolean enzyme expressions
(`ENSG1 and ENSG2 or ENSG3`). The ETL splits on OR and takes the first valid
Ensembl ID per OR-group, ignoring AND logic (isozyme groups). This is conservative:
it may undercount catalytic edges for multi-enzyme complexes. Count of reactions
with unparsed complex associations is logged.

### HMDB streaming
`hmdb_metabolites.zip` is ~1.5 GB uncompressed. The ETL uses `xml.etree.ElementTree
iterparse` to avoid loading the full XML into RAM. A minimal lookup TSV
(`data/processed/hmdb_lookup.tsv`) is written; the unzipped XML is deleted
after processing to save disk.

### Variant gnomAD allele frequency
`Variant.gnomad_af` is not populated in Phase 2 (gnomAD ETL focused on gene
constraint pLI scores, not variant-level allele frequencies). This is a known
gap — variant-level gnomAD annotation is deferred.

### ENCODE (Phase 3, gated)
1.7M cCRE nodes cannot be loaded on Neo4j Community Edition without OOM errors.
`15_encode.py` is hard-gated: it checks `MATCH (n) RETURN count(n)` and refuses
to start unless > 500k nodes are present (indicating AuraDB migration has occurred)
or the user explicitly sets `ENCODE_FORCE_LOAD=true`. Never set `ENCODE_FORCE_LOAD`
on Community Edition.

---

## 7. Tunable Parameters

All parameters are environment variables with defaults in `.env.example`. Never
hardcode any of these values in ETL or traversal code.

| Env var | Default | Controls | Phase introduced |
|---------|---------|---------|-----------------|
| `DOROTHEA_MIN_CONFIDENCE` | `A,B` | DoRothEA confidence tiers loaded | Phase 1 |
| `STRING_MIN_CONFIDENCE` | `0.9` | STRING PPI threshold (0.9 ≈ 50k edges; 0.7 ≈ 130k edges) | Phase 2 |
| `STRING_MAX_EXPAND_PER_NODE` | `10` | Hub-protein traversal cap per frontier step | Phase 2 |
| `GWAS_MIN_SIGNIFICANCE` | `5e-8` | GWAS p-value cutoff for ASSOCIATED_WITH edges | Phase 2 |
| `NCBI_API_KEY` | (empty) | NCBI E-utilities rate limit: 3 req/s without, 10 req/s with | Phase 2 |
| `min_signal` (ε) | `0.05` | Signal-decay floor — traversal stops below this | Phase 1 |
| `decay` (d) | `0.7` | Per-hop signal decay multiplier | Phase 1 |
| `max_nodes` | `150` | Hard subgraph size cap | Phase 1 |
| `TCGA_MIN_LOG2FC` | `1.0` | Minimum absolute log2FC for DIFFERENTIALLY_EXPRESSED edges | Phase 3 |
| `TCGA_MAX_ADJ_PVALUE` | `0.05` | Adjusted p-value cutoff for TCGA edges (not yet implemented — reserved) | Phase 3 |
| `METABOLOMICS_MIN_REACTIONS` | `1` | Min reactions a Metabolite must appear in (default = keep all) | Phase 3 |
| `ENCODE_BATCH_SIZE` | `5000` | cCRE nodes per Cypher UNWIND batch (AuraDB per-transaction limit) | Phase 3 |
| `ENCODE_FORCE_LOAD` | `false` | Override AuraDB gate (NEVER use on Community Edition) | Phase 3 |

---

## 8. Agent Write Catalog

Background agents run independently from the ETL pipeline. They read from the
graph, call external APIs, and write back to specific node properties.

### EmbeddingAgent

| Property | | Details |
|----------|--|---------|
| **Reads** | Node types | `Gene`, `Protein`, `Disease` |
| **Reads** | Filter | `summary_text IS NOT NULL AND embedding IS NULL` |
| **Writes** | `Gene.embedding` | 1536-dim float array |
| **Writes** | `Protein.embedding` | 1536-dim float array |
| **Writes** | `Disease.embedding` | 1536-dim float array |
| **Writes** | `*.embedding_model` | `"text-embedding-3-small"` |
| **Writes** | `*.embedding_at` | timestamp |
| **Writes** | `*.source_agent` | `"EmbeddingAgent"` |
| **Writes** | `*.agent_version` | version string |
| **API** | OpenRouter | `base_url=https://openrouter.ai/api/v1`, model `text-embedding-3-small`, 1536-dim |
| **Run trigger** | Nightly batch or manual | Processes only nodes with null embedding |
| **Skipped nodes** | `Transcript`, `Variant`, `Metabolite` | No meaningful free text for these types |

**Safety:** EmbeddingAgent writes only to `embedding`, `embedding_model`,
`embedding_at`, `source_agent`, `agent_version`, `run_timestamp` properties.
It never creates nodes, edges, or modifies any biological field.

---

### Text2Cypher (query-time, not batch)

Not a batch agent — fires on each user query. Does not write to the graph.

| Property | Details |
|----------|---------|
| **Reads** | `apoc.meta.schema()` at startup (cached per process lifetime) |
| **Generates** | Cypher query from natural language |
| **Validates** | `validate_cypher()` blocks any write keyword (MERGE/CREATE/DELETE/SET) |
| **Executes** | Read-only Cypher against Neo4j |
| **Model** | Configured via `LLM_MODEL` env var (default: claude-sonnet-4-6 via OpenRouter) |

---

## 9. Phase 3 Additions (planned)

See `08_phase3_build_prompt.md` for full implementation instructions.

### New nodes
| Node type | Canonical key | Layer | Color | Source |
|-----------|--------------|-------|-------|--------|
| `Metabolite` | `hmdb_id` (primary) / `chebi_id` (fallback) | Layer 4 (`layer_z=900`) | `#fb923c` orange | Recon3D 3.04 SBML |
| `cCRE` | `encode_id` | Genomics (`layer_z=0`) | `#475569` charcoal | ENCODE Registry V4 |

### New edges
| Edge type | Source → Target | Conductance | Source |
|-----------|----------------|-------------|--------|
| `DIFFERENTIALLY_EXPRESSED` | Gene → Disease | `min(1.0, abs(log2fc)/4.0)` | TCGA Xena PANCAN |
| `CATALYSES` | Protein → Metabolite | 0.7 (fixed) | Recon3D 3.04 |
| `BINDS` | Protein → cCRE | `chip_score` (0–1) | ENCODE ChIP-seq |
| `REGULATES_VIA` | cCRE → Gene | ~1.0 | ENCODE (structural proximity, 500kb) |

### Layer Z shift (ADR-0009)
Disease shifts from `layer_z=900` → `layer_z=1200` (backend constant `DISEASE_LAYER_Z`).
Frontend phenotype Y shifts from 600 → 900. New metabolomics plane at Y=600.
Any hardcoded `900` not going through the constant will break silently — audit
with `grep -rn "= 900" frontend/src/ backend/` before implementing Phase 7.

### New gene property
`Gene.cancer_gene` (bool, pre-existing but previously null) and `Gene.cosmic_tier`
(`"1"` or `"2"`) sourced from COSMIC CGC v99 by `12_cosmic.py`.
