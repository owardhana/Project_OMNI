# ADR 0008 — Neo4j native vector indexing over an external vector store

Status: Accepted (2026-06-16)

## Context

Semantic search on graph nodes requires storing and querying embedding vectors.
Two architectural options:

**Option A — External vector store** (Qdrant, Weaviate, Pinecone, pgvector):
- Purpose-built ANN (approximate nearest-neighbour) search
- Better recall/latency at scale
- Requires a second storage system; semantic results must be joined with graph
  data in application code (two round trips)

**Option B — Neo4j native vector indexing:**
- Available since Neo4j 5.11 via `db.index.vector.createNodeIndex()`
- Embeddings stored as `[float]` properties on nodes
- `CALL db.index.vector.queryNodes()` can be composed with `MATCH` clauses in
  the same Cypher query

## Decision

Use **Neo4j native vector indexing**. Embeddings stored as `embedding: [float]`
on Gene, Protein, and Disease nodes where `summary_text IS NOT NULL`.

## Reason

The defining capability for OmniGraph is composing semantic search **with graph
traversal in a single query**:

```cypher
CALL db.index.vector.queryNodes('protein_embeddings', 10, $query_vector)
YIELD node, score
MATCH (node)-[:REGULATES]->(g:Gene {hgnc_symbol: 'BRCA2'})
RETURN node.hgnc_symbol, score
ORDER BY score DESC
```

"Find proteins functionally similar to kinases that also regulate BRCA2" — one
Cypher query, no application-layer join. With an external store this requires:
1. Vector store → top-k node IDs
2. Neo4j → filter by graph structure
3. Application layer → join and rank

The external path has two round trips and loses the unified ranking. For a
knowledge graph where the traversal IS the product, composability outweighs
raw ANN performance.

## Scope of embedding

Not all nodes are embedded — only those with meaningful natural-language text:

| Node | Text source | Embedded |
|------|-------------|---------|
| Gene | NCBI Gene summary (`summary_text`) | Yes (where non-null) |
| Protein | UniProt function comment (`summary_text`) | Yes (where non-null) |
| Disease | EFO/GWAS trait description (`description`) | Yes |
| Transcript | No meaningful free text | No |
| Variant | No free text (structured properties only) | No |

Estimated footprint: ~60k nodes × 1536 dims × 4 bytes ≈ **370MB** of float data.

## Model

`openai/text-embedding-3-small` (1536-dim) via OpenRouter — same provider as
Text2Cypher and citation check, no new API contract. Domain-specific biomedical
models (BioLinkBERT, PubMedBERT) would give better semantic precision but
require self-hosting; defer to a future phase if retrieval quality becomes a
bottleneck.

## Implementation

Vector indexes created at startup in `backend/db/neo4j_client.py` alongside the
existing fulltext index:

```python
"CREATE VECTOR INDEX gene_embeddings IF NOT EXISTS "
"FOR (n:Gene) ON (n.embedding) "
"OPTIONS {indexConfig: {`vector.dimensions`: 1536, `vector.similarity_function`: 'cosine'}}",
```

Three indexes: `gene_embeddings`, `protein_embeddings`, `disease_embeddings`.

Embeddings populated by the **embedding agent** (background, scheduled batch):
processes nodes where `summary_text IS NOT NULL AND embedding IS NULL`, calls
OpenRouter embedding API in batches of 50–100, writes `embedding` property back
to Neo4j via MERGE.

## Consequences

- `neo4j_client.py` gains three vector index DDL statements (alongside existing
  fulltext + b-tree indexes).
- `backend/api/models.py` — `embedding` field is internal; never returned in
  API responses (too large).
- `backend/agents/` — new `EmbeddingAgent` following the `CitationAgent` pattern:
  batch, scheduled, nightly or post-ETL.
- `backend/llm/prompts/text2cypher.py` — can now include semantic query examples
  using `db.index.vector.queryNodes()` in the curated examples section.
- Neo4j memory: `pagecache` must cover the ~370MB embedding data plus existing
  graph; the docker-compose bump to `pagecache: 4G` (from current 1G) is required
  before embedding agent runs.

## Revisit condition

If ANN search latency becomes a bottleneck (>500ms per semantic query at scale),
or if the graph exceeds ~500k embedded nodes, evaluate migrating embeddings to
a dedicated Qdrant instance. The `embedding` property can be dual-written during
migration. At current projected scale (60k nodes) this is not expected.

## Alternatives considered

- **Qdrant standalone:** rejected — two systems, no traversal composability.
- **pgvector (Postgres):** rejected — adds Postgres to the stack for one feature.
- **Weaviate:** rejected — same composability problem; also requires managing
  a separate schema for what is already a graph entity.
