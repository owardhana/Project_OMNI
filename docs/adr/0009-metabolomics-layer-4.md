# ADR 0009 — Metabolomics as Layer 4: Metabolite nodes between proteomics and phenotype

Status: Accepted (2026-06-22)

## Context

Phase 3 introduces metabolites — the small-molecule substrates and products of
enzymatic reactions — from the Recon3D human metabolic reconstruction (SBML),
together with `CATALYSES` edges from enzyme proteins to the metabolites they act
on. The graph must place these nodes somewhere in the stacked omics model, and
there are two coherent options:

**Option A — fold metabolites into the proteomics layer.**
Metabolites share the proteomics plane with proteins. No new layer; Disease stays
at Layer 4.

**Option B — a new metabolomics layer (Layer 4), shifting Disease to Layer 5.**
Metabolites occupy their own plane between proteomics and phenotype.

The central question: does the extended central dogma —
`gene → RNA → protein → metabolite → phenotype` — warrant a distinct plane, or are
metabolites close enough to proteins to share one?

## Decision

**Option B.** Metabolites become a first-class `(:Metabolite)` node forming a new
**metabolomics layer** — Layer 4, between proteomics (Layer 3) and phenotype.
Disease shifts up to **Layer 5**.

Metabolite is also a **valid traversal seed** alongside Gene, Protein, and Disease
(`GET /api/metabolite/{hmdb_id}/graph`), consistent with the first-class-node
precedent set for Disease in [ADR-0007](0007-disease-as-first-class-nodes.md).

## Reasons

1. **Biological directionality.** The extended central dogma is
   `gene → RNA → protein → metabolite → phenotype`. Metabolites are *downstream*
   of the proteins (enzymes) that produce them but *upstream* of observable
   phenotype. A dedicated plane preserves traversal directionality — signal flows
   proteomics → metabolomics → (eventually) phenotype — exactly as the lower
   layers already read bottom-to-top.
2. **Visual clarity.** Putting metabolites in the proteomics plane would overlay
   `CATALYSES` edges on top of the existing intra-layer `INTERACTS_WITH` (STRING
   PPI) tangle on the *same* plane, producing a visual mess. A separate plane
   keeps enzymatic links legible and visually distinct from protein-protein
   interactions.
3. **Layer = entity kind (invariant).** The stacked model's core rule is that a
   node belongs to exactly one layer, fixed by its entity kind (CONTEXT.md). A new
   entity kind (`metabolite`) therefore gets a new layer rather than being
   crammed into an existing one — the same reasoning that gave Disease its own
   phenotype layer.

## Machine ID

Metabolite canonical key follows the **primary + fallback** pattern already used
for Protein (UniProt primary) and Variant (rsid primary, `chr:pos:ref:alt`
fallback):

- **Primary:** HMDB ID — e.g. `HMDB0000122` (glucose).
- **Fallback:** ChEBI ID — e.g. `CHEBI:4167` — for metabolites not resolvable to
  HMDB but present in Recon3D's ChEBI annotations.

A metabolite with neither HMDB nor ChEBI identifier is discarded (it cannot be
given a stable, collision-free key).

## Node schema

```cypher
(:Metabolite {
  hmdb_id: "HMDB0000122",   // canonical key (primary)
  chebi_id: "CHEBI:4167",   // fallback key / crosslink
  name: "D-Glucose",         // display
  formula: "C6H12O6",        // molecular formula
  charge: 0,                  // net charge (optional)
  node_type: "metabolite",
  layer_z: 900,              // metabolomics plane (backend metadata)
  source_db: "Recon3D",
  source_version: "3.04"
})
```

No `embedding` is stored on Metabolite nodes — like Transcript and Variant, they
carry no meaningful free text for semantic search (CONTEXT.md).

## Edge schema

```cypher
(:Protein)-[:CATALYSES {
  role: "substrate",        // "substrate" (reactant) | "product"
  reaction_id: "RXN_...",   // Recon3D reaction ID
  source_db: "Recon3D",
  source_version: "3.04"
}]->(:Metabolite)
```

`CATALYSES` conductance in signal-decay traversal (ADR-0005 amendment) is the
structural-enzymatic constant **0.7** — a moderately confident structural link,
weaker than the `~1.0` vertical backbone (ENCODES/TRANSLATES_TO) but stronger than
a noisy association. `CATALYSES` is **not** dense-capped: most proteins catalyse
only 1–5 reactions, so there is no hub-explosion risk (unlike INTERACTS_WITH).

## Layer-Z bookkeeping

The Z constant shift is the regression vector of this ADR. Backend
`backend/api/models.py`:

| Layer | Entity kind | `layer_z` | Change |
|-------|-------------|-----------|--------|
| 0 Genomics | gene, variant, cCRE | 0 | — |
| 1 Transcriptomics | transcript | 300 | — |
| 2 Proteomics | protein | 600 | — |
| 3 Metabolomics | metabolite | **900** | NEW |
| 4 Phenotype | disease | **1200** | shifted from 900 |

Frontend `frontend/src/styles/layers.ts` world-Y mapping shifts in parallel:
new `metabolomics` plane at **Y = 600**, `phenotype` plane shifts **600 → 900**.

Existing Disease nodes in Neo4j do **not** need a stored-property update —
`layer_z` is API metadata derived from entity kind, never persisted on the graph
node. The shift lives entirely in the model constants and the frontend layer map.

## Color

Metabolite nodes render **orange (`#fb923c`)** — distinct from every existing node
hue (gene green, transcript blue, protein violet, TF amber, variant teal, disease
pink). `CATALYSES` edges also render orange; `DIFFERENTIALLY_EXPRESSED` (the other
Phase-3 edge) renders amber (`#f59e0b`) to read as a cancer-context signal.

## Consequences

- New entity kind in CONTEXT.md (done), schema, ETL (`etl/14_metabolomics.py`),
  `backend/api/models.py` (`MetaboliteNode` + `METABOLITE_LAYER_Z = 900`,
  `DISEASE_LAYER_Z = 1200`), and frontend (5th plane, metabolite rendering,
  Metabolite entity-browser tab).
- Metabolite added as a valid traversal seed and to the `node_search` fulltext
  index (over `name` + `formula`).
- `CATALYSES` conductance added to signal-decay traversal (ADR-0005 amendment).
- Any hardcoded `900` (Disease Z) not routed through the constant becomes a silent
  bug — audited via `grep -rn "= 900" frontend/src/ backend/` before Phase 7/8 land.

## Alternatives considered

- **Metabolites in the proteomics plane (Option A):** rejected — overlays
  `CATALYSES` on the `INTERACTS_WITH` tangle and breaks the
  protein → metabolite directional reading of the stacked model.
- **KEGG compound ID as canonical key:** rejected — HMDB has broader human
  metabolite coverage and is Recon3D's richest annotation; KEGG/ChEBI crosslinks
  are kept as properties, ChEBI as the documented fallback key.
</content>
