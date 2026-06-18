"""ETL 10 — Enrich Gene nodes with NCBI Gene functional summaries.

Enrichment pattern (06_data_vision.md Pattern 2). For Gene nodes that have no
``summary_text``, resolve the Entrez gene id from the HGNC file (ensembl_gene_id
-> entrez_id), fetch the paragraph-length NCBI Gene summary in batches via the
E-utilities ``esummary`` endpoint, and SET it back keyed by ensembl_id. It never
mints Gene nodes.

Requests use POST (E-utilities supports it and NCBI recommends POST for batched
id lists, avoiding URL-length limits with 500 ids per call). Rate limit: 3 req/s
without an NCBI key, 10 req/s with one (NCBI_API_KEY env var).

    etl/.venv/bin/python etl/10_ncbi_summaries.py
"""

import os
import sys
import time
from pathlib import Path

import httpx
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.id_mapper import strip_version  # noqa: E402
from utils.neo4j_client import close_driver, get_session  # noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
HGNC_FILE = _PROJECT_ROOT / "data" / "raw" / "hgnc_complete_set.txt"
ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
FETCH_BATCH = 500
WRITE_BATCH = 1000
HTTP_TIMEOUT_S = 60.0

SET_QUERY = """
UNWIND $rows AS r
MATCH (g:Gene {ensembl_id: r.ensembl_id})
SET g.summary_text = r.summary
RETURN count(g) AS c
"""


def load_ensembl_to_entrez() -> dict[str, str]:
    df = pd.read_csv(
        HGNC_FILE, sep="\t", dtype=str,
        usecols=["ensembl_gene_id", "entrez_id"], low_memory=False,
    )
    df = df.dropna(subset=["ensembl_gene_id", "entrez_id"])
    out: dict[str, str] = {}
    for ensembl, entrez in zip(df["ensembl_gene_id"], df["entrez_id"]):
        ensembl = strip_version(ensembl.strip())
        entrez = entrez.strip().split(".")[0]  # "7157.0" -> "7157"
        if ensembl and entrez:
            out[ensembl] = entrez
    return out


def fetch_summaries(http: httpx.Client, entrez_ids: list[str], api_key: str) -> dict[str, str]:
    """entrez_id -> summary text for one batch, or {} on error."""
    data = {"db": "gene", "id": ",".join(entrez_ids), "retmode": "json"}
    if api_key:
        data["api_key"] = api_key
    # Retry transient failures (E-utilities returns sporadic 502/429 under load);
    # without this a single blip silently drops a whole 500-gene batch.
    result = None
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = http.post(ESUMMARY_URL, data=data)
            resp.raise_for_status()
            result = resp.json().get("result", {})
            break
        except (httpx.HTTPError, ValueError) as exc:
            last_exc = exc
            time.sleep(1.0 * (attempt + 1))
    if result is None:
        print(f"  [skip batch after 3 retries] {last_exc}")
        return {}
    out: dict[str, str] = {}
    for uid in result.get("uids", []):
        summary = (result.get(uid) or {}).get("summary")
        if isinstance(summary, str) and summary.strip():
            out[uid] = summary.strip()
    return out


def main() -> None:
    start = time.time()
    if not HGNC_FILE.exists():
        raise FileNotFoundError(f"{HGNC_FILE} not found. Run etl/00_download.sh first.")

    api_key = os.getenv("NCBI_API_KEY", "").strip()
    delay = 0.1 if api_key else 0.34  # 10 req/s with key, 3 req/s without
    ensembl_to_entrez = load_ensembl_to_entrez()

    with get_session() as session:
        genes = [
            r["e"]
            for r in session.run(
                "MATCH (g:Gene) WHERE g.summary_text IS NULL AND g.ensembl_id IS NOT NULL "
                "RETURN g.ensembl_id AS e"
            ).data()
        ]
        print(f"Genes needing a summary: {len(genes)}")

        # ensembl_id -> entrez, and the reverse for writing summaries back.
        pairs: list[tuple[str, str]] = []
        no_entrez = 0
        for ensembl in genes:
            entrez = ensembl_to_entrez.get(strip_version(ensembl))
            if entrez:
                pairs.append((ensembl, entrez))
            else:
                no_entrez += 1
        entrez_to_ensembls: dict[str, list[str]] = {}
        for ensembl, entrez in pairs:
            entrez_to_ensembls.setdefault(entrez, []).append(ensembl)
        unique_entrez = list(entrez_to_ensembls)
        print(f"Resolved to Entrez: {len(pairs)}  (no Entrez id: {no_entrez})")

        entrez_to_summary: dict[str, str] = {}
        with httpx.Client(timeout=HTTP_TIMEOUT_S) as http:
            for i in range(0, len(unique_entrez), FETCH_BATCH):
                batch = unique_entrez[i : i + FETCH_BATCH]
                entrez_to_summary.update(fetch_summaries(http, batch, api_key))
                time.sleep(delay)
                if (i // FETCH_BATCH + 1) % 20 == 0:
                    print(f"  ...{i + len(batch)}/{len(unique_entrez)} entrez fetched")

        rows = [
            {"ensembl_id": ensembl, "summary": entrez_to_summary[entrez]}
            for entrez, ensembls in entrez_to_ensembls.items()
            if entrez in entrez_to_summary
            for ensembl in ensembls
        ]
        enriched = 0
        for i in range(0, len(rows), WRITE_BATCH):
            rec = session.run(SET_QUERY, rows=rows[i : i + WRITE_BATCH]).single()
            enriched += rec["c"] if rec else 0

    elapsed = time.time() - start
    print(f"Genes enriched with summary_text: {enriched}")
    print(f"Genes with no Entrez id: {no_entrez}")
    print(f"Genes with no NCBI summary: {len(pairs) - enriched}")
    print(f"Time elapsed: {elapsed:.1f}s")
    close_driver()


if __name__ == "__main__":
    main()
