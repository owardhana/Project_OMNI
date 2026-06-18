# ADR 0007 — Disease as first-class traversable nodes in a phenotype layer

Status: Accepted (2026-06-16)

## Context

GWAS Catalog variant-trait associations can be modelled two ways:

**Option A — edge attribute:**
```cypher
(:Variant)-[:ASSOCIATED_WITH {trait: "Type 2 Diabetes", p_value: 3e-10}]->(:Gene)
```
Disease is a string property on the edge. Simple schema, no new node type.

**Option B — first-class node:**
```cypher
(:Variant)-[:ASSOCIATED_WITH {p_value: 3e-10}]->(:Disease {name: "Type 2 Diabetes", ontology_id: "EFO_0001360"})
```
Disease is a traversable node with a stable machine ID.

The central question: do disease *traversal* patterns matter — e.g. "all genes
associated with metabolic diseases", seeding a subgraph from a disease rather
than a gene?

## Decision

Disease as a first-class `(:Disease)` node with **EFO ontology ID** as canonical
key (e.g. `EFO_0001360`), forming a **phenotype layer** — a 4th layer above
proteomics in the stacked model.

Disease is also a **valid search entry point** in the UI alongside gene search,
seeding signal-decay traversal inward: Disease → Variant → Gene → Protein/Transcript.

## Reasons

1. **Traversability.** `MATCH (d:Disease)<-[:ASSOCIATED_WITH]-(v:Variant)-[:IN_GENE]->(g:Gene)`
   is the natural pattern for disease-mechanism questions. Edge attributes prevent
   this — you cannot traverse *through* a string property.
2. **Category traversal.** EFO has a disease hierarchy (e.g. "metabolic disease"
   subsumes "Type 2 Diabetes", "obesity"). First-class nodes make category queries
   possible; edge attributes do not.
3. **Signal-decay conductance.** `ASSOCIATED_WITH` conductance =
   `-log10(p_value)` normalised 0–1 against the genome-wide significance floor
   (p = 5×10⁻⁸ → ~0.4; p = 10⁻³⁰ → ~1.0). This is cleaner as an edge property
   between two nodes than as a mixed attribute on a string field.
4. **Semantic search.** Disease nodes carry `description` text and an `embedding`
   for vector search — "find diseases similar to insulin resistance" — not possible
   on an edge attribute.

## Node schema

```cypher
(:Disease {
  ontology_id: "EFO_0001360",   // canonical key — EFO ID
  name: "type 2 diabetes",       // display
  category: "metabolic disease", // EFO parent category
  omim_id: "125853",             // optional crosslink
  description: "...",            // for embedding
  embedding: [float]             // 1536-dim, populated by embedding agent
})
```

## Edge schema

```cypher
(:Variant)-[:ASSOCIATED_WITH {
  p_value: 3e-10,
  beta: 0.12,                   // optional effect size
  odds_ratio: 1.15,             // optional
  source_db: "GWAS_Catalog",
  pmids: [...]
}]->(:Disease)

(:Variant)-[:IN_GENE {
  consequence_type: "missense_variant",
  source_db: "GWAS_Catalog"
}]->(:Gene)
```

## Load scope

GWAS Catalog associations at `p < 5×10⁻⁸` (controlled by `GWAS_MIN_SIGNIFICANCE`
env var). ~30–50k unique Variant nodes; ~5–10k Disease nodes expected.

## Consequences

- New node type in schema, ETL (`etl/08_gwas.py`), `backend/api/models.py`
  (`DiseaseNode`), frontend (4th layer / phenotype plane, disease search box).
- Disease added as a valid traversal seed: `GET /api/search?q=...` must cover
  Disease names (fulltext index on `Disease.name` + `Disease.description`).
- Text2Cypher dynamic schema (ADR-0007 dependency on APOC schema generation) will
  include Disease automatically — no manual prompt update needed.
- ASSOCIATED_WITH conductance formula added to signal-decay traversal (ADR-0005 amendment).

## Alternatives considered

- **Edge attribute only:** rejected — blocks traversal, category queries, and
  semantic search on diseases.
- **OMIM IDs as canonical key:** rejected — EFO is the GWAS Catalog native
  ontology and covers a broader disease space; OMIM crosslinks are kept as an
  optional property.
