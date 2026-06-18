# OmniGraph — Phase 2 Build Prompt

> Copy everything between the triple-backtick fences into a new Claude Code session
> opened at `/Users/oliverwardhana/Desktop/Project_OMNI` on the
> `phase-2-multiomics-expansion` branch, then run `/loop`.

```
/loop

You are extending OmniGraph — a multi-omics knowledge graph of human biology.
The MVP (Phase 1) is complete on the `main` branch. You are on
`phase-2-multiomics-expansion`. Do NOT touch the MVP codebase unless a phase
explicitly requires it.

Read these files IN FULL before writing any code:
- CONTEXT.md          ← domain glossary (includes new Phase 2 terms)
- 01_vision.md        ← layered model + node/edge status table
- 06_data_vision.md   ← data engineering map: node schemas, sources, ETL patterns
- 04_decisions.md     ← all finalized decisions (Phase 2 section at the bottom)
- AGENTS.md           ← agent safety rules (never violated)
- docs/adr/0007-disease-as-first-class-nodes.md
- docs/adr/0008-neo4j-native-vector-indexing.md
- docs/adr/0005-signal-decay-traversal.md
- docs/adr/0001-tissue-weights-flat-properties.md

04_decisions.md is ground truth for all tech choices. When in doubt, check it first.

---

KEY CONTEXT (details in the files above):

Phase 2 adds three biological capabilities the MVP cannot answer:
1. Protein signalling chains — via full proteome + STRING protein-protein interactions
2. Disease mechanisms — via Variant + Disease nodes linked by GWAS/ClinVar associations
3. Semantic node search — via Neo4j native vector embeddings on Gene, Protein, Disease

New node types: Variant (genomics layer), Disease (phenotype layer — 4th layer)
New edge types: INTERACTS_WITH, IN_GENE, ASSOCIATED_WITH, IMPLICATED_IN
Full proteome: ~20k proteins (was TF-only ~1,500)
New agents: EmbeddingAgent (runs nightly alongside CitationAgent)
Text2Cypher: dynamic schema block from apoc.meta.schema() — no longer hardcoded

Tunable env vars (all have defaults in .env.example):
  STRING_MIN_CONFIDENCE=0.9        # STRING edge threshold (~50k edges at 0.9)
  STRING_MAX_EXPAND_PER_NODE=10    # hub-protein traversal cap
  GWAS_MIN_SIGNIFICANCE=5e-8       # genome-wide significance
  EMBEDDING_AGENT_BATCH_SIZE=50
  EMBEDDING_AGENT_CRON_HOUR=1

ETL patterns (non-negotiable):
  Topology (new nodes/edges) → bulk file download via 00_download.sh + local pandas parse
  Enrichment (add properties to existing nodes) → REST API calls, batched, rate-limited
  Never call an API to discover which nodes to create.

---

Work through phases in exact order. Complete each phase fully — all scripts
working, all verification checks passing — before moving to the next.
After each phase: run /code-review high on all files written in that phase,
fix every finding, then proceed.

---

PHASE 1 — Infrastructure & memory

Files to create/modify:
- docker-compose.yml
    Increase Neo4j memory for Phase 2 data volume:
      NEO4J_server_memory_heap_initial__size: "2G"
      NEO4J_server_memory_heap_max__size: "4G"
      NEO4J_server_memory_pagecache_size: "4G"
    No other changes to docker-compose.yml.

- .env.example
    Add all Phase 2 env vars (see 04_decisions.md environment section):
      EMBEDDING_MODEL, STRING_MIN_CONFIDENCE, STRING_MAX_EXPAND_PER_NODE,
      GWAS_MIN_SIGNIFICANCE, EMBEDDING_AGENT_BATCH_SIZE, EMBEDDING_AGENT_CRON_HOUR

- backend/db/neo4j_client.py
    Add to INDEX_STATEMENTS:
    1. B-tree indexes for new node types:
       CREATE INDEX protein_uniprot_idx IF NOT EXISTS FOR (n:Protein) ON (n.uniprot_id)
       CREATE INDEX protein_symbol_idx IF NOT EXISTS FOR (n:Protein) ON (n.hgnc_symbol)
       CREATE INDEX variant_rsid_idx IF NOT EXISTS FOR (n:Variant) ON (n.rsid)
       CREATE INDEX disease_ontology_idx IF NOT EXISTS FOR (n:Disease) ON (n.ontology_id)
    2. Fulltext index expansion (add Protein and Disease to gene_search):
       CREATE FULLTEXT INDEX node_search IF NOT EXISTS
       FOR (n:Gene|Transcript|Protein|Disease) ON EACH [n.hgnc_symbol, n.description, n.summary_text, n.name]
       (rename from gene_search to node_search — update all references)
    3. Vector indexes (Neo4j 5.11+ native syntax):
       CREATE VECTOR INDEX gene_embeddings IF NOT EXISTS
       FOR (n:Gene) ON (n.embedding)
       OPTIONS {indexConfig: {`vector.dimensions`: 1536, `vector.similarity_function`: 'cosine'}}
       — repeat for protein_embeddings, disease_embeddings

- backend/config.py
    Add: EMBEDDING_MODEL, STRING_MIN_CONFIDENCE (float), STRING_MAX_EXPAND_PER_NODE (int),
    GWAS_MIN_SIGNIFICANCE (float), EMBEDDING_AGENT_BATCH_SIZE (int),
    EMBEDDING_AGENT_CRON_HOUR (int)

After Phase 1:
- docker compose up neo4j -d (restart with new memory settings)
- Verify in Neo4j browser: SHOW INDEXES — all new indexes present with ONLINE status.

---

PHASE 2 — ETL data downloads

File to modify:
- etl/00_download.sh
    Add these sources (same curl + skip-if-present pattern as existing entries):
    STRING v12 human:
      "9606.protein.links.detailed.v12.0.txt.gz|https://stringdb-downloads.org/download/protein.links.detailed.v12.0/9606.protein.links.detailed.v12.0.txt.gz"
    GWAS Catalog full associations:
      "gwas_catalog_associations.tsv|https://www.ebi.ac.uk/gwas/api/search/downloads/full"
    ClinVar variant summary:
      "ClinVarVariantSummary.txt.gz|https://ftp.ncbi.nlm.nih.gov/pub/clinvar/tab_delimited/variant_summary.txt.gz"
    gnomAD gene constraint:
      "gnomad_v4_constraint.tsv|https://storage.googleapis.com/gcp-public-data--gnomad/release/4.1/constraint/gnomad.v4.1.constraint_metrics.tsv"
    EFO ontology:
      "efo.json|https://github.com/EBISPOT/efo/releases/latest/download/efo.json"

Run etl/00_download.sh and verify all files downloaded to data/raw/.

---

PHASE 3 — Full proteome ETL

File to create:
- etl/06_uniprot_enrich.py
    Enrich existing Protein nodes (TF slice already loaded) with UniProt data.
    Pattern: REST API per entity (enrichment pattern — reads nodes from graph, calls API).

    Steps:
    1. Query Neo4j: MATCH (p:Protein) WHERE p.summary_text IS NULL RETURN p.uniprot_id
    2. For each uniprot_id (batch 50 at a time):
       GET https://rest.uniprot.org/uniprotkb/{accession}.json
       Headers: Accept: application/json
       Extract:
         - function text: result['comments'] where commentType == 'FUNCTION', first value's texts[0].value
         - subcellular_loc: result['comments'] where commentType == 'SUBCELLULAR LOCATION', first value
         - go_terms: result['uniProtKBCrossReferences'] where database == 'GO', extract id + properties.GoTerm
         - molecular_weight: result['sequence']['molWeight']
         - subtype: derive from go_terms (GO:0003700 → 'transcription_factor', GO:0016301 → 'kinase', etc.)
    3. MERGE updates back:
       MATCH (p:Protein {uniprot_id: $uniprot_id})
       SET p.summary_text = $function_text,
           p.subcellular_loc = $loc,
           p.go_terms = $go_list,
           p.molecular_weight = $mw,
           p.subtype = CASE WHEN p.subtype IS NOT NULL THEN p.subtype ELSE $derived_subtype END
    Rate limit: 1 req/s without API key (UniProt free tier). Add 1s sleep between requests.
    Print: proteins enriched, proteins with no function text, time elapsed.

After Phase 3:
- Verify: MATCH (p:Protein) WHERE p.summary_text IS NOT NULL RETURN count(p) — expect >1000 for TF slice.
- Spot check: MATCH (p:Protein {hgnc_symbol:'TP53'}) RETURN p.summary_text — should be non-null.

---

PHASE 4 — STRING protein-protein interactions

File to create:
- etl/07_string.py
    Load STRING INTERACTS_WITH edges between existing Protein nodes.
    Pattern: bulk file download + local parse.

    Input: data/raw/9606.protein.links.detailed.v12.0.txt.gz
    Columns: protein1, protein2, neighborhood, fusion, cooccurence, coexpression,
             experimental, database, textmining, combined_score (0–1000 integer)

    Steps:
    1. Load file with pandas. Filter: combined_score >= int(settings.STRING_MIN_CONFIDENCE * 1000).
       (STRING stores scores as 0–1000 integers; threshold 0.9 → filter >= 900)
    2. STRING uses Ensembl protein IDs (9606.ENSP00000...). Need to map to UniProt.
       Use etl/utils/id_mapper.py — add method ensp_to_uniprot(ensp_id) using the
       GENCODE SwissProt metadata (ENST→UniProt) combined with Ensembl ENSG→ENSP mapping.
       If no mapping found: log and skip (never guess).
    3. MERGE edges between Protein nodes that exist in the graph:
       UNWIND $rows AS row
       MATCH (a:Protein {uniprot_id: row.uniprot_a})
       MATCH (b:Protein {uniprot_id: row.uniprot_b})
       MERGE (a)-[r:INTERACTS_WITH {source_db: 'STRING'}]->(b)
       SET r.combined_score = row.combined_score_normalized,
           r.experimental_score = row.experimental_normalized,
           r.coexpression_score = row.coexpression_normalized,
           r.source_version = 'v12.0'
    Batch size: 2000 rows. Print: edges created, edges merged, pairs skipped (no UniProt match).

After Phase 4:
- MATCH ()-[r:INTERACTS_WITH]->() RETURN count(r) — expect ~50k at threshold 0.9.
- MATCH (p:Protein {hgnc_symbol:'TP53'})-[r:INTERACTS_WITH]->() RETURN count(r) — expect >10.

---

PHASE 5 — GWAS Catalog: Variant + Disease nodes

File to create:
- etl/08_gwas.py
    Load Variant nodes, Disease nodes, ASSOCIATED_WITH and IN_GENE edges.
    Pattern: bulk file download + local parse.

    Input: data/raw/gwas_catalog_associations.tsv
    Key columns: SNPS (rsid), P-VALUE, CHR_ID, CHR_POS, REPORTED GENE(S),
                 MAPPED_GENE, STRONGEST SNP-RISK ALLELE, DISEASE/TRAIT,
                 MAPPED_TRAIT, MAPPED_TRAIT_URI, BETA, OR or BETA, PUBMEDID

    Steps:
    1. Filter: P-VALUE <= settings.GWAS_MIN_SIGNIFICANCE (5e-8).
    2. Parse rsid: take first rsid from SNPS column (some rows have multiple, space-separated).
       Fallback key: f"chr{CHR_ID}:{CHR_POS}:NA:NA" if no rsid.
    3. Parse Disease: MAPPED_TRAIT_URI gives EFO ID (e.g. "http://www.ebi.ac.uk/efo/EFO_0001360"
       → extract "EFO_0001360"). MAPPED_TRAIT gives display name.
    4. MERGE Disease nodes:
       UNWIND $diseases AS d
       MERGE (dis:Disease {ontology_id: d.ontology_id})
       SET dis.name = d.name, dis.description = d.name
    5. MERGE Variant nodes:
       UNWIND $variants AS v
       MERGE (var:Variant {rsid: v.rsid})
       SET var.chromosome = v.chr, var.position_grch38 = v.pos,
           var.consequence_type = 'intergenic'   ← placeholder; ClinVar enriches this
    6. MERGE ASSOCIATED_WITH edges:
       UNWIND $assocs AS a
       MATCH (var:Variant {rsid: a.rsid})
       MATCH (dis:Disease {ontology_id: a.ontology_id})
       MERGE (var)-[r:ASSOCIATED_WITH]->(dis)
       SET r.p_value = a.p_value, r.source_db = 'GWAS_Catalog', r.pmids = [a.pubmedid]
    7. MERGE IN_GENE edges (use MAPPED_GENE column → match Gene by hgnc_symbol):
       MATCH (var:Variant {rsid: a.rsid})
       MATCH (g:Gene {hgnc_symbol: a.mapped_gene})
       MERGE (var)-[:IN_GENE {consequence_type: 'intergenic', source_db: 'GWAS_Catalog'}]->(g)
    8. MERGE IMPLICATED_IN rollup edges:
       MATCH (var:Variant)-[:IN_GENE]->(g:Gene)
       MATCH (var)-[a:ASSOCIATED_WITH]->(dis:Disease)
       MERGE (g)-[:IMPLICATED_IN]->(dis)

    Print: Disease nodes created, Variant nodes created, ASSOCIATED_WITH edges, IN_GENE edges.

After Phase 5:
- MATCH (d:Disease) RETURN count(d) — expect ~5k–10k.
- MATCH (v:Variant) RETURN count(v) — expect ~30k–50k.
- MATCH ()-[r:ASSOCIATED_WITH]->() RETURN count(r) — expect ~100k–300k.
- Spot: MATCH (d:Disease {name: 'type 2 diabetes'})<-[:ASSOCIATED_WITH]-(v:Variant) RETURN count(v).

---

PHASE 6 — ClinVar + gnomAD enrichment

Files to create:
- etl/09_clinvar.py
    Enrich existing Variant nodes with clinical significance from ClinVar.
    Pattern: bulk file download + local parse.

    Input: data/raw/ClinVarVariantSummary.txt.gz
    Key columns: RS# (dbSNP ID → rsid with "rs" prefix), ClinicalSignificance,
                 PhenotypeList, Origin, Assembly (filter: GRCh38)

    Steps:
    1. Filter Assembly == 'GRCh38'. Build rsid → clinical_significance map.
    2. MATCH existing Variant nodes by rsid, SET clinical_significance.
    3. For variants with no rsid match: skip (log count).
    Print: variants enriched, not found.

- etl/11_gnomad.py
    Enrich Gene nodes with pLI score (intolerance to loss-of-function).
    Pattern: bulk file download + local parse.

    Input: data/raw/gnomad_v4_constraint.tsv
    Key columns: gene (HGNC symbol), lof.pLI

    Steps:
    1. Load TSV. Build hgnc_symbol → pLI map.
    2. MATCH Gene nodes, SET g.pli_score = pLI value.
    Print: genes enriched.

After Phase 6:
- MATCH (v:Variant) WHERE v.clinical_significance IS NOT NULL RETURN count(v).
- MATCH (g:Gene {hgnc_symbol:'BRCA1'}) RETURN g.pli_score — expect ~1.0 (high intolerance).

---

PHASE 7 — NCBI Gene summaries

File to create:
- etl/10_ncbi_summaries.py
    Enrich Gene nodes with paragraph-length functional summaries.
    Pattern: REST API per entity (enrichment pattern).

    Steps:
    1. Query Neo4j: MATCH (g:Gene) WHERE g.summary_text IS NULL RETURN g.ensembl_id, g.hgnc_id
    2. Extract entrez_id from hgnc_id (format: "HGNC:1101" → need entrez separately).
       Use HGNC file (already in data/raw/hgnc_complete_set.txt) — load entrez_id column.
       Build ensembl_id → entrez_id map from HGNC file.
    3. Batch 500 entrez_ids per E-utilities call:
       GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi
           ?db=gene&id=7157,672,...&retmode=json
       Extract: result[entrez_id]['summary'] — the paragraph description.
    4. MATCH (g:Gene {ensembl_id: $ensembl_id}) SET g.summary_text = $summary
    Rate limit: 3 req/s without NCBI key (settings.NCBI_API_KEY raises to 10/s).
    Print: genes enriched, genes with no Entrez ID, genes with no summary.

After Phase 7:
- MATCH (g:Gene) WHERE g.summary_text IS NOT NULL RETURN count(g) — expect >30k.
- MATCH (g:Gene {hgnc_symbol:'TP53'}) RETURN g.summary_text — should be a paragraph.

---

PHASE 8 — Pipeline runner

File to create:
- etl/run_pipeline.py
    Python DAG runner that runs all ETL scripts in the correct order, logging each
    step to DataSource nodes in Neo4j.

    DAG (in order):
      01_hgnc → 02_gencode → 03_gtex → 05_proteins → 04_dorothea
      → 06_uniprot_enrich → 07_string → 08_gwas → 09_clinvar
      → 10_ncbi_summaries → 11_gnomad

    For each step:
    - Print step name + start timestamp.
    - Import and call the script's main() function (don't subprocess — import directly).
    - On success: MERGE (:DataSource {name: script_name}) SET loaded_at = now(), status = 'success'.
    - On failure: print error, SET status = 'failed', continue to next step if non-blocking,
      abort if a downstream step has an explicit dependency on this one.
    - Print total elapsed time at the end.

    Blocking dependencies:
      05_proteins requires 04_dorothea (reads REGULATES edges)
      07_string requires 05_proteins (needs Protein nodes)
      08_gwas requires 01_hgnc (needs Gene nodes)
      All enrichment scripts (06, 09, 10, 11) require their topology script to have run.

    Usage: python etl/run_pipeline.py [--from 07_string]  ← optional --from flag to resume

After Phase 8:
- python etl/run_pipeline.py — run full pipeline, all steps should succeed.
- Verify final node/edge counts in Neo4j:
  MATCH (g:Gene) RETURN count(g)                → >40k
  MATCH (p:Protein) RETURN count(p)             → >1k (TF slice; grows with full proteome)
  MATCH (v:Variant) RETURN count(v)             → 30k–50k
  MATCH (d:Disease) RETURN count(d)             → 5k–10k
  MATCH ()-[r:INTERACTS_WITH]->() RETURN count(r)  → ~50k
  MATCH ()-[r:ASSOCIATED_WITH]->() RETURN count(r) → 100k–300k
  MATCH (g:Gene) WHERE g.summary_text IS NOT NULL RETURN count(g) → >30k

---

PHASE 9 — Backend: new models + API routes

Files to modify/create:

- backend/api/models.py
    Add new Pydantic models:
      VariantNode(BaseModel):
        id: str  # rsid or chr:pos:ref:alt
        rsid: Optional[str]
        chromosome: Optional[str]
        position_grch38: Optional[int]
        consequence_type: Optional[str]
        cadd_score: Optional[float]
        gnomad_af: Optional[float]
        clinical_significance: Optional[str]
        node_type: Literal["variant"] = "variant"
        layer_z: int = GENE_LAYER_Z  # variant sits in genomics layer

      DiseaseNode(BaseModel):
        id: str  # ontology_id
        ontology_id: str
        name: Optional[str]
        category: Optional[str]
        description: Optional[str]
        node_type: Literal["disease"] = "disease"
        layer_z: int = DISEASE_LAYER_Z  # = 900 (above PROTEIN_LAYER_Z=600)

    Add DISEASE_LAYER_Z = 900 constant.
    Update GraphNode union type to include VariantNode and DiseaseNode.
    Update graph_response_from_raw() to handle "variant" and "disease" node kinds.
    Update ProteinNode: add summary_text, go_terms, subcellular_loc, molecular_weight fields.

- backend/db/queries/genes.py
    Update signal-decay traversal (_fetch_subgraph) to:
    1. Include INTERACTS_WITH edges in frontier expansion.
    2. Apply per-node expansion cap for INTERACTS_WITH: when expanding a Protein node's
       INTERACTS_WITH edges, sort by combined_score DESC, take only top
       settings.STRING_MAX_EXPAND_PER_NODE neighbours.
    3. Add ASSOCIATED_WITH and IN_GENE edge types to the traversal.
    4. Conductance formula per edge type (amending ADR-0005):
       REGULATES: edge.confidence
       PRODUCES: 0.9 (structural constant)
       TRANSLATES_TO / ENCODES: 1.0
       INTERACTS_WITH: edge.combined_score
       ASSOCIATED_WITH: min(1.0, -log10(edge.p_value) / 30)  ← normalise against p=10^-30
       IN_GENE: 1.0 (structural)
       IMPLICATED_IN: 0.5 (rollup, lower weight)

- backend/db/queries/graph.py
    Update search_genes() → rename to search_nodes():
    - Use updated "node_search" fulltext index (covers Gene, Transcript, Protein, Disease)
    - Return a discriminated union (SearchResult carries node_type field)
    - Disease search returns DiseaseNode results

- backend/api/routes/search.py
    Update GET /api/search to call search_nodes() and return mixed results.

- backend/api/routes/genes.py
    Add GET /api/disease/{ontology_id}/graph → DiseaseNode + subgraph via traversal
    (Disease is a valid traversal seed, same signal-decay algorithm, seeds at signal=1.0)

After Phase 9:
- curl localhost:8000/api/search?q=diabetes → returns Disease nodes in results
- curl localhost:8000/api/disease/EFO_0001360/graph → returns Disease + connected subgraph
- curl localhost:8000/api/gene/TP53/graph → now includes INTERACTS_WITH edges in result
- All existing MVP endpoints still return valid JSON (no regressions)

---

PHASE 10 — Embedding agent

File to create:
- backend/agents/embedding_agent.py
    Pattern: same as CitationAgent — batch, scheduled, process nodes with unmet trigger condition.

    class EmbeddingAgent(BaseAgent):
      agent_name = "EmbeddingAgent"
      agent_version = "0.1.0"

      async def run(self, batch_size: int = settings.EMBEDDING_AGENT_BATCH_SIZE):
        # 1. Fetch nodes needing embedding
        nodes = await self._fetch_unembedded_nodes(batch_size)
        # 2. For each node: call OpenRouter embedding API
        for node in nodes:
          text = node['summary_text']
          embedding = await self._embed(text)
          await self._write_embedding(node['id'], node['label'], embedding)
        # 3. Write agent run log to graph

      async def _fetch_unembedded_nodes(self, limit):
        # UNION across Gene, Protein, Disease where summary_text IS NOT NULL AND embedding IS NULL
        # MATCH (n:Gene) WHERE n.summary_text IS NOT NULL AND n.embedding IS NULL RETURN ...
        # UNION MATCH (n:Protein) ... UNION MATCH (n:Disease) ...
        # LIMIT $limit

      async def _embed(self, text: str) -> list[float]:
        # Call OpenRouter text-embedding-3-small via AsyncOpenAI client
        # response = await get_client().embeddings.create(model=settings.EMBEDDING_MODEL, input=text)
        # return response.data[0].embedding

      async def _write_embedding(self, node_id: str, label: str, embedding: list[float]):
        # MATCH (n:{label} {id_field: $id}) SET n.embedding = $embedding

    Register in backend/main.py APScheduler:
      add_job(embedding_agent.run, "cron", hour=settings.EMBEDDING_AGENT_CRON_HOUR)
    Add POST /admin/agents/embedding/run → trigger immediately (same pattern as citation agent)

After Phase 10:
- POST /admin/agents/embedding/run
- MATCH (g:Gene) WHERE g.embedding IS NOT NULL RETURN count(g) — expect >0 after first run.
- Verify embedding dimension: MATCH (g:Gene) WHERE g.embedding IS NOT NULL
  RETURN size(g.embedding) LIMIT 1 — expect 1536.

---

PHASE 11 — Dynamic Text2Cypher schema

File to modify:
- backend/llm/prompts/text2cypher.py
    Replace the hardcoded schema block with a dynamic generator.

    Add function:
      async def get_schema_block() -> str:
        """Generate schema description from live Neo4j via apoc.meta.schema()."""
        async with get_session() as session:
          result = await session.run("CALL apoc.meta.schema() YIELD value RETURN value")
          schema = (await result.single())["value"]
        return _render_schema(schema)

      def _render_schema(schema: dict) -> str:
        # Render node labels, their properties and types, and relationships
        # Filter to OmniGraph labels: Gene, Transcript, Protein, Variant, Disease
        # Format: "- (:Label {prop1: type, prop2: type})" per label
        # Format: "- (:LabelA)-[:REL_TYPE {prop}]->(:LabelB)" per relationship
        # Exclude internal/operational properties: embedding, citation_attempted

    Cache the result at module level (regenerate once at startup):
      _cached_schema_block: str = ""

      async def ensure_schema_cached():
        global _cached_schema_block
        if not _cached_schema_block:
          _cached_schema_block = await get_schema_block()

    Call ensure_schema_cached() in backend/main.py startup event (after create_indexes()).

    Update TEXT2CYPHER_SYSTEM_PROMPT to be a function:
      async def build_text2cypher_prompt() -> str:
        schema = _cached_schema_block or await get_schema_block()
        return f"""You are a Neo4j Cypher expert for OmniGraph...

    # Schema
    {schema}

    # Rules
    {CURATED_RULES}

    # Examples
    {CURATED_EXAMPLES}"""

    Update CURATED_EXAMPLES to add:
    Q: Which proteins interact with TP53?
    A: MATCH (p:Protein {hgnc_symbol: 'TP53'})-[r:INTERACTS_WITH]-(partner:Protein)
       RETURN partner.hgnc_symbol AS partner, r.combined_score AS score
       ORDER BY r.combined_score DESC LIMIT 20

    Q: What genes are associated with type 2 diabetes?
    A: MATCH (d:Disease)<-[:ASSOCIATED_WITH]-(v:Variant)-[:IN_GENE]->(g:Gene)
       WHERE toLower(d.name) CONTAINS 'type 2 diabetes'
       RETURN DISTINCT g.hgnc_symbol AS gene, count(v) AS variant_count
       ORDER BY variant_count DESC

    Q: Find pathogenic variants in TP53.
    A: MATCH (v:Variant)-[:IN_GENE]->(g:Gene {hgnc_symbol: 'TP53'})
       WHERE v.clinical_significance IN ['Pathogenic', 'Likely pathogenic']
       RETURN v.rsid AS variant, v.clinical_significance AS significance

    Q: What proteins interact with EGFR that also regulate cancer-related genes?
    A: MATCH (egfr:Protein {hgnc_symbol: 'EGFR'})-[:INTERACTS_WITH]-(p:Protein)
       MATCH (p)-[:REGULATES]->(g:Gene)
       WHERE g.cancer_gene = true AND EXISTS { (p)-[:REGULATES]->() }
       RETURN DISTINCT p.hgnc_symbol AS protein, collect(g.hgnc_symbol) AS regulated_cancer_genes

After Phase 11:
- POST /api/query {"question": "Which proteins interact with TP53?"}
  → returns valid Cypher using INTERACTS_WITH, answer mentions protein partners.
- POST /api/query {"question": "What genes are associated with type 2 diabetes?"}
  → returns valid Cypher traversing Disease → Variant → Gene.

---

PHASE 12 — Frontend: visual overhaul + camera modes

Files to modify:

- frontend/src/styles/layers.ts
    Full colour palette update (see 06_data_vision.md for rationale):
      Gene:         '#4ade80'  (green — keep)
      Transcript:   '#60a5fa'  (blue — keep)
      Protein:      '#c084fc'  (violet — all subtypes)
      Protein TF:   '#f59e0b'  (amber — TF accent, larger nodeSize)
      Variant:      '#2dd4bf'  (teal)
      Disease:      '#f472b6'  (hot pink)
    Edge colours:
      REGULATES activator:   '#22c55e'
      REGULATES repressor:   '#ef4444'
      PRODUCES:              '#818cf8'
      TRANSLATES_TO/ENCODES: '#c084fc'
      INTERACTS_WITH:        '#64748b'
      ASSOCIATED_WITH:       '#f472b6'
      IN_GENE:               '#2dd4bf'
      IMPLICATED_IN:         '#fb923c'
    Add layer constants:
      DISEASE_LAYER_Z = 900
    Add 4th layer config:
      { z: 900, color: '#f472b6', label: 'Phenotype' }

- frontend/src/components/GraphViewer3D.tsx
    Scene background: set scene.background = new THREE.Color(0x050508).
    No stars, no gradients — minimal dark only.
    Add 4th semi-transparent layer plane at Z=900 (pink tint).
    Update node renderer to use revised palette. TF proteins render at nodeSize*1.4.
    Update edge renderer with new edge colour map.
    Add Orbit/Fly camera toggle:
      Import FlyControls from 'three/examples/jsm/controls/FlyControls'.
      State: cameraMode: 'orbit' | 'fly', default 'orbit'.
      On mount: initialise OrbitControls (existing). When cameraMode switches to 'fly':
        disable OrbitControls, enable FlyControls.
        FlyControls config: movementSpeed=80, rollSpeed=0.005, dragToLook=true.
      Keyboard handler (document keydown): 'f' or 'F' toggles cameraMode.
      Render a small HUD badge bottom-left: "ORBIT" or "FLY MODE" in monospace.
      Fly controls: W/S = forward/back, A/D = strafe, Q/E = up/down,
                    mouse drag = look direction. Esc or F = return to Orbit.

After Phase 12 visual check:
  - Background is deep dark (near-black), no visible stars/gradient.
  - Gene nodes are green, transcript nodes blue, protein nodes violet, TF nodes amber.
  - Variant nodes are teal, disease nodes hot pink.
  - Press F → HUD changes to "FLY MODE", WASD moves camera through graph.
  - Press F again → returns to orbit.

---

PHASE 13 — Frontend: 4th layer + new node type rendering

Files to modify:

- frontend/src/components/GraphViewer3D.tsx
    Add 4th semi-transparent layer plane (PlaneGeometry at Z=900, hot-pink tint).
    Update node renderer: variant nodes = teal spheres. Disease nodes = hot-pink spheres, nodeSize*1.6.
    INTERACTS_WITH edges render at linkWidth*0.6 (thinner than inter-layer edges).
    All colours from layers.ts Phase 12 palette.

- frontend/src/components/SearchBar.tsx
    Support mixed search results (Gene + Disease + Protein + Variant).
    Show node_type chip per result in dropdown ("Gene", "Disease", "Protein", "Variant").
    On Disease selected: call /api/disease/{ontology_id}/graph.
    Route to correct endpoint by node_type field.

- frontend/src/components/NodeDetailPanel.tsx
    Add DiseasePanel: ontology_id, name, category, variant count.
    Add VariantPanel: rsid, chr:position, clinical_significance, consequence_type, gnomad_af.
    Update ProteinPanel: show summary_text (3-line truncate, expandable), go_terms (first 5 as chips), subcellular_loc.
    Update GenePanel: show pli_score (label "LoF intolerance"), cancer_gene flag.

- frontend/src/components/LayerToggle.tsx
    Add 4th checkbox: Phenotype (alongside Genomics / Transcriptomics / Proteomics).

After Phase 13: /verify checks:
  1. 4 layer planes visible in 3D viewer.
  2. Variant nodes = teal, disease nodes = hot-pink (larger).
  3. Search "diabetes" → Disease result with "Disease" chip.
  4. Disease subgraph loads with Variants + Genes.
  5. VariantPanel shows rsid + clinical_significance.
  6. DiseasePanel shows name + variant count.
  7. Phenotype layer toggle hides/shows disease nodes correctly.
  8. INTERACTS_WITH edges visually thinner than REGULATES edges.

---

PHASE 14 — Frontend: entity browser sidebar

Files to create/modify:

- backend/api/routes/search.py (expand existing)
    Expand GET /api/search to accept filter params:
      type: "gene"|"protein"|"variant"|"disease"|"transcript"|"all" (default "all")
      chromosome: string (filter Gene/Variant)
      biotype: string (filter Gene/Transcript)
      clinical: string (filter Variant clinical_significance)
      pli_min: float (filter Gene pli_score >= value)
      limit: int (default 50, max 200)
      offset: int (default 0, pagination)
    Returns: { results: [...SearchResult], total: int, has_more: bool }
    SearchResult gains: node_type field.
    Use Neo4j fulltext index "node_search" (covers Gene|Transcript|Protein|Disease).
    For Variant: separate MATCH on rsid (not covered by fulltext index).
    For type="all": UNION across labels, rank by fulltext score.

- backend/api/routes/graph.py (new file)
    POST /api/graph/multi
      Body: { seed_ids: [str], seed_types: [str] }  ← parallel list of (id, type) pairs
      For each seed: run signal-decay traversal (same params as /api/gene/{symbol}/graph).
      Run traversals with asyncio.gather (parallel).
      Merge results: deduplicate nodes by machine ID, deduplicate edges by (source, type, target).
      Return one GraphResponse.
      Also detects connected components: if merged graph has >1 component, add
        warnings: [{ type: "disconnected", component_count: N, message: "..." }]
      to the response (extend GraphResponse model).

    GET /api/graph/path?from={id_a}&type_a={type}&to={id_b}&type_b={type}&max_hops=6
      Runs Neo4j shortestPath:
        MATCH (a:{LabelA} {id_field: $id_a}), (b:{LabelB} {id_field: $id_b})
        MATCH p = shortestPath((a)-[*..{max_hops}]-(b))
        RETURN p
      Response:
        { path_found: bool, hop_count: int|null, path_quality: "direct"|"moderate"|"weak"|"no_path",
          nodes: [...], edges: [...], warning: str|null }
      Quality tiers: 1-2 hops="direct", 3-4="moderate", 5-6="weak", no result="no_path".
      Hard cap at 6 hops — enforced in Cypher ([*..6]), not just in response filtering.
      "no_path" message: "No path found within 6 hops. These entities may not be directly connected at current data resolution."
      "weak" warning: "This path spans N hops and may not represent a direct biological relationship."

- frontend/src/components/EntityBrowser.tsx (new component)
    Collapsible left panel — 320px wide when open, 24px handle when collapsed.
    Slides over 3D viewer (viewer does not resize).
    Collapsed state: vertical "ENTITY BROWSER" label on handle. Click to open.

    Contents:
    1. Search input (debounced 300ms) → GET /api/search?q=...&type=...&limit=50
    2. Type filter tabs: All / Gene / Protein / Variant / Disease
    3. Additional filters (collapsed accordion):
       - Chromosome (Gene/Variant): dropdown
       - Clinical significance (Variant): dropdown
       - LoF intolerance ≥ (Gene): slider
    4. Virtualized result list (react-window FixedSizeList):
       - Each row: checkbox + node_type chip + display name + secondary info
       - Checkbox selects for multi-load
    5. Pagination: "Load more" button when has_more=true (offset += 50)
    6. Footer (sticky): "Load selected (N)" button + "Clear graph" button
       "Load selected": disabled when N=0. Calls POST /api/graph/multi with selected IDs.
       "Clear graph": resets viewer to empty (no default TP53 pre-load).

    Disconnected island handling:
    - After POST /api/graph/multi, if response.warnings includes "disconnected":
      show banner above viewer: "N of M selected entities form separate clusters."
      Show "Find path between..." button for each disconnected pair (up to 3 pairs).
      On click: GET /api/graph/path → adds path nodes/edges to current view.
      Path quality badge shown on the added path edges (direct/moderate/weak/no_path).

    Seed tinting (Layer 2):
    - When multi-select loads N seeds, assign each seed a subtle accent ring colour
      (cycle through 6 accent colours distinct from node colours).
    - Nodes exclusive to one seed get a faint glow of that seed's accent.
    - Shared nodes (bridges) get no accent — they're visually neutral.
    - Implemented via a custom nodeThreeObject renderer in GraphViewer3D.

- frontend/src/App.tsx
    Add EntityBrowser to left side of layout.
    Wire "Clear graph" to reset graphData to empty.
    Pass onMultiLoad callback to EntityBrowser that calls POST /api/graph/multi
    and merges result into current graphData (additive, not replacing).
    Show disconnected-island banner when warnings present.
    Show "Find path" shortcut when exactly 2 nodes are selected and disconnected.

After Phase 14: /verify checks:
  1. Entity browser opens/collapses via left handle.
  2. Search "TP53" in browser → Gene result appears with checkbox.
  3. Search "diabetes" → Disease results appear.
  4. Select TP53 + type 2 diabetes → "Load selected (2)" → graph loads both subgraphs.
  5. If disconnected: banner appears with "Find path" button.
  6. Click "Find path" → path added to graph with quality badge.
  7. If no path: "No path found within 6 hops" message shown.
  8. "Clear graph" resets viewer to empty.
  9. Seed tinting: TP53 cluster has one accent ring, diabetes cluster another.

---

PHASE 15 — Tests

Files to modify/create:
- backend/tests/test_queries.py
    Add:
      test_variant_lookup: MATCH (v:Variant) RETURN count(v) > 0
      test_disease_lookup: MATCH (d:Disease {name: ...}) returns valid node
      test_interacts_with_edges: TP53 protein has INTERACTS_WITH neighbours
      test_associated_with_edges: a variant has ASSOCIATED_WITH edge to a disease
      test_disease_traversal: /api/disease/{ontology_id}/graph returns nodes + edges
      test_multi_seed_graph: POST /api/graph/multi with ["TP53","BRCA2"] returns
        merged subgraph — node count > 0, no duplicate node ids, edges list non-empty.
      test_multi_seed_disconnected: POST /api/graph/multi with two unrelated seeds
        (e.g. ["BRCA2","EFO_0001360"]) returns {"connected": false, "seeds": [...]}
        in metadata — both seed clusters present in nodes list.
      test_shortest_path_found: GET /api/graph/path?from_id=...&to_id=... where a path
        exists — response has path_nodes list, hop_count <= 6.
      test_shortest_path_not_found: same endpoint with two nodes that have no path
        within 6 hops — response has {"path_found": false} and hop_count is null.
      test_entities_search: GET /api/entities?q=TP53&types=Gene&page=1&limit=20 returns
        items list with at least 1 result, each item has id, node_type, display_name fields.

- backend/tests/test_agents.py
    Add:
      test_embedding_agent_no_new_nodes: after run, node count unchanged
      test_embedding_agent_no_new_edges: after run, edge count unchanged
      test_embedding_agent_sets_embedding: nodes have embedding of correct dimension (1536)

- backend/tests/test_text2cypher.py
    Add 4 new benchmark questions (one per new edge type):
      "Which proteins interact with TP53?"
      "What genes are associated with type 2 diabetes?"
      "Find pathogenic variants in BRCA1."
      "What proteins interact with EGFR?"
    Each: assert response.cypher is non-empty, assert no write keywords, assert answer non-empty.

Run pytest backend/tests/ — all tests must pass.

---

KNOWN RISKS:

- STRING ENSP→UniProt mapping: STRING uses Ensembl protein IDs (ENSP). The GENCODE
  SwissProt metadata maps ENST→UniProt; Ensembl also maps ENSG→ENSP. You may need
  the UniProt ID mapping file from UniProt (idmapping_selected.tab.gz) to cover gaps.
  Download it if more than 5% of STRING interactions are skipped due to missing mappings.

- GWAS Catalog TSV format changes: the full-download TSV format has changed between
  releases. If column names differ from the ETL script, print available columns and abort
  (same discipline as ADR-0003 / GTEx).

- apoc.meta.schema() performance: on a large graph this can take 5–30 seconds.
  Call it only once at startup and cache; never call it per-request.

- Vector index memory: ~370MB for 60k embedded nodes at 1536 dims. If Neo4j runs
  out of page cache after embedding agent completes, increase pagecache further.

- Hub protein explosion: TP53, EGFR, MYC each have 30–50 STRING interactions at
  combined_score > 0.9. The STRING_MAX_EXPAND_PER_NODE cap (default 10) must be
  applied in the traversal, not just at load time.

---

RULES (non-negotiable, same as Phase 1):
- No placeholder code or TODOs — every file complete and functional.
- All ETL uses MERGE (idempotent), never CREATE alone.
- Topology (new nodes/edges) from bulk downloaded files only. API calls for enrichment only.
- All agent writes carry: source_agent, agent_version, run_timestamp.
- validate_cypher() blocks MERGE/CREATE/DELETE/SET — enforced before every LLM execution.
- No new graph topology from LLM — embeddings and PMIDs are agent-written, never LLM-hallucinated.
- After each phase: /code-review high, fix all findings before next phase.
- After Phase 12: /verify the 4 visual checks (background, node colours, FLY MODE badge,
  camera controls). All 4 must pass before Phase 13.
- After Phase 13: /verify all 8 checks (4th layer plane, teal/pink nodes, Disease search,
  VariantPanel, DiseasePanel, Phenotype toggle, thinner INTERACTS_WITH edges).
- After Phase 14: /verify all 9 entity browser smoke-test checks. All must pass before Phase 15.
- If any verify check fails: /diagnose before trying random fixes.
- Entity browser rules: "Load selected" with 0 items selected is a no-op (button disabled).
  "Clear graph" resets viewer to empty — never call the traversal API on clear.
  Disconnected island banner is shown when POST /api/graph/multi metadata.connected is false.
  Shortest-path hard cap = 6 hops in Cypher; if no path found within cap, show
  "No path found within 6 hops" and do NOT retry with a larger cap.
- OpenRouter client (not Anthropic SDK) — base_url=https://openrouter.ai/api/v1.
- STRING_MIN_CONFIDENCE and GWAS_MIN_SIGNIFICANCE are env vars — never hardcode thresholds.
- CONTEXT.md and 06_data_vision.md are the domain authority — if code conflicts with them,
  fix the code.
```
