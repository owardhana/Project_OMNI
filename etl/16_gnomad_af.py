"""ETL 16 — Enrich existing Variant nodes with gnomAD allele frequency.

Enrichment pattern (docs/data-architecture.md "Pattern 2 — REST API per entity"), but
*batched*: reads the rs-variants already in the graph (minted by 08_gwas / matched by
09_clinvar) and calls the Ensembl Variation REST API to attach the gnomAD population
allele frequency. It NEVER mints Variant nodes — topology comes from files, not APIs.

This is deliberately a SEPARATE script from 11_gnomad: 11 is a Pattern-1 bulk-file
*gene*-level constraint (pLI) enrich that only needs 01_hgnc; this is a Pattern-2
*variant*-level API crawl that needs the variants to exist first (08_gwas / 09_clinvar).

Why Ensembl, not the gnomAD VCF/GraphQL: our Variant nodes carry only ``rsid`` (+
chromosome/position) — no ref/alt alleles — so a per-variant key is required, and the
full gnomAD sites VCF is hundreds of GB. Ensembl REST resolves rsIDs to gnomAD pop AF
directly, 200 ids per POST.

Ensembl response shape (POST /variation/homo_sapiens?pops=1), keyed by rsid:
  - mappings[].allele_string : "REF/ALT" (the alt is everything after the first "/")
  - populations[]            : {population, allele, frequency, allele_count}
                               gnomAD names: "gnomADg:ALL" (genomes) / "gnomADe:ALL"
                               (exomes), each with one entry per allele.
We set ``Variant.gnomad_af`` = the summed non-reference allele frequency in the chosen
population (= the alt AF for a biallelic site; robust to multiallelic), preferring
genomes (gnomADg) over exomes (gnomADe). ``Variant.gnomad_source`` records which.

Only rs-variants are enrichable. The ``chr:pos:NA:NA`` fallback variants (08_gwas, no
rsid) carry no rsid Ensembl can resolve and are reported as un-enrichable, not failed.

Resumable: only variants with ``gnomad_af IS NULL`` are fetched, so a re-run continues
where a crash left off (same discipline as 06_uniprot_enrich).

Rate limit: Ensembl REST allows ~15 req/s and returns HTTP 429 + Retry-After when
exceeded; we honour Retry-After and pause GNOMAD_AF_REQUEST_DELAY_S between batches.
Batch / delay are env-tunable (never hardcode thresholds — project rule).

    etl/.venv/bin/python etl/16_gnomad_af.py
"""

import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.neo4j_client import close_driver, get_session  # noqa: E402

ENSEMBL_URL = "https://rest.ensembl.org/variation/homo_sapiens?pops=1"
# Ensembl POST caps at 200 ids/request, but pops=1 returns ~148 populations PER
# variant, so a 200-id batch is a multi-MB response that exceeds a 60s read (measured).
# 50 ids ~= 19s and resolves ~100% of gnomAD ALL frequencies — the practical sweet
# spot. The crawl is a long (~hours) resumable backfill regardless; batch/delay/timeout
# are env-tunable (never hardcode thresholds — project rule).
BATCH_SIZE = int(os.getenv("GNOMAD_AF_BATCH_SIZE", "50"))
REQUEST_DELAY_S = float(os.getenv("GNOMAD_AF_REQUEST_DELAY_S", "0.1"))
HTTP_TIMEOUT_S = float(os.getenv("GNOMAD_AF_TIMEOUT_S", "90"))
# Flush to Neo4j often (every ~10 Ensembl batches) so a long backfill that is
# interrupted loses little — it resumes from gnomad_af IS NULL.
WRITE_BATCH_SIZE = 500
MAX_RETRIES = 4

# gnomAD population preference: genomes first (broader, less ascertainment bias than
# the exome capture), exomes as fallback.
_POP_PREFERENCE = [("gnomADg:ALL", "genomes"), ("gnomADe:ALL", "exomes")]

SET_QUERY = """
UNWIND $rows AS r
MATCH (v:Variant {rsid: r.rsid})
SET v.gnomad_af = r.af, v.gnomad_source = r.source
RETURN count(v) AS c
"""


def _alt_frequency(variant: dict) -> tuple[float, str] | None:
    """(alt_allele_freq, source_label) from a single Ensembl variation record, or None.

    alt AF = sum of non-reference allele frequencies in the preferred gnomAD population
    (biallelic -> the alt AF; multiallelic -> total non-ref AF). Prefers genomes.
    """
    mappings = variant.get("mappings") or []
    if not mappings:
        return None
    ref_allele = str(mappings[0].get("allele_string", "")).split("/")[0]
    if not ref_allele:
        return None

    pops = variant.get("populations") or []
    for pop_name, label in _POP_PREFERENCE:
        entries = [p for p in pops if p.get("population") == pop_name]
        if not entries:
            continue
        non_ref = sum(
            float(p["frequency"])
            for p in entries
            if p.get("allele") != ref_allele and p.get("frequency") is not None
        )
        return round(min(1.0, non_ref), 6), label
    return None


def fetch_batch(http: httpx.Client, rsids: list[str]) -> dict | None:
    """POST a batch of rsIDs to Ensembl, honouring 429 Retry-After. None on hard error."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = http.post(
                ENSEMBL_URL,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                json={"ids": rsids},
            )
        except httpx.HTTPError as exc:
            print(f"  [retry {attempt + 1}] network error: {exc}")
            time.sleep(2 * (attempt + 1))
            continue
        if resp.status_code == 429:
            wait = float(resp.headers.get("Retry-After", "2"))
            print(f"  [429] rate-limited; sleeping {wait}s")
            time.sleep(wait + 0.5)
            continue
        if resp.status_code != 200:
            print(f"  [skip] batch HTTP {resp.status_code}")
            return None
        try:
            return resp.json()
        except ValueError as exc:
            print(f"  [skip] batch parse error: {exc}")
            return None
    print("  [skip] batch exhausted retries")
    return None


def main() -> None:
    start = time.time()

    with get_session() as session:
        all_rs = {
            r["rsid"]
            for r in session.run(
                "MATCH (v:Variant) WHERE v.rsid STARTS WITH 'rs' RETURN v.rsid AS rsid"
            ).data()
        }
        total_variants = session.run(
            "MATCH (v:Variant) RETURN count(v) AS c"
        ).single()["c"]
        # ClinVar-significant variants first, so the highest-value AFs land early in
        # this long (~hours) backfill; the rest follow. (Resumable, so order only
        # affects which AFs appear first, not correctness.)
        todo = [
            r["rsid"]
            for r in session.run(
                "MATCH (v:Variant) WHERE v.rsid STARTS WITH 'rs' "
                "AND v.gnomad_af IS NULL "
                "RETURN v.rsid AS rsid "
                "ORDER BY CASE WHEN v.clinical_significance IS NOT NULL THEN 0 ELSE 1 END"
            ).data()
        ]
        non_rs = total_variants - len(all_rs)
        print(f"Total Variant nodes: {total_variants}")
        print(f"  rs-variants (enrichable): {len(all_rs)}")
        print(f"  non-rs variants (chr:pos fallback — not enrichable): {non_rs}")
        print(f"  rs-variants still needing gnomad_af: {len(todo)}")

        enriched = 0
        no_af = 0
        errored = 0
        batch_rows: list[dict] = []

        def flush(rows: list[dict]) -> None:
            if rows:
                session.run(SET_QUERY, rows=rows).consume()

        with httpx.Client(timeout=HTTP_TIMEOUT_S) as http:
            for i in range(0, len(todo), BATCH_SIZE):
                rsids = todo[i : i + BATCH_SIZE]
                data = fetch_batch(http, rsids)
                time.sleep(REQUEST_DELAY_S)
                if data is None:
                    errored += len(rsids)
                    continue
                for rsid in rsids:
                    rec = data.get(rsid)
                    if not rec:
                        no_af += 1  # rsid not resolved by Ensembl
                        continue
                    af = _alt_frequency(rec)
                    if af is None:
                        no_af += 1  # resolved but no gnomAD population data
                        continue
                    batch_rows.append({"rsid": rsid, "af": af[0], "source": af[1]})
                    enriched += 1
                if len(batch_rows) >= WRITE_BATCH_SIZE:
                    flush(batch_rows)
                    batch_rows = []
                done = min(i + BATCH_SIZE, len(todo))
                if (i // BATCH_SIZE) % 25 == 0:
                    print(f"  ...{done}/{len(todo)} rsIDs queried "
                          f"(enriched={enriched}, no_af={no_af}, errored={errored})")

        flush(batch_rows)

        session.run(
            "MERGE (ds:DataSource {name: $name}) "
            "SET ds.loaded_at = datetime(), ds.source_db = 'gnomAD_via_Ensembl_REST', "
            "    ds.endpoint = $endpoint, ds.rs_variants = $rs, "
            "    ds.enriched = $enriched, ds.no_af = $no_af, ds.errored = $errored",
            name="16_gnomad_af", endpoint=ENSEMBL_URL, rs=len(all_rs),
            enriched=enriched, no_af=no_af, errored=errored,
        ).consume()

    elapsed = time.time() - start
    print(f"Variants enriched with gnomad_af: {enriched}")
    print(f"rs-variants with no gnomAD AF (unresolved / not in gnomAD): {no_af}")
    print(f"rs-variants skipped (HTTP/parse error — re-run to retry): {errored}")
    print(f"Time elapsed: {elapsed:.1f}s")
    close_driver()


if __name__ == "__main__":
    main()
