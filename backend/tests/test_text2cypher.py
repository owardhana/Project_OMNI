"""Text2Cypher benchmark tests (live LLM via OpenRouter + live Neo4j)."""

import pytest

from backend.agents.query_agent import query_agent
from backend.llm.validators import has_write_operation

BENCHMARK_QUESTIONS = [
    "What transcription factors regulate TP53?",
    "What transcripts does BRCA2 produce in liver?",
    "Which TFs repress MYC?",
    "What are the most confident TF regulators of EGFR in brain?",
    "Show me transcripts of TP53 with high expression in blood",
    # Phase 2 — protein PPIs, disease/variant mechanisms (06_data_vision.md).
    "What proteins interact with TP53?",
    "Which genes are associated with type 2 diabetes?",
    "What are the pathogenic variants in BRCA1?",
    "What proteins interact with EGFR?",
]


@pytest.mark.parametrize("question", BENCHMARK_QUESTIONS)
async def test_benchmark_question(question):
    response = await query_agent.query(question)
    assert isinstance(response.cypher, str) and response.cypher.strip()
    assert isinstance(response.answer, str) and response.answer.strip()
    # The generated Cypher must be read-only.
    assert not has_write_operation(response.cypher)
    assert response.error is None
