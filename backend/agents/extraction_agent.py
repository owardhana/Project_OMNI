"""ExtractionAgent — literature -> CandidateEdge proposals (Feature 2, P1).

Orchestrates the closed-world pipeline: build the gazetteer from the graph, pull a
PubMed reldate delta, and for every sentence with >=2 distinct linked entities, ask a
cheap model whether an in-vocab relation is asserted, then STAGE it as a CandidateEdge
(never trusted topology — ADR-0013).

First agent to propose topology, so it is firewalled: candidates live in operational
labels, tagged provenance_tier='literature', promotion is a separate (P2) gate. The
run is manual/opt-in — the admin trigger is gated on EXTRACTION_AGENT_ENABLED (default
off) so it never spends on NCBI/LLM unattended.
"""

import asyncio
import logging

import httpx

from backend.agents.base_agent import BaseAgent
from backend.db.neo4j_client import get_session
from backend.extraction.dictionary import Entry, build_gazetteer_from_graph
from backend.extraction.ingest import (
    fetch_articles,
    fetch_recent_pmids,
    request_delay,
    split_sentences,
)
from backend.extraction.relation import edge_type_for, extract_relation
from backend.extraction.stage import stage_verdict
from backend.config import settings

logger = logging.getLogger(__name__)


def _resolve_entities(matches) -> list[Entry]:
    """All resolved entities in the text, de-duplicated by node_id.

    A surface routinely resolves to *several* nodes — most importantly a symbol like
    ``TP53`` is BOTH a gene and a protein (built as separate nodes by the gazetteer).
    Keeping every candidate (not just ``candidates[0]``) is essential: it's what lets
    ``_candidate_pairs`` form the protein-protein pair for ``INTERACTS_WITH``. Dropping
    to the first candidate (the gene) makes protein-protein pairs impossible, so the
    extractor would only ever produce ``IMPLICATED_IN``.
    """
    seen: dict[str, Entry] = {}
    for m in matches:
        for entry in m.candidates:
            seen.setdefault(entry.node_id, entry)
    return list(seen.values())


def _candidate_pairs(entities: list[Entry]) -> list[tuple[Entry, Entry]]:
    """Distinct, in-vocabulary entity pairs from one sentence. Skips self-pairs
    (same node_id) and pairs whose kinds map to no MVP edge type."""
    pairs: list[tuple[Entry, Entry]] = []
    for i in range(len(entities)):
        for j in range(i + 1, len(entities)):
            a, b = entities[i], entities[j]
            if a.node_id == b.node_id:
                continue
            if edge_type_for(a.kind, b.kind) is None:
                continue
            pairs.append((a, b))
    return pairs


class ExtractionAgent(BaseAgent):
    agent_name = "ExtractionAgent"
    agent_version = "0.1.0"

    async def run(
        self,
        term: str | None = None,
        days: int | None = None,
        retmax: int | None = None,
    ) -> dict:
        async with get_session() as session:
            gazetteer = await build_gazetteer_from_graph(session)
        logger.info("ExtractionAgent: gazetteer surfaces=%d", len(gazetteer))

        # Keys 'candidate'/'enriched'/'skipped' match stage_verdict's returned status
        # exactly, so `stats[status] += 1` lands in the right bucket.
        stats = {
            "pmids_fetched": 0, "articles": 0, "sentences_scanned": 0,
            "pairs_evaluated": 0, "candidate": 0, "enriched": 0, "skipped": 0,
        }
        delay = request_delay()

        async with httpx.AsyncClient(timeout=30.0) as http, get_session() as session:
            pmids = await fetch_recent_pmids(http, term, days, retmax)
            stats["pmids_fetched"] = len(pmids)
            await asyncio.sleep(delay)

            batch = settings.EXTRACTION_EFETCH_BATCH
            for i in range(0, len(pmids), batch):
                articles = await fetch_articles(http, pmids[i : i + batch])
                await asyncio.sleep(delay)
                for pmid, art in articles.items():
                    stats["articles"] += 1
                    text = f"{art['title']}. {art['abstract']}"
                    for sentence in split_sentences(text):
                        stats["sentences_scanned"] += 1
                        entities = _resolve_entities(gazetteer.match(sentence))
                        if len(entities) < 2:
                            continue  # co-mention gate: need >=2 distinct entities
                        for a, b in _candidate_pairs(entities):
                            stats["pairs_evaluated"] += 1
                            verdict = await extract_relation(sentence, a, b, pmid)
                            if verdict is None:
                                continue
                            res = await stage_verdict(session, verdict, self.provenance())
                            status = res.get("status", "skipped")
                            stats[status] = stats.get(status, 0) + 1

        await self.write_run_log_to_graph("ExtractionRun", stats)
        logger.info("ExtractionAgent: %s", stats)
        return stats

    async def recent_runs(self, limit: int = 10) -> list[dict]:
        query = """
        MATCH (n:ExtractionRun)
        RETURN properties(n) AS props
        ORDER BY n.run_timestamp DESC
        LIMIT $limit
        """
        async with get_session() as session:
            rows = await (await session.run(query, limit=limit)).data()
        return [r["props"] for r in rows]

    async def list_candidates(self, limit: int = 50) -> list[dict]:
        """Pending candidates at/above the confidence floor, strongest first (review
        surface; promotion is P2)."""
        query = """
        MATCH (ce:CandidateEdge)
        WHERE ce.status = 'pending' AND ce.confidence >= $floor
        RETURN properties(ce) AS props
        ORDER BY ce.confidence DESC, ce.n_affirm DESC
        LIMIT $limit
        """
        async with get_session() as session:
            rows = await (
                await session.run(
                    query, floor=settings.EXTRACTION_CONFIDENCE_FLOOR, limit=limit
                )
            ).data()
        return [r["props"] for r in rows]


extraction_agent = ExtractionAgent()


if __name__ == "__main__":  # manual local run: PYTHONPATH=. python -m backend.agents.extraction_agent
    print(asyncio.run(extraction_agent.run()))
