"""PubMed ingest for literature extraction (Feature 2, stage 1).

Two access patterns over the same E-utils endpoints:
  - **Delta / forward** (nightly): a bounded publication-date window.
  - **Backfill / backward** (historical): the same window machinery, walked toward a
    floor date. See ``extraction/cursor.py`` for the walk; this module just fetches.

``fetch_recent_pmids`` (``reldate``) is retained for the one-shot manual trigger, but
the cursor-driven pipeline uses ``count_pmids_in_range`` + ``fetch_pmids_in_range``,
which query a fixed [mindate, maxdate] window so progress is a persistable date, not a
moving relative window. The date field is ``EXTRACTION_DATE_TYPE`` (default ``edat``,
the PubMed entry date — a clean per-record partition; ``pdat`` piles year-only dates on
Jan 1 and would truncate the backfill). Abstracts only — PMC full text is out for the MVP.

Cost note: E-utils is free. This stage makes zero LLM calls — the ≥2-entity
co-mention gate (applied by the agent after dictionary matching) is what culls 99%
of sentences before any model sees them. Requests retry with backoff and honour a
429 ``Retry-After`` so a days-long backfill survives transient NCBI throttling.
"""

from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
# NCBI serves the first ≤9,999 esearch hits without the history server; the cursor's
# probe-then-shrink keeps a window under EXTRACTION_MAX_PMIDS_PER_CHUNK, so a plain
# retstart page loop suffices and we never need WebEnv (YAGNI).
_ESEARCH_HARD_CAP = 9999

# Sentence splitter: break on ., ! or ? followed by whitespace + a capital/digit,
# but not after a lowercase abbreviation dot (e.g. "e.g."). Deliberately simple — no
# nltk/scispaCy dependency (YAGNI for abstract-length text).
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_ABBREV = re.compile(r"\b(?:e\.g|i\.e|et al|vs|Fig|approx|cf|Dr|no)\.$", re.IGNORECASE)


def request_delay() -> float:
    """NCBI allows 3 req/s without a key, 10 req/s with one."""
    return 0.1 if settings.NCBI_API_KEY else 0.34


def _ncbi_params(**extra) -> dict:
    params = dict(extra)
    if settings.NCBI_API_KEY:
        params["api_key"] = settings.NCBI_API_KEY
    return params


async def _get_with_retry(http: httpx.AsyncClient, url: str, params: dict) -> httpx.Response:
    """GET with exponential backoff. Retries transient network / 5xx / 429 errors and
    honours a 429 ``Retry-After`` header; a 4xx other than 429 fails fast (a bad
    request won't fix itself). Raises the last error after the retry budget."""
    max_retries = settings.EXTRACTION_HTTP_MAX_RETRIES
    base = settings.EXTRACTION_HTTP_BACKOFF_S
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = await http.get(url, params=params)
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if (retry_after or "").isdigit() else base * (2 ** attempt)
                logger.warning("ingest: %s on %s, waiting %.1fs (attempt %d/%d)",
                               resp.status_code, url.rsplit("/", 1)[-1], wait, attempt + 1, max_retries + 1)
                last_exc = httpx.HTTPStatusError("throttled", request=resp.request, response=resp)
                if attempt < max_retries:
                    await asyncio.sleep(wait)
                    continue
            resp.raise_for_status()
            return resp
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last_exc = exc
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500 \
                    and exc.response.status_code != 429:
                raise  # deterministic client error — don't retry
            if attempt < max_retries:
                await asyncio.sleep(base * (2 ** attempt))
    assert last_exc is not None
    raise last_exc


async def fetch_recent_pmids(
    http: httpx.AsyncClient,
    term: str | None = None,
    days: int | None = None,
    retmax: int | None = None,
) -> list[str]:
    """PMIDs added within the last ``days`` matching the (broad) delta term.

    Retained for the one-shot manual trigger (``POST /admin/agents/extraction/run``).
    The cursor pipeline uses ``fetch_pmids_in_range`` instead. Uses the configured
    ``EXTRACTION_DATE_TYPE`` (default ``edat``) — same partition key as the cursor."""
    params = _ncbi_params(
        db="pubmed",
        term=term or settings.PUBMED_DELTA_TERM,
        reldate=days or settings.PUBMED_DELTA_DAYS,
        datetype=settings.EXTRACTION_DATE_TYPE,
        retmax=retmax or settings.PUBMED_DELTA_RETMAX,
        retmode="json",
    )
    resp = await _get_with_retry(http, _ESEARCH_URL, params)
    return resp.json().get("esearchresult", {}).get("idlist", [])


async def count_pmids_in_range(
    http: httpx.AsyncClient, mindate: str, maxdate: str, term: str | None = None
) -> int:
    """How many PMIDs match ``term`` with an entry date in [mindate, maxdate]
    (inclusive, ``YYYY/MM/DD`` or ``YYYY-MM-DD``, ``EXTRACTION_DATE_TYPE``). A cheap
    count-only esearch (``retmax=0``) so the cursor can probe-then-shrink a window
    before fetching."""
    params = _ncbi_params(
        db="pubmed", term=term or settings.PUBMED_DELTA_TERM,
        datetype=settings.EXTRACTION_DATE_TYPE, mindate=_slash(mindate), maxdate=_slash(maxdate),
        retmax=0, retmode="json",
    )
    resp = await _get_with_retry(http, _ESEARCH_URL, params)
    return int(resp.json().get("esearchresult", {}).get("count", 0))


async def fetch_pmids_in_range(
    http: httpx.AsyncClient, mindate: str, maxdate: str,
    term: str | None = None, delay: float = 0.0,
) -> list[str]:
    """All PMIDs matching ``term`` with an entry date (``EXTRACTION_DATE_TYPE``) in
    [mindate, maxdate].

    Since the caller keeps a window under EXTRACTION_MAX_PMIDS_PER_CHUNK (well below the
    no-history cap), esearch returns the whole window in ONE call (``retmax=total``); the
    ``retstart`` loop only matters at the cap. If a single window still exceeds the cap we
    fetch the cap's worth and log — an accepted, near-impossible edge for this corpus scope
    at a 1-day floor, rather than pulling in WebEnv/history-server complexity."""
    total = await count_pmids_in_range(http, mindate, maxdate, term)
    if total == 0:
        return []
    if total > _ESEARCH_HARD_CAP:
        logger.warning("ingest: window %s..%s has %d PMIDs > cap %d — truncating",
                       mindate, maxdate, total, _ESEARCH_HARD_CAP)
        total = _ESEARCH_HARD_CAP
    pmids: list[str] = []
    for retstart in range(0, total, _ESEARCH_HARD_CAP):
        params = _ncbi_params(
            db="pubmed", term=term or settings.PUBMED_DELTA_TERM,
            datetype=settings.EXTRACTION_DATE_TYPE, mindate=_slash(mindate), maxdate=_slash(maxdate),
            retstart=retstart, retmax=min(_ESEARCH_HARD_CAP, total - retstart), retmode="json",
        )
        resp = await _get_with_retry(http, _ESEARCH_URL, params)
        pmids.extend(resp.json().get("esearchresult", {}).get("idlist", []))
        if delay:
            await asyncio.sleep(delay)
    return pmids


def _slash(date: str) -> str:
    """E-utils wants ``YYYY/MM/DD``; accept ``YYYY-MM-DD`` too."""
    return date.replace("-", "/")


async def fetch_articles(http: httpx.AsyncClient, pmids: list[str]) -> dict[str, dict]:
    """efetch title + abstract for a batch of PMIDs -> {pmid: {title, abstract}}."""
    if not pmids:
        return {}
    params = _ncbi_params(
        db="pubmed", id=",".join(pmids), rettype="abstract", retmode="xml"
    )
    resp = await http.get(_EFETCH_URL, params=params)
    resp.raise_for_status()
    out: dict[str, dict] = {}
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as exc:
        logger.warning("ingest: efetch XML parse error: %s", exc)
        return out
    for article in root.findall(".//PubmedArticle"):
        pmid = article.findtext(".//PMID")
        if not pmid:
            continue
        title = (article.findtext(".//ArticleTitle") or "").strip()
        abstract = " ".join(
            (node.text or "") for node in article.findall(".//AbstractText")
        ).strip()
        out[pmid] = {"title": title, "abstract": abstract}
    return out


def split_sentences(text: str) -> list[str]:
    """Split abstract text into sentences (best-effort, dependency-free)."""
    if not text:
        return []
    # Re-join over known abbreviation dots so "e.g. X" doesn't split.
    parts = _SENT_SPLIT_RE.split(text)
    sentences: list[str] = []
    buf = ""
    for part in parts:
        candidate = f"{buf} {part}".strip() if buf else part
        if _ABBREV.search(part.strip()):
            buf = candidate  # abbreviation at end -> merge with next
            continue
        sentences.append(candidate)
        buf = ""
    if buf:
        sentences.append(buf)
    return [s.strip() for s in sentences if s.strip()]
