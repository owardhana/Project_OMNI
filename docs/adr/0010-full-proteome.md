# ADR 0010 — Scale the proteomics layer from the TF slice to the full proteome

Status: Accepted (2026-06-22); **amended 2026-06-23** (see "Amendment" below)

> **Amendment (2026-06-23) — consolidated into `05_proteins.py`, STRING threshold
> raised.** The full-proteome mint originally shipped as a separate `05b_full_proteome.py`
> placed late in the pipeline (after `07_string`) specifically to avoid building PPI
> over the whole proteome. That separation has been **reversed by decision**: the full
> proteome is now minted directly in `05_proteins.py` (which also tags the TF subtype
> from REGULATES and migrates REGULATES), `05b_full_proteome.py` is **deleted**, and
> full-proteome PPI is now *wanted*. Because `07_string` now runs over ~20k proteins,
> its `STRING_MIN_CONFIDENCE` default was raised **0.9 → 0.95** (highest-confidence
> band only) to keep `INTERACTS_WITH` at a manageable volume. This supersedes the
> "Extend 05_proteins.py in place" rejection and the "deferred PPI" reasoning in the
> sections below, which are retained as the original record.
>
> **Amendment 2 (2026-06-23) — UI + traversal fallout of the full proteome.** Loading
> the full proteome + PPI surfaced three frontend/traversal issues, now fixed:
> 1. **Metabolite recoloured orange `#fb923c` -> cyan `#22d3ee`** (node, metabolomics
>    plane tint, CATALYSES edge). The old orange collided with the TF-protein amber
>    `#f59e0b` in the layer-selection bar + legend. The legend is data-driven (it
>    auto-includes Metabolite + Catalyses once present); only the colour needed fixing.
> 2. **Metabolite neighbourhoods were unreachable from the UI** — `client.ts` had no
>    `/api/metabolite/{id}/graph` loader and `App.handleSelect` routed metabolite
>    clicks into `loadGene(id)` (404, stale graph). Added `getMetaboliteGraph` +
>    `useGraph.loadMetabolite` + a metabolite branch in `handleSelect`. This — not the
>    node cap — was the real reason "no metabolomics" showed.
> 3. **`REGULATES` now dense-capped** (`REGULATES_MAX_EXPAND_PER_NODE=25`, top-k by
>    DoRothEA confidence) and `TRAVERSAL_MAX_NODES` raised 150 -> 300. A master-
>    regulator TF (TP53 -> hundreds of targets) flooded gene-seeded views and starved
>    the molecular backbone, so proteins/metabolites never appeared. Partial fix
>    (ADR-0005 amendment): deep nodes (metabolites are ring-3 via gene->protein->
>    CATALYSES) are still trimmed when ring-2 variants fill the budget. **Deferred:** a
>    "balanced traversal" guaranteeing multi-layer capture vs connectivity breadth —
>    its own future ADR.

## Context

ADR-0004 introduced `:Protein` nodes but deliberately scoped the MVP to a thin
slice: mint a protein **only** for genes that act as transcription factors in
DoRothEA (~117), and explicitly listed *"Full proteome now (mint all
protein-coding genes)"* as **rejected for MVP** — *"Scale to the full proteome
later."*

That deferral became the binding constraint for Phase-6 metabolomics (ADR-0009).
`14_metabolomics.py` mints `(:Protein)-[:CATALYSES]->(:Metabolite)` edges by
mapping Recon3D reaction genes (Entrez → Ensembl → graph `:Protein`). With only
the 117 TF proteins in the graph, just **5 of 3,283** mapped Recon3D genes
resolved to a protein, so CATALYSES had **8 edges** and the metabolite layer,
though populated (1,281 nodes), was effectively **disconnected** from the rest of
the graph — signal-decay traversal could not flow into it.

This is the "later" ADR-0004 anticipated.

## Decision

Mint the **full human proteome** as a new ETL step `etl/05b_full_proteome.py`,
structurally "05 minus the TF filter":

- **Input set** — every HGNC gene that resolves to a UniProt accession
  (`hgnc_complete_set.txt`, ~20k), instead of only the TF symbols wired in
  `REGULATES`. One `:Protein` per accession (first gene wins on collision).
- **Edge logic reused verbatim from 05** so a protein is never orphaned:
  `(:Transcript)-[:TRANSLATES_TO]->(:Protein)` primary (GENCODE SwissProt
  metadata, ENST → UniProt, for transcripts in the graph) + `(:Gene)-[:ENCODES]
  ->(:Protein)` fallback for proteins with no transcript link.
- **The 117 TFs are preserved, not clobbered** — `MERGE` on `uniprot_id` with
  `ON CREATE SET` so `subtype='transcription_factor'` / `hgnc_symbol` survive on
  the pre-existing nodes. New proteins get `entity_kind='protein'` and **no
  subtype** (they render as the generic violet `protein`, not the amber TF) until
  enrichment derives one.
- **No REGULATES migration** (that is TF-specific and 05 already did it).
- **Provenance** — `source_db='HGNC'` on minted proteins and ENCODES edges,
  `source_db='GENCODE_SwissProt'` on TRANSLATES_TO (deterministic-ETL provenance,
  matching 05; not the agent-write fields).

Idempotent and safe to run after 05; re-runnable.

## Consequences

- **CATALYSES densifies 8 → 24,545**; 3,265 / 3,283 Recon3D genes now map to a
  protein (the HGNC-coverage ceiling is 3,264). **1,199 / 1,281 metabolites (94%)
  now have ≥1 catalysing protein** — the metabolite layer is connected, and the
  canonical Phase-6 example resolves end to end: `LDHA → protein → L-Lactic acid`
  (and Pyruvic acid), i.e. the lactate-dehydrogenase reaction.
- **Node count ~603k → ~622k** (+19,960 proteins). This is well within Neo4j
  Community limits — the 500k "migration trigger" is specific to ENCODE cCRE
  *volume* (Phase 9), **not** a node cap, so this does **not** trip the Phase-9
  gate. Phase 9 remains held off pending AuraDB migration.
- **Node type / layer / colour need no change.** Node type and proteomics layer_z
  derive from the **label** (`'Protein' IN lbls`), not subtype; the frontend
  colours by `subtype === 'transcription_factor' ? amber : violet`, so the ~20k
  new non-TF proteins render violet automatically and the legend supports both.
- **One backend fix WAS required — the `is_tf` derivation (ADR-0004's predicted
  silent break).** A gene's `is_tf` was derived as "has *any* protein" — correct
  only while the proteome was the TF slice ("has a protein" ≡ "is a TF"). The full
  proteome makes ~20k genes reach a protein, so the unfiltered clause flagged
  **20,184 genes** as TFs (was ~117) → the TF badge (`SearchBar`, `NodeDetailPanel`)
  would light on essentially every gene. Fixed by adding the
  `{subtype: 'transcription_factor'}` filter to the EXISTS clause in all three query
  sites: `db/queries/genes.py` (`_GENE_IS_TF_CLAUSE`), `db/queries/graph.py`
  (`search_genes`), `db/queries/traversal.py` (`_GENE_IS_TF`). Post-fix: 117 genes
  flag `is_tf` (GAPDH/ACTB False, TP53/GATA3 True). The authoritative TF marker is
  unchanged — `subtype='transcription_factor'` on the protein — but the *gene-side
  shortcut* had to be made subtype-aware. This is badge-only; layout/colour key off
  subtype directly and were never affected.
- **06_uniprot_enrich.py is now a ~20k-accession REST crawl (~5 h at 1 req/s).**
  It is **not** required for CATALYSES connectivity (only adds function text / GO /
  subtype for embeddings) and is deferred as an optional follow-up. The new
  proteins have no `summary_text`/`embedding` until it runs.

## Alternatives considered

- **Extend 05_proteins.py in place:** rejected — 05 is the TF-model step (reads TF
  symbols from REGULATES, migrates REGULATES). Overloading it would entangle the
  TF-specific migration with full-proteome minting. A separate 05b keeps each
  step's intent legible and 05 a faithful record of ADR-0004.
- **SwissProt-only minting (TRANSLATES_TO, no ENCODES fallback):** rejected — a
  gene whose coding transcripts aren't in the SwissProt ENST set would mint a
  protein with no path back to its gene, silently yielding no CATALYSES gain. The
  ENCODES fallback (867 edges) erases that risk.
- **Run 06 enrichment as part of this step:** rejected — 5 h REST crawl, not
  needed for the connectivity goal; decoupled so the graph topology lands fast.
