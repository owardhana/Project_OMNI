"""CitationAgent — enrich existing REGULATES edges with PubMed PMIDs.

For each uncited REGULATES edge it searches NCBI E-utilities for the TF/target
pair, fetches abstracts, and keeps only PMIDs the citation-check model confirms
discuss both entities. It NEVER creates biological nodes or edges — it only SETs
pmids/citation_attempted (+provenance) on existing edges and writes an
operational CitationRun log node.

Scope note: only REGULATES edges are cited. PRODUCES (gene->transcript) edges are
structural; a "regulation" literature search over a gene/transcript pair is not
meaningful, so they are left uncited.
"""

import asyncio
import json
import logging
import re
import xml.etree.ElementTree as ET

import httpx

from backend.agents.base_agent import BaseAgent
from backend.config import settings
from backend.db.neo4j_client import get_session
from backend.llm.client import CITATION_CHECK_MODEL, complete
from backend.llm.prompts.citation_check import CITATION_CHECK_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
_MAX_RESULTS_PER_EDGE = 5
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class CitationAgent(BaseAgent):
    agent_name = "CitationAgent"
    agent_version = "0.1.0"

    @property
    def _request_delay(self) -> float:
        # NCBI allows 3 req/s without a key, 10 req/s with one.
        return 0.1 if settings.NCBI_API_KEY else 0.34

    def _ncbi_params(self, **extra) -> dict:
        params = dict(extra)
        if settings.NCBI_API_KEY:
            params["api_key"] = settings.NCBI_API_KEY
        return params

    async def _fetch_uncited_edges(self, batch_size: int) -> list[dict]:
        # REGULATES is now (:Protein)-[r]->(:Gene) (ADR-0004); the source protein's
        # hgnc_symbol still drives the PubMed query, so search is unchanged.
        query = """
        MATCH (s:Protein)-[r:REGULATES]->(t:Gene)
        WHERE (r.pmids IS NULL OR size(r.pmids) = 0)
          AND coalesce(r.citation_attempted, false) <> true
        RETURN elementId(r) AS eid, s.hgnc_symbol AS src, t.hgnc_symbol AS tgt
        LIMIT $batch
        """
        async with get_session() as session:
            return await (await session.run(query, batch=batch_size)).data()

    async def _esearch(self, http: httpx.AsyncClient, src: str, tgt: str) -> list[str]:
        params = self._ncbi_params(
            db="pubmed",
            term=f"{src} {tgt} regulation",
            retmax=_MAX_RESULTS_PER_EDGE,
            retmode="json",
        )
        resp = await http.get(_ESEARCH_URL, params=params)
        resp.raise_for_status()
        return resp.json().get("esearchresult", {}).get("idlist", [])

    async def _efetch_abstracts(
        self, http: httpx.AsyncClient, pmids: list[str]
    ) -> dict[str, str]:
        params = self._ncbi_params(
            db="pubmed", id=",".join(pmids), rettype="abstract", retmode="xml"
        )
        resp = await http.get(_EFETCH_URL, params=params)
        resp.raise_for_status()
        abstracts: dict[str, str] = {}
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            logger.warning("CitationAgent: efetch XML parse error: %s", exc)
            return abstracts
        for article in root.findall(".//PubmedArticle"):
            pmid = article.findtext(".//PMID")
            if not pmid:
                continue
            text = " ".join(
                (node.text or "") for node in article.findall(".//AbstractText")
            ).strip()
            abstracts[pmid] = text
        return abstracts

    async def _is_relevant(self, abstract: str, src: str, tgt: str) -> bool:
        if not abstract:
            return False
        raw = await complete(
            CITATION_CHECK_MODEL,
            [
                {"role": "system", "content": CITATION_CHECK_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Entity A: {src}\nEntity B: {tgt}\n\nAbstract:\n{abstract[:3000]}",
                },
            ],
            temperature=0,
        )
        match = _JSON_RE.search(raw)
        if not match:
            return False
        try:
            return bool(json.loads(match.group(0)).get("relevant", False))
        except json.JSONDecodeError:
            return False

    async def _write_pmids(self, eid: str, pmids: list[str]) -> None:
        # SET only on the existing edge; citation_attempted is set even when no
        # PMIDs were found, so the edge is not re-queried next run.
        params = {
            "eid": eid,
            "pmids": pmids,
            **self.provenance(),
        }
        query = """
        MATCH ()-[r]->() WHERE elementId(r) = $eid
        SET r.pmids = $pmids,
            r.citation_attempted = true,
            r.source_agent = $source_agent,
            r.agent_version = $agent_version,
            r.run_timestamp = $run_timestamp
        """
        async with get_session() as session:
            await session.run(query, **params)

    async def run(self, batch_size: int | None = None) -> dict:
        batch_size = batch_size or settings.CITATION_AGENT_BATCH_SIZE
        edges = await self._fetch_uncited_edges(batch_size)
        delay = self._request_delay
        edges_processed = 0
        pmids_added = 0

        async with httpx.AsyncClient(timeout=30.0) as http:
            for edge in edges:
                validated: list[str] = []
                try:
                    candidate_pmids = await self._esearch(http, edge["src"], edge["tgt"])
                    await asyncio.sleep(delay)
                    if candidate_pmids:
                        abstracts = await self._efetch_abstracts(http, candidate_pmids)
                        await asyncio.sleep(delay)
                        for pmid, abstract in abstracts.items():
                            if await self._is_relevant(abstract, edge["src"], edge["tgt"]):
                                validated.append(str(pmid))
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "CitationAgent: error citing %s->%s: %s",
                        edge["src"], edge["tgt"], exc,
                    )
                # Always mark attempted (even with 0 results) to avoid re-querying.
                await self._write_pmids(edge["eid"], validated)
                edges_processed += 1
                pmids_added += len(validated)

        summary = {"edges_processed": edges_processed, "pmids_added": pmids_added}
        await self.write_run_log_to_graph("CitationRun", summary)
        return summary

    async def recent_runs(self, limit: int = 10) -> list[dict]:
        query = """
        MATCH (n:CitationRun)
        RETURN properties(n) AS props
        ORDER BY n.run_timestamp DESC
        LIMIT $limit
        """
        async with get_session() as session:
            rows = await (await session.run(query, limit=limit)).data()
        return [r["props"] for r in rows]


citation_agent = CitationAgent()
