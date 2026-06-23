# OmniGraph — Current State & Roadmap

Where the build is, what's loaded, and what's deferred. For the data model see
[`data-architecture.md`](data-architecture.md); for product scope and design see
[`vision-and-mvp.md`](vision-and-mvp.md).

---

## Current graph state

Live in Neo4j Community 5.x (Docker, named volume).

| Metric | Value |
|--------|-------|
| Total nodes | ~622,800 |
| Total relationships | ~2.04M |
| Protein | **20,077** (full proteome — ADR-0010; 117 TFs keep `subtype`) |
| Variant / Transcript / Gene / Disease | ~325k / ~221k / ~42k / ~13k |
| Metabolite | 1,281 (HMDB-enriched) |
| `INTERACTS_WITH` | 101,684 (STRING @ 0.95) |
| `CATALYSES` | 24,545 (94% of metabolites connected) |
| `DIFFERENTIALLY_EXPRESSED` | 128,714 (16 TCGA cohorts) |
| `cancer_gene` flags | 752 (580 tier-1, 172 tier-2; COSMIC v104) |

---

## Done

- **Phases 1–2 (genomics → proteomics → disease):** Gene/Transcript/Protein/Variant/
  Disease nodes; REGULATES, PRODUCES, TRANSLATES_TO/ENCODES, INTERACTS_WITH, IN_GENE,
  ASSOCIATED_WITH, IMPLICATED_IN. Text2Cypher, citation + embedding agents, semantic
  search, 3D viz, Entity Browser, shortest-path.
- **Full proteome (ADR-0010):** Protein 117 → 20,077, minted directly by
  `05_proteins.py` (TRANSLATES_TO + ENCODES, TF-subtype tag, REGULATES migration).
  This connected the metabolite layer (CATALYSES 8 → 24,545). STRING re-run at the
  raised 0.95 threshold (INTERACTS_WITH 642 → 101,684).
- **Cancer / differential expression:** COSMIC CGC v104 flags; TCGA matched
  tumour-vs-adjacent-normal log2FC DE edges.
- **Metabolomics (ADR-0009):** Recon3D `.mat` (scipy) → 1,281 Metabolite nodes +
  CATALYSES; HMDB streamed for canonical names. Layer-Z shift (Disease 900→1200,
  metabolomics plane at 900). Frontend metabolomics layer + colour deconfliction
  (metabolite cyan, not TF amber).
- **Backbone-guaranteed traversal (ADR-0011):** a seed's own vertical omics chain
  — including the metabolites its protein catalyses — is guaranteed present via a
  pre-pass; metabolites are terminal leaves (no cofactor flood). This resolved the
  former "gene seeds show no metabolites / few non-TF proteins" gap. Verified:
  LDHA → 15 metabolites (was 0); TP53 → 0 (correct, no metabolic backbone);
  metabolite-seeded views unchanged.

### Verification notes
- Layer-Z: `METABOLITE_LAYER_Z=900`, `DISEASE_LAYER_Z=1200` (constants, audited;
  no stray hardcoded `900`).
- `is_tf` derivation requires `subtype='transcription_factor'` post-full-proteome
  (else every protein-coding gene flags); fixed in genes/graph/traversal queries.
- pytest runs against live Neo4j; module import through the iCloud-synced project
  dir is pathologically slow, so data gates are also confirmed via direct Cypher.

---

## Deferred / optional

- **`06_uniprot_enrich`** for the ~20k new proteins' function text + embeddings
  (~5h REST crawl). Not needed for topology; enables semantic protein search over
  the full proteome.
- **Variant-level gnomAD allele frequency** (`Variant.gnomad_af`) — gnomAD ETL
  currently covers gene-level pLI only.
- **GTEx tissue panel expansion**, **co-expression networks**
  (`CO_EXPRESSED_WITH`, needs TCGA+GTEx counts in one pipeline), **cell-type
  resolution** (indefinitely deferred — data too noisy vs tissue level).
- **Literature extraction agent** — new-edge proposals; separate design session
  (NLP pipeline + validation queue).
- **Metabolite "bridge" connectivity** — let metabolites expand to co-catalysing
  proteins (shared-substrate links), with CATALYSES dense-capping + a
  cofactor-exclusion list. Deferred in favour of the simpler leaf rule (ADR-0011);
  revisit if shared-substrate exploration becomes a real use case.
- **Horizontal metabolite reach-through for pure-TF seeds** — surfacing
  metabolites that belong to a TF's regulated genes. Explicitly rejected as the
  current floor (semantically muddier; ADR-0011 "Rejected alternatives"); can be
  added later as an opt-in pass.

---

## Gated — ENCODE / cCREs (Phase 9)

ENCODE regulatory elements (`cCRE` nodes, `BINDS` Protein→cCRE, `REGULATES_VIA`
cCRE→Gene) are **intentionally not started.** The 1.7M cCRE nodes OOM on Neo4j
Community Edition; `15_encode.py` is hard-gated and refuses to start unless >500k
nodes are present (indicating an AuraDB migration has occurred) or
`ENCODE_FORCE_LOAD=true` is set. **Never force-load on Community.**

**Migration triggers to AuraDB Professional (~$65/month):** node count
materially exceeds Community headroom *for cCRE volume specifically* / pagecache
miss rate > 30% / production reliability or multi-user RBAC required. The current
~622k total nodes is above the 500k figure, but that figure is an ENCODE-cCRE
volume gate, not a hard node cap — Phase 9 stays user-driven.

Other infra triggers: >500 ms ANN vector-search latency → revisit the native
vector index (ADR-0008); ≥3 agents needing independent schedules → add a
Prefect/Dagster orchestrator (deferred until then).
