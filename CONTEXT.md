# OmniGraph

Domain language for OmniGraph — a tissue-segmented, multi-omics knowledge graph
of human molecular biology. This file is a glossary, not a spec: it defines what
each term **is**, not how it is implemented.

## Language

### Entities

**Entity kind**:
The omics class of a node — one of `gene`, `transcript`, `protein`, `variant`, or
`disease`. The single source of truth for what a node *is*.

**Gene**:
A genomic locus that can be transcribed. Lives in the **genomics layer**. Machine
ID = Ensembl gene ID (ENSG). Display name = HGNC symbol (e.g. `TP53`).
_Avoid_: locus, ORF (too narrow).

**Transcript**:
An RNA isoform produced from a gene. Lives in the **transcriptomics layer**.
Machine ID = Ensembl transcript ID (ENST). Display name = symbol + isoform number
(e.g. `TP53-201`).
_Avoid_: mRNA (excludes non-coding), isoform (use for the biological concept, not the node).

**Protein**:
A polypeptide translated from a transcript. Lives in the **proteomics layer**.
Machine ID = UniProt accession (e.g. `P04637`). Display name = symbol + kind tag
(e.g. `TP53 (protein)`). Phase 2: full proteome (~20k proteins), not just the TF
slice.

**Transcription factor (TF)**:
A **protein subtype** — a protein that regulates the expression of genes. NOT its
own node kind; a TF is a `protein` whose subtype is `transcription_factor`. It is
the source of `REGULATES` edges.
_Avoid_: regulator (too broad), "TF node" / "TF layer" (there is no separate TF
node kind or layer — TFs live in the proteomics layer).

**Protein subtype**:
A finer classification of a protein. Distinguished visually by **color**, not by
layer. Subtypes: `transcription_factor` (regulatory DNA binding), `kinase`
(phosphorylation), `enzyme` (catalysis), `structural` (scaffolding). Annotated
from UniProt. Only `transcription_factor` existed in the MVP; full subtype
annotation is a phase-2 addition.

**Variant**:
A genomic variant (SNP or indel) at sub-gene resolution. Lives in the **genomics
layer** as a distinct node kind (different shape/color from Gene). Machine ID =
rsid (e.g. `rs7903146`); fallback = `chr:pos:ref:alt` (GRCh38) for variants
without rsids — same primary + fallback pattern as `TRANSLATES_TO`/`ENCODES`.
Source: GWAS Catalog (disease associations, p < 5×10⁻⁸) and ClinVar (clinical
significance).
_Avoid_: mutation (implies pathogenicity), polymorphism (too narrow).

**Disease**:
A human disease or phenotypic trait. Lives in the **phenotype layer**. Machine ID
= EFO ontology ID (e.g. `EFO_0001360`). A first-class traversable node — not an
edge attribute. A Disease node is a valid traversal seed alongside Gene.
Source: GWAS Catalog, EFO ontology.
_Avoid_: "disease attribute", "trait string" — Disease is always a node.

**Metabolite**:
A small-molecule substrate or product of an enzymatic reaction. Lives in the
**metabolomics layer** (Layer 4, between proteomics and phenotype). Machine ID =
HMDB ID (primary, e.g. `HMDB0000122` for glucose); fallback = ChEBI ID
(e.g. `CHEBI:4167`) for metabolites not in HMDB. A first-class traversable node.
Source: Recon3D human metabolic reconstruction (SBML).
_Avoid_: "compound", "small molecule" — use metabolite in domain language.

**Embedding**:
A 1536-dimensional float array stored as a property on Gene, Protein, and Disease
nodes, encoding their `summary_text` for semantic search. Populated by the
embedding agent using `text-embedding-3-small` via OpenRouter. Not stored on
Transcript, Variant, or Metabolite nodes (no meaningful free text). Queried via
Neo4j native vector index (`db.index.vector`), composable with graph traversal.
_Avoid_: "vector" as a synonym in domain language — use embedding.

### Layers

**Layer** (omics layer):
A horizontal plane in the stacked model. Bottom to top:
**genomics → transcriptomics → proteomics → metabolomics → phenotype**.
A node belongs to exactly one layer, fixed by its **entity kind**.

**Genomics layer**: holds **gene**, **variant**, and **cCRE** nodes.
**Transcriptomics layer**: holds **transcript** nodes.
**Proteomics layer**: holds **protein** nodes (all subtypes, including TFs).
**Metabolomics layer**: holds **metabolite** nodes. Layer 4, above proteomics.
  Phase 3 addition (ADR-0009). Orange colour (#fb923c).
**Phenotype layer**: holds **disease** nodes. Layer 5, above metabolomics.
  (Was Layer 4 before Metabolomics was added.)

### Relationships

**Regulates**:
A **protein** (transcription-factor subtype) acting on a **gene** to activate or
repress its expression. Directed, runs *downward* proteomics → genomics. The
biology of TF→DNA binding. Source: DoRothEA (confidence tiers A–B).
_Avoid_: "gene regulates gene" — the regulator is the TF protein, not its gene.

**Produces**:
A **gene** giving rise to a **transcript**. Directed, genomics → transcriptomics.
Carries tissue context as flat `tw_<tissue>` float properties (ADR-0001).

**Translates to**:
A **transcript** giving rise to a **protein**. Directed, transcriptomics →
proteomics. Primary link — preferred when the canonical transcript is in the graph.

**Encodes**:
A **gene** giving rise to a **protein**, directed genomics → proteomics. Fallback
link used only when the protein's canonical transcript is absent.

**Interacts with**:
A physical protein-protein interaction between two **protein** nodes. Intra-layer,
within proteomics. Source: STRING v12, filtered at `combined_score > 0.9` (~50k
edges). Conductance in signal-decay traversal = STRING `combined_score`. Expansion
per traversal frontier step is capped at top-k by `combined_score` (default k=10)
to prevent hub-protein explosion.
_Avoid_: "PPI" as a relationship label — the label is `INTERACTS_WITH`.

**In gene**:
Structural mapping of a **variant** to its host **gene** locus. Directed, variant
→ gene. Consequence type (e.g. `missense_variant`) from GWAS Catalog / Ensembl VEP.

**Associated with**:
A **variant** linked to a **disease** via a GWAS association or ClinVar clinical
classification. Directed, variant → disease. Carries `p_value`, `beta`,
`odds_ratio`, `source_db`. Conductance in signal-decay traversal =
`-log10(p_value)` normalised 0–1 against genome-wide significance floor (p=5×10⁻⁸).

**Implicated in**:
A rolled-up **gene**-to-**disease** association (aggregated from GWAS Catalog
variant-level hits). Directed, gene → disease. A convenience edge for direct
gene-disease queries without traversing through individual variants.

### Provenance & trust

**Provenance tier**:
The trust class of a node or edge — `canonical` (from a curated consortium source:
GTEx, STRING, UniProt, Recon3D, GWAS Catalog, ClinVar, COSMIC, TCGA) or `literature`
(proposed by the literature-extraction agent from a paper). Canonical is consortium
truth; literature is machine-proposed and carries less traversal signal. Always
distinguishable — never collapse the two.
_Avoid_: treating a literature-proposed edge as equivalent to consortium data.

**Candidate relationship**:
A relationship the literature-extraction agent has *proposed* from a paper but which
is **not** part of the trusted graph. It is staged separately with its supporting
evidence (PMID + sentence) and a confidence, and touches no biological topology until
**promotion**. A candidate is never a traversable edge.
_Avoid_: "extracted edge" (implies it is already in the graph — it is not).

**Promotion**:
The gated act of turning a candidate relationship into a real, traversable edge —
via human review or a calibrated auto-promote policy. A promoted edge is permanently
tagged `provenance_tier = literature`. See
[ADR-0013](docs/adr/0013-literature-extraction-trust-model.md).
_Avoid_: "merge" / "accept" as synonyms for the gated step — promotion is deliberate.

### Identity & disambiguation

The same molecule appears once per layer (gene `TP53`, transcript `TP53-201`,
protein `TP53 (protein)`), all derived from each other. Kept distinct by:

1. **Machine ID** — layer-specific and collision-free: ENSG (gene), ENST
   (transcript), UniProt (protein), rsid (variant), EFO ID (disease).
2. **Display name** — symbol plus a kind cue, shown on every surface.
3. **Visual channels** in 3D — **layer** (Z position) + **color** (subtype) +
   **shape** (per node kind). Redundant on purpose.

## Flagged ambiguities

- **"TP53"** alone is ambiguous — it names a gene, its transcripts, and its
  protein. Always qualify by **entity kind**.
- **"TF"** is not a node kind and not a layer. It is a **protein subtype** living
  in the proteomics layer.
- **"Regulation"** is protein→gene, never gene→gene.
- **"Disease"** is always a first-class node with an EFO ontology ID, never an
  edge attribute or a free-text string on a relationship.
- **"Variant"** lives in the genomics layer alongside genes — it is a distinct
  node kind, not a property on a Gene node.
- **Traversal terms** ("signal", "conductance", "decay", "signal floor") are
  *algorithm* vocabulary — see [ADR-0005](docs/adr/0005-signal-decay-traversal.md).
  Deliberately kept out of this glossary.

## Example dialogue

> **Dev:** When I query "Type 2 Diabetes", what do I get?
> **Bio:** You seed the traversal from a **Disease** node (`EFO_0001360`) in the
> phenotype layer. Signal decays inward: Disease → Variant (via `ASSOCIATED_WITH`)
> → Gene (via `IN_GENE`) → Protein, Transcripts, and STRING interactors. Stronger
> GWAS hits (lower p-value) carry more signal and reach further.
> **Dev:** Why is Disease a node and not just an edge attribute?
> **Bio:** Because you need to traverse *through* diseases — "find all genes
> associated with metabolic diseases" traverses from a Disease category to its
> children to their variants to their genes. An edge attribute can't be traversed.
> **Dev:** When I query TP53, why do I see it twice?
> **Bio:** Because you're seeing two entities. The **gene** TP53 in the genomics
> layer — that's the locus other TFs regulate. And the **protein** `TP53 (protein)`
> in the proteomics layer — that's the transcription factor regulating *other*
> genes. The vertical **ENCODES** edge between them says "same molecule."
