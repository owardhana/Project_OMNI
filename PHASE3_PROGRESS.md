# Phase 3 Build — Progress Tracker

Durable state for the `/loop` autonomous build of Phase 3 (TCGA + Metabolomics +
ENCODE). Each loop iteration reads this first to resume. Status legend:
`TODO` · `IN_PROGRESS` · `CODE_COMPLETE` (written, statically checked, but a live-graph
gate is unrun) · `DONE` (code + verification gates passed) · `BLOCKED` · `GATED`.

Branch: `phase-3-hpc-singularity` (name is stale from prior HPC work; the
uncommitted Metabolite glossary in CONTEXT.md confirms this is the Phase-3 line).

## Environment facts (re-confirm each session)
- Neo4j: started via `docker compose up neo4j -d` (named volume `neo4j_data`). LIVE.
- Node count at start: **601,558** (Variant 325k, Transcript 221k, Gene 42k,
  Disease 13k, **Protein only 117**). → verify-as-you-go for backend.
- Backend venv: `backend/.venv/bin/python` (has fastapi/neo4j/pydantic).
- etl venv: `etl/.venv/bin/python` (has pandas/numpy; libsbml NOT installed).
- ⚠ Protein=117 (not ~20k "full proteome"). Phase-6 CATALYSES will be SPARSE and
  the LDHA gate will likely fail even with Recon3D data → user must re-run the
  Phase-2 proteome ETL (05_proteins/06_uniprot_enrich) for a full proteome.
- ⚠ Total nodes 601k already > the Phase-9 migration trigger (500k). Phase 9 still
  HARD-GATED on user-driven AuraDB migration — do NOT attempt on Community.

## DATA STATUS (updated 2026-06-22 — user supplied files into data/raw/)
The user downloaded the previously-blocked Phase-3 files (via the alternate URLs
that were in 00_download.sh #comments). On inspection:
- **TCGA expression + phenotype + cttv map → RESOLVED & LOADED.** Files arrived
  under their real names (`tcga_RSEM_gene_fpkm.gz`, `TCGA_phenotype_denseDataOnly
  Download.tsv.gz`, `cancer2EFO_mappings.tsv`). 00_download.sh + 13_tcga.py updated
  to those names. The matrix is Toil RSEM FPKM (values already log2(fpkm+0.001)),
  so 13_tcga.py was reworked: matched tumour-vs-normal log2FC (difference of
  medians; barcode/sample_type split) replacing the dimensionally-wrong GTEx
  whole-blood proxy. Cohort→disease via the new curated, graph-verified crosswalk
  etl/reference/tcga_disease_to_efo.tsv (28/33 cohorts resolve to a present Disease;
  cttv's own EFO ids only covered 4/33). **Result: 128,714 DIFFERENTIALLY_EXPRESSED
  edges across 16 cohorts** (the other 12 mapped cohorts lack ≥10 adjacent normals
  — OV/LAML/GBM/LGG/TGCT/UVM/UCS have 0 normals — and 5 have no disease node).
- **COSMIC CGC → RESOLVED & LOADED.** The user downloaded the v104 GRCh37 tar
  (`Cosmic_CancerGeneCensus_Tsv_v104_GRCh37.tar`, a `*.tsv.gz` with GENE_SYMBOL/TIER).
  12_cosmic.py now auto-resolves tar/tsv.gz/csv and the column-naming variants.
  **Result: 752 genes flagged cancer_gene=true** (580 tier-1, 172 tier-2; TP53→tier 1).
- **Recon3D → RESOLVED & LOADED (via .mat).** The `Recon3D_301.zip` ships a MATLAB
  COBRA model (.mat), not SBML, so 14_metabolomics.py was rewritten to use
  `scipy.io.loadmat` (mets/metHMDBID/metCHEBIID/metNames/metFormulas/metCharges/
  metInChIString + rxnGeneMat + S matrix). Recon3D genes are Entrez ids → crosswalked
  to Ensembl via the HGNC file → UniProt via the graph. **Result: 1,277 Metabolite
  nodes** (deduped per chemical). ⚠ **CATALYSES = 8 edges only** — just 5 of 3,283
  mapped genes hit the partial proteome (Protein=117 = the TF subset), so the
  metabolite layer is loaded but largely DISCONNECTED until the full proteome is
  loaded (05_proteins/06_uniprot_enrich).
- **HMDB → USED.** `hmdb_metabolites.zip` (6.4GB xml) is streamed (iterparse, bounded
  mem) to fill canonical name/inchikey for HMDB-keyed metabolites. 698 nodes enriched
  (Recon3D `HMDB00015` normalised to `HMDB0000015` to join — verified nonzero).

## ENCODE / Phase 9 — HELD OFF (confirmed by user 2026-06-22)
Phase 9 (ENCODE cCREs) remains intentionally NOT started. The graph is already
>500k nodes (the migration trigger) and Neo4j Community would OOM; it needs the
user-driven AuraDB migration first. User explicitly confirmed it is OK to hold.

## Phase status
| Phase | Scope | Status |
|-------|-------|--------|
| 1 | downloads (00_download.sh) | DONE — URLs/names point at the working files now (TCGA loaded; COSMIC/Recon3D noted) |
| 2 | COSMIC flags (12_cosmic.py) + DAG | **DONE — 752 genes flagged cancer_gene from v104 tar; verified** |
| 3 | TCGA DE (13_tcga.py) + DAG | **DONE — 128,714 DIFFERENTIALLY_EXPRESSED edges / 16 cohorts loaded & verified** |
| 4 | Backend TCGA models+traversal+/cancer | DONE (live-verified; data gate now RUN — see ledger) |
| 5 | ADR-0009 | DONE |
| 6 | Metabolomics ETL (14_metabolomics.py via scipy .mat + HMDB) | **DONE — 1,277 metabolites loaded & verified; CATALYSES=8 (sparse, proteome-gated)** |
| 7 | Backend metabolite models+Z shift+indexes+API | DONE (live-verified; metabolite data gates now RUN — see ledger) |
| 8 | Frontend metabolomics layer + Z shift + UI polish | DONE — 8a/8b (33d27ed) + 8c (41fd9de); + colour-consistency fix (see ledger) |
| 9 | ENCODE cCREs | HELD OFF (user-confirmed 2026-06-22; needs AuraDB migration) |
| 10 | Tests | DONE for backend (layer-z PASS; all Phase-3 data gates now RUN/PASS) |

## Verification ledger
- Phase 4 conductance: PASS (DE |log2fc|=2→0.5, =8→1.0 cap, none→0.25; CATALYSES 0.7).
- Phase 4 dense-cap: PASS (DIFFERENTIALLY_EXPRESSED capped; CATALYSES not).
- Phase 7 layer-Z: PASS (METABOLITE=900, DISEASE=1200; test_five_layer_z, test_layer_z_no_overlap).
- Phase 7 Pydantic union: PASS (MetaboliteNode resolves via discriminator).
- Phase 7 index DDL: PASS (create_indexes() applied node_search widen + metabolite idxs).
- Phase 4/7 endpoints: PASS no-crash on live graph (/cancer→[], metabolite lookup→None).
- Regression: PASS (15 existing query tests; search + TP53 traversal intact).
- Phase 2 gate (cancer_gene>0): **PASS — 752 genes flagged** (580 tier-1, 172 tier-2;
  TP53→cancer_gene=true tier 1). Loaded from the v104 COSMIC tar.
- Phase 3 gate (DE edges>1000): **PASS — 128,714 edges, 16 cohorts** (range log2fc
  -18.25..13.69; ERBB2 up in UCEC / down in renal; DataSource 13_tcga recorded).
  Both data-gated tests' assertions verified PASS via direct Cypher:
  test_differentially_expressed_edges (count 128,714>0) and test_tcga_traversal
  (TP53 -> 1 DE row: ESCA up +1.42 MONDO_0019086, with tumor_type+efo_id). NOTE:
  the pytest *runner* couldn't finish `collecting...` — module import through the
  iCloud-synced project dir (~/Desktop is symlinked into CloudDocs) is pathologically
  slow (also why tsc -b took minutes). Env issue, not a code/test failure.
- Phase 6 gate: **PARTIAL PASS.** metabolites>1000 → **1,277 PASS** (698 HMDB-enriched
  w/ inchikey; layer_z=900; searchable: Cortexolone/S-Adenosylmethionine resolve).
  CATALYSES>5000 → **FAIL by design: only 8 edges** (5/3,283 genes hit Protein=117).
  LDHA→lactate → N/A (LDHA's protein not among the 117). Metabolite endpoints live-
  verified: /metabolite/{id}→200, /metabolite/{id}/graph→bounded 150-node neighborhood
  (Proton→protein→network). The layer is loaded but disconnected pending full proteome.
- `= 900` audit: PASS backend (only via METABOLITE_LAYER_Z constant) + frontend
  (only intended metabolomics y:600 / phenotype y:900; DISEASE_LAYER_Z now 1200).
- Phase 8a/8b (commit 33d27ed): PASS tsc -b + vite build (both green; dist built).
  Dev server loads with NO console errors; live graph confirms disease nodes
  target Y=900 (Z-shift) and the 5-layer toggle renders (genomics/transcriptomics/
  proteomics/metabolomics/phenotype). Checks 1,4 PASS; checks 2,3,6 (metabolite +
  CATALYSES/DIFFERENTIALLY_EXPRESSED render) UNRUN — no Recon3D/TCGA data.
  NOTE: production `tsc -b` was already RED on HEAD pre-Phase-8 (hgnc_symbol on
  FGNode union, d3-force-3d types) — fixed those too to get a green build.
- Colour consistency (2026-06-22): FIXED. The 3D graph (nodeColor) and the legend
  (GraphLegend) both already read NODE_COLORS; the drift was the TOP layer toggle,
  which used a single per-LAYER `accent` and so hid the 2nd node colour in the two
  multi-type layers (genomics = gene-green + variant-teal; proteomics = protein-
  violet + TF-amber). Fix: removed `accent`; added LAYER_NODE_COLORS (derived from
  NODE_COLORS) so the toggle renders one swatch per node colour the layer actually
  contains — toggle, legend, and graph now share the single NODE_COLORS source.
- Phase 8c (commit 41fd9de): DONE + live-verified. Checks 7 (glass — all 4 panels
  blur(12px)+rgba(250,249,245,0.82)), 8 (status bar 150 nodes·289 edges·TP53·ORBIT),
  9 (dynamic legend), 10 (? overlay) PASS. Check 11 (hover tooltip) wired+typechecks
  but real canvas hover needs the raycaster (not headless-verifiable). 2/3/6 UNRUN
  (metabolite/edge render — no data). Glass adapted to the LIGHT theme (dark glass
  would force a full palette inversion) — say the word for a full dark theme.

## BUILD STATUS (updated 2026-06-22, second pass — user supplied COSMIC + asked to
## parse Recon3D via scipy and use HMDB)
- **Phases 1–8 + 10 all DONE + verified.** All three data-blocked phases now LOADED:
  Phase 2 COSMIC (752 cancer-gene flags), Phase 3 TCGA DE (128,714 edges / 16 cohorts),
  Phase 6 metabolomics (1,277 metabolites, HMDB-enriched).
- **One known limitation:** Phase-6 CATALYSES = 8 edges only (the metabolite layer is
  loaded but largely disconnected) because the graph holds a partial proteome
  (Protein=117). The single remaining unblock for a *connected* metabolite layer is to
  load the full proteome (05_proteins/06_uniprot_enrich) and re-run 14_metabolomics.py.
- Phase 9 (ENCODE) HELD OFF — user-confirmed; needs the AuraDB migration first.

## Notes / decisions
- "agent writes carry source_agent/agent_version/run_timestamp" applies to
  backend/agents (LLM processes), NOT deterministic ETL scripts — ETL uses
  source_db/source_version (matches existing 08_gwas.py, 11_gnomad.py).
- TCGA DE methodology change (2026-06-22): switched from a GTEx-whole-blood "proxy
  normal" to matched tumour-vs-adjacent-normal log2FC. Forced (the proxy is
  dimensionally meaningless), but it changes edge semantics and drops cohorts with
  no adjacent normals — a user-vetoable decision, recorded here and in 13_tcga.py.
