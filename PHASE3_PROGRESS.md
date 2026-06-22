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
- **COSMIC CGC → STILL BLOCKED.** The downloaded `cosmic_cancer_gene_census.csv` is
  an HTML *login page* (387 lines, `<title>Login</title>`), not CSV. Sanger gates it
  behind a free account. 12_cosmic.py now detects the HTML head and aborts with a
  clear message. Phase 2 (cancer_gene flags) stays UNRUN until a real CSV is placed
  in data/raw/ (overwriting the login page).
- **Recon3D → WRONG FORMAT (held).** The downloaded `Recon3D_301.zip` contains
  MATLAB `.mat` files, NOT SBML XML, so 14_metabolomics.py (libsbml) cannot read it.
  Even with SBML, Phase 6 is gated on the full proteome (Protein=117 → ~0 CATALYSES),
  so metabolomics is HELD regardless. To unblock later: load the proteome
  (05_proteins/06_uniprot_enrich) AND fetch Recon3D as SBML → data/raw/Recon3D.xml.
- **HMDB → present but unused.** `hmdb_metabolites.xml` (6.4GB) is fine; only needed
  for name cross-ref, and Phase 6 is held anyway.

## ENCODE / Phase 9 — HELD OFF (confirmed by user 2026-06-22)
Phase 9 (ENCODE cCREs) remains intentionally NOT started. The graph is already
>500k nodes (the migration trigger) and Neo4j Community would OOM; it needs the
user-driven AuraDB migration first. User explicitly confirmed it is OK to hold.

## Phase status
| Phase | Scope | Status |
|-------|-------|--------|
| 1 | downloads (00_download.sh) | DONE — URLs/names point at the working files now (TCGA loaded; COSMIC/Recon3D noted) |
| 2 | COSMIC flags (12_cosmic.py) + DAG | CODE_COMPLETE — BLOCKED: COSMIC file is an HTML login page (HTML guard added) |
| 3 | TCGA DE (13_tcga.py) + DAG | **DONE — 128,714 DIFFERENTIALLY_EXPRESSED edges / 16 cohorts loaded & verified** |
| 4 | Backend TCGA models+traversal+/cancer | DONE (live-verified; data gate now RUN — see ledger) |
| 5 | ADR-0009 | DONE |
| 6 | Metabolomics ETL (14_metabolomics.py) + DAG + libsbml | CODE_COMPLETE — HELD: Recon3D zip is .mat not SBML + Protein=117 |
| 7 | Backend metabolite models+Z shift+indexes+API | DONE (live-verified; metabolite data gate still UNRUN — Phase 6 held) |
| 8 | Frontend metabolomics layer + Z shift + UI polish | DONE — 8a/8b (33d27ed) + 8c (41fd9de); + colour-consistency fix (see ledger) |
| 9 | ENCODE cCREs | HELD OFF (user-confirmed 2026-06-22; needs AuraDB migration) |
| 10 | Tests | DONE for backend (layer-z PASS; TCGA data gates now RUN; metabolite/COSMIC SKIP) |

## Verification ledger
- Phase 4 conductance: PASS (DE |log2fc|=2→0.5, =8→1.0 cap, none→0.25; CATALYSES 0.7).
- Phase 4 dense-cap: PASS (DIFFERENTIALLY_EXPRESSED capped; CATALYSES not).
- Phase 7 layer-Z: PASS (METABOLITE=900, DISEASE=1200; test_five_layer_z, test_layer_z_no_overlap).
- Phase 7 Pydantic union: PASS (MetaboliteNode resolves via discriminator).
- Phase 7 index DDL: PASS (create_indexes() applied node_search widen + metabolite idxs).
- Phase 4/7 endpoints: PASS no-crash on live graph (/cancer→[], metabolite lookup→None).
- Regression: PASS (15 existing query tests; search + TP53 traversal intact).
- Phase 2 gate (cancer_gene>0): UNRUN — COSMIC file is a login page (still blocked).
- Phase 3 gate (DE edges>1000): **PASS — 128,714 edges, 16 cohorts** (range log2fc
  -18.25..13.69; ERBB2 up in UCEC / down in renal; DataSource 13_tcga recorded).
  Both data-gated tests' assertions verified PASS via direct Cypher:
  test_differentially_expressed_edges (count 128,714>0) and test_tcga_traversal
  (TP53 -> 1 DE row: ESCA up +1.42 MONDO_0019086, with tumor_type+efo_id). NOTE:
  the pytest *runner* couldn't finish `collecting...` — module import through the
  iCloud-synced project dir (~/Desktop is symlinked into CloudDocs) is pathologically
  slow (also why tsc -b took minutes). Env issue, not a code/test failure.
- Phase 6 gate (metabolites>1000, CATALYSES>5000, LDHA→lactate): UNRUN — Recon3D .mat
  (not SBML) + Protein=117 (held).
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

## BUILD STATUS (updated 2026-06-22)
- Phases 1/3/4/5/7/8/10 DONE + verified. **Phase 3 (TCGA DE) is now LOADED**
  (128,714 edges) after the user supplied the data and 13_tcga.py was reworked
  (matched-normal log2FC + graph-verified crosswalk).
- Phase 2 (COSMIC) BLOCKED — supplied file is an HTML login page; needs a real CSV.
- Phase 6 (metabolomics) HELD — supplied Recon3D is .mat not SBML, and gated on the
  full proteome (Protein=117) regardless.
- Phase 9 (ENCODE) HELD OFF — user-confirmed; needs the AuraDB migration first.
- To finish the remainder: drop a real COSMIC CSV + Recon3D SBML (and reload the
  proteome) into data/raw/, then `etl/.venv/bin/python etl/12_cosmic.py` /
  `14_metabolomics.py`; OR trigger Phase 9 after AuraDB migration.

## Notes / decisions
- "agent writes carry source_agent/agent_version/run_timestamp" applies to
  backend/agents (LLM processes), NOT deterministic ETL scripts — ETL uses
  source_db/source_version (matches existing 08_gwas.py, 11_gnomad.py).
- TCGA DE methodology change (2026-06-22): switched from a GTEx-whole-blood "proxy
  normal" to matched tumour-vs-adjacent-normal log2FC. Forced (the proxy is
  dimensionally meaningless), but it changes edge semantics and drops cohorts with
  no adjacent normals — a user-vetoable decision, recorded here and in 13_tcga.py.
