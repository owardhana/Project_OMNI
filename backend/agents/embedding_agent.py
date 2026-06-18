"""EmbeddingAgent — populate semantic-search embeddings on graph nodes.

Same shape as CitationAgent (BaseAgent subclass, batch, scheduled): it processes
nodes whose trigger condition is unmet — Gene/Protein with ``summary_text`` and
Disease with ``description``, where ``embedding IS NULL`` — calls the OpenRouter
embedding API, and SETs the 1536-dim vector back with provenance (ADR-0008).

It NEVER creates biological topology — it only SETs an ``embedding`` property on
nodes that already exist, plus an operational EmbeddingRun log node.
"""

import logging

from backend.agents.base_agent import BaseAgent
from backend.config import settings
from backend.db.neo4j_client import get_session
from backend.llm.client import get_client

logger = logging.getLogger(__name__)

_EMBED_DIM = 1536
_MAX_CHARS = 8000  # keep well under the model's token limit

# Canonical key field per embeddable label. Doubles as a label allowlist so the
# label/id_field interpolated into Cypher can never be user input.
_ID_FIELD = {"Gene": "ensembl_id", "Protein": "uniprot_id", "Disease": "ontology_id"}

# Gene/Protein embed their UniProt/NCBI summary_text; Disease embeds its trait
# description (Transcript/Variant carry no meaningful free text — not embedded).
_FETCH_UNEMBEDDED = """
CALL {
  MATCH (n:Gene)
  WHERE n.summary_text IS NOT NULL AND n.embedding IS NULL
  RETURN 'Gene' AS label, n.ensembl_id AS id, n.summary_text AS text
  UNION
  MATCH (n:Protein)
  WHERE n.summary_text IS NOT NULL AND n.embedding IS NULL
  RETURN 'Protein' AS label, n.uniprot_id AS id, n.summary_text AS text
  UNION
  MATCH (n:Disease)
  WHERE n.description IS NOT NULL AND n.embedding IS NULL
  RETURN 'Disease' AS label, n.ontology_id AS id, n.description AS text
}
RETURN label, id, text
LIMIT $limit
"""


class EmbeddingAgent(BaseAgent):
    agent_name = "EmbeddingAgent"
    agent_version = "0.1.0"

    async def _fetch_unembedded_nodes(self, limit: int) -> list[dict]:
        async with get_session() as session:
            return await (
                await session.run(_FETCH_UNEMBEDDED, limit=limit)
            ).data()

    async def _embed(self, text: str) -> list[float]:
        response = await get_client().embeddings.create(
            model=settings.EMBEDDING_MODEL, input=text[:_MAX_CHARS]
        )
        return response.data[0].embedding

    async def _write_embedding(
        self, label: str, node_id: str, embedding: list[float]
    ) -> None:
        id_field = _ID_FIELD[label]  # KeyError on any non-allowlisted label
        query = f"""
        MATCH (n:{label} {{{id_field}: $id}})
        SET n.embedding = $embedding,
            n.embedding_model = $model,
            n.source_agent = $source_agent,
            n.agent_version = $agent_version,
            n.run_timestamp = $run_timestamp
        """
        params = {
            "id": node_id,
            "embedding": embedding,
            "model": settings.EMBEDDING_MODEL,
            **self.provenance(),
        }
        async with get_session() as session:
            await session.run(query, **params)

    async def run(self, batch_size: int | None = None) -> dict:
        batch_size = batch_size or settings.EMBEDDING_AGENT_BATCH_SIZE
        nodes = await self._fetch_unembedded_nodes(batch_size)
        embedded = 0
        failed = 0
        for node in nodes:
            try:
                embedding = await self.retry(self._embed, node["text"])
                if len(embedding) != _EMBED_DIM:
                    logger.warning(
                        "EmbeddingAgent: %s %s returned dim %d (expected %d), skipping",
                        node["label"], node["id"], len(embedding), _EMBED_DIM,
                    )
                    failed += 1
                    continue
                await self._write_embedding(node["label"], node["id"], embedding)
                embedded += 1
            except Exception as exc:  # noqa: BLE001 — one bad node must not stop the batch
                failed += 1
                logger.warning(
                    "EmbeddingAgent: failed to embed %s %s: %s",
                    node["label"], node["id"], exc,
                )
        summary = {
            "nodes_embedded": embedded,
            "nodes_failed": failed,
            "batch_size": batch_size,
        }
        await self.write_run_log_to_graph("EmbeddingRun", summary)
        return summary

    async def recent_runs(self, limit: int = 10) -> list[dict]:
        query = """
        MATCH (n:EmbeddingRun)
        RETURN properties(n) AS props
        ORDER BY n.run_timestamp DESC
        LIMIT $limit
        """
        async with get_session() as session:
            rows = await (await session.run(query, limit=limit)).data()
        return [r["props"] for r in rows]


embedding_agent = EmbeddingAgent()
