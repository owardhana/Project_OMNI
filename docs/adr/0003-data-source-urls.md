# ADR 0003 — Data source URLs, formats, and the REGULATES count

Status: Accepted (2026-06-15)

## Context

The build spec's download URLs and some format assumptions were stale. Verified
against live sources on 2026-06-15.

### HGNC
- Spec URL `https://ftp.ebi.ac.uk/pub/databases/genenames/hgnc/tsv/hgnc_complete_set.txt`
  now returns **404**.
- Working URL: `https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt`
- The file has a `location` column (e.g. `17p13.1`), **not** a `chromosome`
  column. `01_hgnc.py` derives chromosome from `location` (`17p13.1` -> `17`,
  `Xq28` -> `X`, mitochondrial -> `MT`).

### GENCODE v46 — unchanged
- `https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_46/gencode.v46.annotation.gtf.gz` (200).

### GTEx v10 — URL unchanged, **column names differ**
- The median-TPM GCT uses underscored column names, not the spec's pretty labels:
  - `Whole Blood` -> **`Whole_Blood`**
  - `Liver` -> `Liver` (unchanged)
  - `Brain - Frontal Cortex (BA9)` -> **`Brain_Frontal_Cortex_BA9`**
- `GTEX_COLUMN_MAP` in `03_gtex.py` uses the real names; the script still aborts
  (does not guess) if an expected column is absent.

### DoRothEA — CSV gone, only `.rda`
- Spec URL `.../inst/extdata/dorothea_hs.csv` returns **404**; that path no longer
  holds CSVs.
- Only `data/dorothea_hs.rda` is published
  (`https://raw.githubusercontent.com/saezlab/dorothea/master/data/dorothea_hs.rda`).
- We read it with **pyreadr** (pure Python, no R). Real columns are
  `tf / confidence / target / mor` — there is **no `likelihood` column** (the
  spec listed one). `mode` is derived from the sign of `mor`
  (>0 activator, <0 repressor, 0 unknown).

## Decision

- Use the working URLs above (encoded in `etl/00_download.sh`).
- Read DoRothEA from the `.rda` via pyreadr; `04_dorothea.py` also accepts a
  `dorothea_hs.csv` if one is ever present.

## The REGULATES count (>30k gate is miscalibrated)

DoRothEA high-confidence tier sizes in this release:

| Tiers     | Edges  |
|-----------|--------|
| A+B       | 6,408  |
| A+B+C     | 13,223 |
| A+B+C+D   | 29,086 |

**No principled cutoff reaches the spec's `>30,000` (or 02_mvp's `>50,000`)**, so
that gate is a spec error, not an ETL bug. 02_mvp.md Known Risks already
pre-authorizes tier expansion ("Add B-tier, lower threshold if needed").

We **ship A-B** because the benchmark genes have ample regulators under A-B:
TP53 = 20, MYC = 38, EGFR = 17 (all ≥ 3), and A-B matches the hardcoded
`confidence_tier IN ['A','B']` in the Text2Cypher examples. Tiers remain
configurable via `DOROTHEA_MIN_CONFIDENCE`. **If that env is ever widened, the
query/prompt `confidence_tier` filters must be driven from the same setting** —
otherwise the extra tiers load as invisible dead data.

The real, achievable gates remain: Gene > 40k, Transcript > 200k, PRODUCES > 200k.
