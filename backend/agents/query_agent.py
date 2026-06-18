"""QueryAgent — Text2Cypher + answer synthesis.

Flow: question -> Text2Cypher (LLM) -> validate_cypher -> execute (10s timeout)
-> synthesis (LLM) -> QueryResponse. On invalid Cypher after 2 retries, returns a
structured error rather than a hallucinated answer. Never writes to the graph.
"""

import asyncio
import json
import logging
import re

from backend.agents.base_agent import BaseAgent
from backend.api.models import QueryResponse
from backend.db.neo4j_client import get_session
from backend.llm.client import SYNTHESIS_MODEL, TEXT2CYPHER_MODEL, complete
from backend.llm.prompts.synthesis import SYNTHESIS_SYSTEM_PROMPT
from backend.llm.prompts.text2cypher import build_text2cypher_prompt
from backend.llm.validators import validate_cypher

logger = logging.getLogger(__name__)

_QUERY_TIMEOUT_S = 10
_MAX_RESULT_ROWS = 50
_FENCE_RE = re.compile(r"```(?:cypher|sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_cypher(text: str) -> str:
    """Pull a Cypher query out of an LLM response (strip markdown fences/prose)."""
    if not text:
        return ""
    fenced = _FENCE_RE.search(text)
    candidate = fenced.group(1) if fenced else text
    return candidate.strip().rstrip(";").strip()


def _extract_citations(rows: list[dict]) -> list[str]:
    """Collect PMID strings from any list-valued result fields."""
    pmids: set[str] = set()
    for row in rows:
        for value in row.values():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item.isdigit():
                        pmids.add(item)
    return sorted(pmids)


async def _run_cypher(cypher: str) -> list[dict]:
    async def _inner() -> list[dict]:
        async with get_session() as session:
            result = await session.run(cypher)
            return await result.data()

    return await asyncio.wait_for(_inner(), timeout=_QUERY_TIMEOUT_S)


class QueryAgent(BaseAgent):
    agent_name = "QueryAgent"
    agent_version = "0.1.0"

    def _user_prompt(self, question: str, tissue: str, max_hops: int) -> str:
        hint = ""
        if tissue and tissue.lower() != "all":
            hint = f"\n(Restrict expression to the '{tissue}' tissue where relevant.)"
        return f"Question: {question}{hint}\nReturn only the Cypher query."

    async def query(
        self, question: str, tissue: str = "all", max_hops: int = 2
    ) -> QueryResponse:
        messages = [
            {"role": "system", "content": await build_text2cypher_prompt()},
            {"role": "user", "content": self._user_prompt(question, tissue, max_hops)},
        ]

        cypher = ""
        # 3 attempts = initial + 2 retries.
        for attempt in range(3):
            raw = await complete(TEXT2CYPHER_MODEL, messages, temperature=0)
            candidate = _extract_cypher(raw)
            if candidate and await validate_cypher(candidate):
                cypher = candidate
                break
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "That query was invalid or used a write operation. "
                        "Return a corrected, READ-ONLY Cypher query only."
                    ),
                }
            )

        if not cypher:
            return QueryResponse(
                answer="I couldn't generate a valid read-only query for that question.",
                cypher="",
                results=[],
                citations=[],
                error="invalid_cypher",
            )

        try:
            rows = await _run_cypher(cypher)
        except asyncio.TimeoutError:
            return QueryResponse(
                answer="The query took too long to execute (>10s).",
                cypher=cypher,
                error="timeout",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("QueryAgent execution failed: %s", exc)
            return QueryResponse(
                answer="The query failed to execute against the graph.",
                cypher=cypher,
                error=str(exc),
            )

        synthesis_user = (
            f"Question: {question}\n\nCypher: {cypher}\n\n"
            f"Results (JSON): {json.dumps(rows[:30], default=str)}"
        )
        answer = await complete(
            SYNTHESIS_MODEL,
            [
                {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
                {"role": "user", "content": synthesis_user},
            ],
        )

        return QueryResponse(
            answer=answer.strip(),
            cypher=cypher,
            results=rows[:_MAX_RESULT_ROWS],
            citations=_extract_citations(rows),
        )


query_agent = QueryAgent()
