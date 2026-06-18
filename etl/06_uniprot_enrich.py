"""ETL 06 — Enrich existing Protein nodes with UniProt function + annotation.

Enrichment pattern (06_data_vision.md "Pattern 2 — REST API per entity): reads the
Protein nodes already in the graph (the TF slice minted by 05_proteins.py) and
calls the UniProt REST API once per accession to attach the free-text function
comment (for embedding), subcellular location, GO terms, molecular weight, and a
derived subtype. It NEVER mints new Protein nodes — topology comes from files, not
APIs.

UniProt JSON shape (rest.uniprot.org/uniprotkb/{accession}.json):
  - function text : comments[] where commentType == 'FUNCTION' -> texts[0].value
  - subcellular   : comments[] where commentType == 'SUBCELLULAR LOCATION'
                    -> subcellularLocations[0].location.value
  - go_terms      : uniProtKBCrossReferences[] where database == 'GO' -> id (GO:...)
  - molecular_wt  : sequence.molWeight (Daltons)
  - subtype       : derived from GO molecular-function ids (TF / kinase / ...)

Rate limit: UniProt free tier is ~1 request/second without a key, so we sleep 1s
between requests. ~117 TF proteins => ~2 minutes.

    etl/.venv/bin/python etl/06_uniprot_enrich.py
"""

import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.neo4j_client import close_driver, get_session  # noqa: E402

UNIPROT_URL = "https://rest.uniprot.org/uniprotkb/{accession}.json"
REQUEST_DELAY_S = 1.0  # UniProt free tier ~1 req/s
HTTP_TIMEOUT_S = 30.0
WRITE_BATCH_SIZE = 50

# GO molecular-function ids -> protein subtype, checked in priority order so a
# specific role (TF, kinase) wins over the broad "catalytic activity" fallback.
SUBTYPE_BY_GO = [
    ("GO:0003700", "transcription_factor"),  # DNA-binding transcription factor activity
    ("GO:0016301", "kinase"),                # kinase activity
    ("GO:0005198", "structural"),            # structural molecule activity
    ("GO:0003824", "enzyme"),                # catalytic activity (broad fallback)
]

MERGE_QUERY = """
UNWIND $rows AS row
MATCH (p:Protein {uniprot_id: row.uniprot_id})
SET p.summary_text = row.function_text,
    p.subcellular_loc = row.subcellular_loc,
    p.go_terms = row.go_terms,
    p.molecular_weight = row.molecular_weight,
    p.subtype = CASE WHEN p.subtype IS NOT NULL THEN p.subtype ELSE row.derived_subtype END
"""


def _function_text(result: dict) -> str | None:
    for c in result.get("comments", []):
        if c.get("commentType") == "FUNCTION":
            texts = c.get("texts") or []
            if texts and texts[0].get("value"):
                return texts[0]["value"]
    return None


def _subcellular_loc(result: dict) -> str | None:
    for c in result.get("comments", []):
        if c.get("commentType") == "SUBCELLULAR LOCATION":
            locs = c.get("subcellularLocations") or []
            if locs:
                value = (locs[0].get("location") or {}).get("value")
                if value:
                    return value
    return None


def _go_terms(result: dict) -> list[str]:
    go: list[str] = []
    for xref in result.get("uniProtKBCrossReferences", []):
        if xref.get("database") == "GO" and xref.get("id"):
            go.append(xref["id"])
    return go


def _molecular_weight(result: dict) -> int | None:
    return (result.get("sequence") or {}).get("molWeight")


def _derived_subtype(go_terms: list[str]) -> str | None:
    go_set = set(go_terms)
    for go_id, subtype in SUBTYPE_BY_GO:
        if go_id in go_set:
            return subtype
    return None


def fetch_uniprot(http: httpx.Client, accession: str) -> dict | None:
    """Return parsed UniProt JSON for an accession, or None on any error."""
    try:
        resp = http.get(
            UNIPROT_URL.format(accession=accession),
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            print(f"  [skip] {accession}: HTTP {resp.status_code}")
            return None
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        print(f"  [skip] {accession}: {exc}")
        return None


def main() -> None:
    start = time.time()

    with get_session() as session:
        accessions = [
            r["u"]
            for r in session.run(
                "MATCH (p:Protein) WHERE p.summary_text IS NULL "
                "AND p.uniprot_id IS NOT NULL "
                "RETURN p.uniprot_id AS u"
            ).data()
        ]
        print(f"Proteins needing enrichment: {len(accessions)}")

        enriched = 0
        no_function = 0
        errored = 0
        batch: list[dict] = []

        def flush(rows: list[dict]) -> None:
            if rows:
                session.run(MERGE_QUERY, rows=rows).consume()

        with httpx.Client(timeout=HTTP_TIMEOUT_S) as http:
            for i, accession in enumerate(accessions, 1):
                result = fetch_uniprot(http, accession)
                time.sleep(REQUEST_DELAY_S)
                if result is None:
                    errored += 1
                    continue

                function_text = _function_text(result)
                go_terms = _go_terms(result)
                if function_text is None:
                    no_function += 1
                else:
                    enriched += 1

                batch.append(
                    {
                        "uniprot_id": accession,
                        "function_text": function_text,
                        "subcellular_loc": _subcellular_loc(result),
                        "go_terms": go_terms,
                        "molecular_weight": _molecular_weight(result),
                        "derived_subtype": _derived_subtype(go_terms),
                    }
                )
                if len(batch) >= WRITE_BATCH_SIZE:
                    flush(batch)
                    batch = []
                if i % 25 == 0:
                    print(f"  ...{i}/{len(accessions)} fetched")

        flush(batch)

    elapsed = time.time() - start
    print(f"Proteins enriched (function text set): {enriched}")
    print(f"Proteins with no function text: {no_function}")
    print(f"Proteins skipped (HTTP/parse error): {errored}")
    print(f"Time elapsed: {elapsed:.1f}s")
    close_driver()


if __name__ == "__main__":
    main()
