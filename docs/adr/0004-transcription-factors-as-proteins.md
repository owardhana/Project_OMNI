# ADR 0004 — Transcription factors are Protein nodes in a proteomics layer

Status: Accepted (2026-06-15)

## Context

The original model (consolidated in [vision-and-mvp.md](../vision-and-mvp.md)) treats a
transcription factor as a **`:Gene`** node and `REGULATES` as a **gene→gene**
edge inside the genomics layer. "TF-ness" is not a stored type — it is *derived*
at query time as `count(outgoing REGULATES) > 0` (`backend/db/queries/genes.py`).

This is biologically wrong: a TF is a **protein** that binds DNA to regulate a
gene. The genes-only model collapses the regulator (a protein) into its gene and
loses the layer distinction. The vision always reserved a proteomics layer
"(future)"; we now pull a thin, verified slice of it into the MVP.

## Decision

Introduce a real **`Protein`** node kind and move transcription factors into a
**proteomics layer** (above transcriptomics in the stack).

- **Node identity** — machine ID = **UniProt accession** (`P04637`). Display name
  = `<symbol> (protein)` (e.g. `TP53 (protein)`). Every node carries
  `entity_kind ∈ {gene, transcript, protein}`; TF is a protein **subtype**
  (`subtype = transcription_factor`), distinguished visually by color, not layer.
  The derived `is_tf` flag is retired as a *type* discriminator (it may survive as
  a convenience boolean, but the type of truth is `entity_kind`/`subtype`).
- **Scope (MVP)** — mint a `Protein` **only** for genes that act as TFs in
  DoRothEA (a "regulatory proteome" slice), not all ~20k protein-coding genes.
  Scale to the full proteome later.
- **`REGULATES` rewired** — was `(:Gene)-[:REGULATES]->(:Gene)`; becomes
  `(:Protein)-[:REGULATES]->(:Gene)`. Directed, vertical, **downward**
  (proteomics → genomics). Target stays a gene.
- **Tie the protein to its molecule** — `(:Transcript)-[:TRANSLATES_TO]->(:Protein)`
  is the **primary** link (stepwise, transcriptomics → proteomics) used whenever
  the protein's canonical transcript is in the graph; `(:Gene)-[:ENCODES]->(:Protein)`
  is the **fallback** (direct, genomics → proteomics) so a protein is never
  orphaned. (See [CONTEXT.md](../../CONTEXT.md) for the domain definitions.)
- **Double representation accepted** — a molecule that is both a TF and a
  regulated gene (e.g. TP53) appears as **two** nodes: gene `TP53` (genomics,
  target) and `TP53 (protein)` (proteomics, regulator), joined by the vertical
  `TRANSLATES_TO`/`ENCODES` edge. This makes feedback loops visible and is the
  intended, biologically honest behavior.

## Data sources (verified 2026-06-15, no new heavyweight dependency)

| Need | Source | Verification |
|------|--------|-------------|
| Protein machine ID (symbol→UniProt) | HGNC `uniprot_ids` column | column 26 present; populated for TP53→P04637, SP1→P08047, GATA1→P15976, MYC, EGFR; 20,288 rows carry a UniProt ID. |
| `TRANSLATES_TO` (ENST→UniProt) | `gencode.v46.metadata.SwissProt.gz` | confirmed on the GENCODE v46 FTP listing (411K, 2024-05-13), separate from the GTF already pulled. |
| `ENCODES` fallback (symbol/ENSG→UniProt) | HGNC `uniprot_ids` | as above. |
| Which genes are TFs | DoRothEA TF list (already loaded) | — |

Edge cases: a TF gene with **multiple** UniProt IDs → take the reviewed/canonical
(first Swiss-Prot). A TF gene with **no** UniProt ID → cannot be a protein;
**require UniProt, log the miss, accept the tiny edge loss** (same "abort/report,
never guess" discipline as ADR-0003). DoRothEA TFs are well-characterized, so this
is expected to be ~0.

## Consequences

- **ETL** — new `etl/05_proteins.py`: reads the DoRothEA TF list, mints a
  `Protein` per TF (UniProt from HGNC), sets `entity_kind`/`subtype`/`hgnc_symbol`,
  and adds `TRANSLATES_TO` (from `metadata.SwissProt`) or `ENCODES` (fallback).
  `etl/00_download.sh` adds the SwissProt metadata file; `etl/utils/id_mapper.py`
  gains a symbol/ENSG → UniProt lookup. Load order becomes
  HGNC → GENCODE → GTEx → **proteins** → DoRothEA.
- **`etl/04_dorothea.py`** — the `REGULATES` source changes from
  `MATCH (s:Gene {hgnc_symbol: row.tf})` to `MATCH (s:Protein {hgnc_symbol: row.tf})`;
  must run after proteins are minted.
- **Text2Cypher prompt** (`backend/llm/prompts/text2cypher.py`, and the skeleton in
  [vision-and-mvp.md](../vision-and-mvp.md)) — schema + every example must change `(:Gene)-[:REGULATES]->(:Gene)`
  to `(:Protein)-[:REGULATES]->(:Gene)`, add the `Protein` node and
  `TRANSLATES_TO`/`ENCODES` edges. Without this the LLM emits wrong Cypher.
- **Citation agent** (`backend/agents/citation_agent.py:53`) — the uncited-edge
  match `(s:Gene)-[r:REGULATES]->(t:Gene)` becomes `(s:Protein)-[r:REGULATES]->(t:Gene)`;
  the PubMed search uses the protein's `hgnc_symbol`, so symbol-level search is
  unaffected. Update the safety-rule wording in AGENTS.md accordingly.
- **`is_tf` derivation breaks SILENTLY** — it is computed as
  `count outgoing (g)-[:REGULATES]->(:Gene) > 0` in **three** places:
  `search_genes` (`backend/db/queries/graph.py`, the search-dropdown TF badge),
  `get_gene_by_symbol` and `_fetch_subgraph` (`backend/db/queries/genes.py`).
  Post-remodel a `:Gene` has **no** outgoing `REGULATES` (it starts at the
  `:Protein`), so `count = 0` everywhere → **every gene reports `is_tf=false`** and
  TF badges disappear (no error, just wrong). All three must re-route through the
  protein, e.g. `EXISTS { (g)-[:ENCODES|TRANSLATES_TO]->(:Protein)-[:REGULATES]->() }`.
  **Redefine the flag:** `is_tf` on a *gene* now means "encodes a TF protein"; the
  authoritative TF marker is `subtype='transcription_factor'` on the **protein**
  node. (This also settles which node carries the badge under double
  representation: the protein is the TF; the gene merely encodes one.)
- **Tests move with the schema** — `backend/tests/test_text2cypher.py` (benchmark
  Cypher now Protein-sourced) and `test_agents.py` (citation match label) update
  alongside the queries.
- **Backend queries / models** — `_fetch_subgraph` and neighborhood queries gain
  the proteomics direction; a `Protein` API model + `entity_kind` field are added.
- **Frontend** — three layers, not two: layer toggle, legend, and Z-layout gain a
  proteomics plane; protein subtype gets its own color/shape. Exact palette, Z
  coordinates, and shapes are owned by the UI restyle (task #1) and layout (task
  #5) decisions — not fixed here.

## Alternatives considered

- **Visual-only relocation** (render `is_tf` genes on a third plane, no schema
  change): rejected — the layer would be a cosmetic lie, the edge would stay
  gene→gene, and it gives no structure to grow protein sub-typing into.
- **Full proteome now** (mint all protein-coding genes): rejected for MVP — large
  ETL + node count for no demo benefit; the TF slice is the part the regulatory
  story needs.
