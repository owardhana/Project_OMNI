"""ExtractionAgent — literature -> CandidateEdge proposals (Feature 2).

Orchestrates the closed-world pipeline: build the gazetteer from the graph, pull a set
of PubMed abstracts, and for every sentence with >=2 distinct linked entities, ask a
cheap model whether an in-vocab relation is asserted, then STAGE it as a CandidateEdge
(never trusted topology — ADR-0013).

Three entry points share one processing core (``_process_pmids``):
  - ``run``           — one-shot delta (``reldate``), the manual admin trigger.
  - ``process_window``— one publication-date window, used by the cursor pipeline
    (``extraction/backfill.py``) for the nightly forward catch-up + historical backfill.

Cost/throughput: the per-(sentence,pair) verdict is the only LLM call and the only slow
step, so verdicts within an efetch batch run under a bounded ``asyncio.Semaphore`` and
are then STAGED SERIALLY — the write is milliseconds against seconds of inference, so we
keep the throughput while avoiding intra-run MERGE contention on the same CandidateEdge.

Firewalled (ADR-0013): candidates live in operational labels, tagged
provenance_tier='literature'; promotion is a separate (P2) gate. Every entry point is
gated on EXTRACTION_AGENT_ENABLED upstream so it never spends unattended.
"""

import asyncio
import logging

import httpx
from neo4j.exceptions import TransientError

from backend.agents.base_agent import BaseAgent
from backend.db.neo4j_client import get_session
from backend.extraction.dictionary import Entry, build_gazetteer_from_graph
from backend.extraction.ingest import (
    fetch_articles,
    fetch_pmids_in_range,
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


def new_stats() -> dict:
    """Fresh counter bucket. Keys 'candidate'/'enriched'/'skipped' match
    ``stage_verdict``'s returned status so ``stats[status] += 1`` lands correctly."""
    return {
        "pmids_fetched": 0, "articles": 0, "sentences_scanned": 0,
        "pairs_evaluated": 0, "candidate": 0, "enriched": 0, "skipped": 0,
        "llm_errors": 0,
    }


class ExtractionAgent(BaseAgent):
    agent_name = "ExtractionAgent"
    agent_version = "0.2.0"

    async def build_gazetteer(self):
        """Build the surface→entity automaton from the graph. Expensive (~111k
        surfaces), so the cursor loop builds it ONCE and reuses it across chunks (it
        only changes as the graph changes)."""
        async with get_session() as session:
            gazetteer = await build_gazetteer_from_graph(session)
        logger.info("ExtractionAgent: gazetteer surfaces=%d", len(gazetteer))
        return gazetteer

    async def _verdict(self, sem: asyncio.Semaphore, sentence, a, b, pmid, model, stats):
        """One semaphore-gated verdict. Returns a RelationVerdict, or None if the model
        output was unparseable (dropped — recall cost only) or the call kept failing
        after retries (transient throttle — counted so the loop can decide to retry the
        whole chunk rather than silently lose it)."""
        async with sem:
            try:
                # extract_relation returns None on UNPARSEABLE output (a drop, never
                # retried); an exception means transient failure (rate limit / network).
                # Hard-bound the whole retry budget with wait_for: the SDK per-call
                # timeout proved unreliable against a slow-streaming free model, and a
                # stuck verdict must never block a chunk ("always running" requirement).
                budget = settings.EXTRACTION_LLM_TIMEOUT_S * (settings.EXTRACTION_HTTP_MAX_RETRIES + 1) + 5
                return await asyncio.wait_for(
                    self.retry(
                        extract_relation, sentence, a, b, pmid, model,
                        n=settings.EXTRACTION_HTTP_MAX_RETRIES,
                    ),
                    timeout=budget,
                )
            except Exception as exc:  # noqa: BLE001  (incl. asyncio.TimeoutError)
                stats["llm_errors"] += 1
                logger.warning("extraction: verdict failed/timeout pmid=%s: %s", pmid, exc)
                return None

    async def _stage(self, session, verdict, provenance) -> dict:
        """Stage one verdict, retrying only on Neo4j TransientError (a deadlock from the
        forward + backward cursors racing a MERGE / pmids-append on the same edge). The
        uniqueness constraint on CandidateEdge.triple_key prevents duplicate nodes; this
        handles the lock contention that constraint introduces."""
        attempts = settings.EXTRACTION_HTTP_MAX_RETRIES
        for attempt in range(attempts + 1):
            try:
                return await stage_verdict(session, verdict, provenance)
            except TransientError as exc:
                if attempt >= attempts:
                    raise
                await asyncio.sleep(settings.EXTRACTION_HTTP_BACKOFF_S * (2 ** attempt))
                logger.warning("extraction: staging deadlock, retry %d: %s", attempt + 1, exc)

    async def _process_pmids(self, session, http, gazetteer, pmids, model, stats) -> None:
        """Core loop: efetch abstracts in batches; within each batch run all verdicts
        concurrently (bounded), then stage the non-None ones serially."""
        provenance = self.provenance()
        delay = request_delay()
        sem = asyncio.Semaphore(max(1, settings.EXTRACTION_LLM_CONCURRENCY))
        batch = settings.EXTRACTION_EFETCH_BATCH
        for i in range(0, len(pmids), batch):
            articles = await fetch_articles(http, pmids[i : i + batch])
            await asyncio.sleep(delay)

            tasks = []
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
                        tasks.append(self._verdict(sem, sentence, a, b, pmid, model, stats))

            verdicts = await asyncio.gather(*tasks)
            for verdict in verdicts:
                if verdict is None:
                    continue
                res = await self._stage(session, verdict, provenance)
                status = res.get("status", "skipped")
                stats[status] = stats.get(status, 0) + 1

    async def process_window(
        self, session, http, gazetteer, mindate: str, maxdate: str, model: str, stats: dict
    ) -> int:
        """Fetch + process one publication-date window (cursor pipeline). Returns the
        number of PMIDs in the window."""
        pmids = await fetch_pmids_in_range(http, mindate, maxdate, delay=request_delay())
        stats["pmids_fetched"] += len(pmids)
        await self._process_pmids(session, http, gazetteer, pmids, model, stats)
        return len(pmids)

    async def run(
        self,
        term: str | None = None,
        days: int | None = None,
        retmax: int | None = None,
    ) -> dict:
        """One-shot delta over a ``reldate`` window — the manual admin trigger. The
        cursor pipeline (forward/backward) is the always-on path; see backfill.py."""
        gazetteer = await self.build_gazetteer()
        stats = new_stats()
        async with httpx.AsyncClient(timeout=30.0) as http, get_session() as session:
            pmids = await fetch_recent_pmids(http, term, days, retmax)
            stats["pmids_fetched"] = len(pmids)
            await asyncio.sleep(request_delay())
            await self._process_pmids(
                session, http, gazetteer, pmids, settings.EXTRACTION_MODEL, stats
            )
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
