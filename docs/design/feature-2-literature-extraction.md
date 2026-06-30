# Design brainstorm — Literature Extraction Agent (Feature 2)

Status: **Brainstorm / not started.** This is a design exploration, not an accepted
ADR. It fleshes out the roadmap's deferred "Literature extraction agent — new-edge
proposals; separate design session (NLP pipeline + validation queue)" item. No code
exists yet. Decisions here are provisional until an ADR is written.

## Goal

A scheduled agent that reads biomedical papers (new NCBI/PubMed uploads + a one-time
backfill of past literature), determines whether any node↔node relationship in
OmniGraph's vocabulary is supported, extracts it with provenance, and **proposes** it
for inclusion — and likewise enriches existing nodes/edges. After extraction the paper
text is discarded (only PMID + the supporting span is kept), so storage stays bounded.

## The hard part is trust, not plumbing

OmniGraph's entire credibility rests on one rule: **agents never hallucinate biology.**
The existing `CitationAgent` only attaches PMIDs to *existing* edges; the `EmbeddingAgent`
only writes vectors. Neither invents topology. A literature extractor *does* propose new
topology, which breaks that rule unless it is firewalled. So the central design
constraint is:

> Extracted relationships are **candidates**, never trusted graph edges. They live in a
> separate staging space with provenance + confidence, and only a **promotion gate**
> (human review, or a high-confidence auto-promote policy) ever moves them into the
> consortium-grade graph.

This firewall is non-negotiable. Without it, one bad extraction poisons the trusted data
from GTEx/STRING/UniProt/Recon3D/GWAS.

## Pipeline

1. **Ingest.** PubMed abstracts are free via NCBI E-utilities (the `CitationAgent`
   already uses `esearch`/`efetch` — reuse that client). Nightly = `reldate` delta;
   backfill = batched historical pull. Full text only where available via the PMC
   Open-Access subset (most papers are abstract-only — design for abstracts first).
2. **NER.** Detect gene/protein/disease/metabolite/variant mentions. Cheap/local model
   or a dictionary+scispaCy pass — this is the high-volume filter, so keep it cheap.
3. **Entity linking (the hardest, most error-prone step).** Map each mention to a
   canonical graph id: HGNC symbol → ENSG, protein name → UniProt, trait → EFO,
   metabolite → HMDB/ChEBI, variant → rsid. Needs a dictionary (HGNC symbols + aliases,
   UniProt names, EFO labels, HMDB names) and disambiguation ("PCA" = gene? method?
   cell line?). Unresolved mentions are dropped, not guessed.
4. **Relation extraction.** An LLM proposes `(subject_id, REL_TYPE, object_id)` from the
   sentence, **restricted to OmniGraph's edge vocabulary** (REGULATES, INTERACTS_WITH,
   ASSOCIATED_WITH, IMPLICATED_IN, CATALYSES, DIFFERENTIALLY_EXPRESSED, …). Must detect
   **negation/hedging** ("X does *not* regulate Y", "may be associated") and down-weight
   or drop accordingly.
5. **Stage — never merge.** Write `(:CandidateEdge {rel_type, subject_id, object_id,
   confidence, pmid, sentence_span, model, extracted_at, status:'pending'})`. Dedup
   against existing trusted edges (if the edge already exists → instead attach the PMID
   as *enrichment*, the CitationAgent's job, not a new edge). Multi-paper agreement
   raises confidence (N independent PMIDs for the same triple → stronger).
6. **Promotion gate.** A review surface (admin UI queue) or an auto-promote policy above
   a confidence threshold **with** ≥N independent papers. On promote: create the real
   edge with `source_db='literature_extracted'`, `pmids=[…]`, provenance. On reject:
   keep the candidate flagged so it isn't re-proposed.
7. **Discard paper.** Retain PMID + extracted sentence span as provenance; drop full
   text. Storage stays bounded.

## Cost / compute (ties into the cloud-migration plan)

High volume makes this the expensive feature (millions of backfill papers + nightly).
- **Tier aggressively:** cheap/local model for NER + a first relation pass on the 99%;
  an expensive API model only on candidate sentences that survive the filter.
- **No free GPU on Oracle A1** (ARM CPU only). Options: a quantized local LLM on the A1
  CPU (llama.cpp; fine for *nightly batch* throughput, free) for bulk NER, and tiered
  API (cheap model) for disambiguation/relation extraction. Backfill = throttled batch
  over days/weeks, or a one-time budgeted API spend.
- See [`docs/design/cloud-migration.md`](cloud-migration.md) for the host/compute side.

## Schema sketch

```
(:CandidateEdge {
   id, rel_type, subject_id, subject_label, object_id, object_label,
   confidence, status: 'pending'|'promoted'|'rejected',
   pmids: [..], sentence_span, model, agent_version, extracted_at
})
```
Kept entirely separate from trusted edges. A promoted candidate becomes a normal typed
edge with `source_db='literature_extracted'`; the CandidateEdge is marked `promoted`.

## Risks / open questions

- **Entity-linking errors propagate into wrong edges** → strict dictionary + confidence
  floor + the promotion gate.
- **Spurious / contradicted relations** → multi-paper agreement, negation handling,
  human review for low-confidence.
- **Backfill cost/scale** → tiering + local CPU model + throttling.
- **Edge vocabulary fit** — not every literature relationship maps cleanly to the 9 edge
  types; out-of-vocabulary relations are dropped (don't invent new edge types ad hoc).
- **Review throughput** — a human gate doesn't scale to millions; need a calibrated
  auto-promote threshold measured against a hand-labelled sample first.

## Suggested phasing

1. **P1 — extraction-to-staging only.** Nightly delta → CandidateEdge nodes, no
   promotion. Build the dictionary + NER + relation extractor; measure precision on a
   labelled sample. (Two ADRs: the pipeline, and the staging/trust model.)
2. **P2 — promotion gate.** Admin review queue + auto-promote policy once precision is
   known. Enrichment path (attach PMIDs to existing edges) lands here too.
3. **P3 — backfill.** Throttled historical pull once P1/P2 are calibrated and cheap.

Not for this session — captured so the design is ready when prioritised.
