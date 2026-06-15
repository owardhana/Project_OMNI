#!/usr/bin/env bash
#
# Download all raw source files for the OmniGraph ETL pipeline into data/raw/.
# Idempotent: files already present (non-empty) are skipped. Re-run safely.
#
# Sources:
#   HGNC          gene symbols + Ensembl ID mapping
#   GENCODE v46   gene + transcript structure (GTF)
#   GTEx v10      tissue median TPM (GCT)
#   DoRothEA      TF -> target regulons with confidence tiers

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAW_DIR="${SCRIPT_DIR}/../data/raw"
mkdir -p "${RAW_DIR}"

# name|url
#
# NOTE on URLs (verified 2026-06-15, see docs/adr/0003-data-source-urls.md):
#   - HGNC moved off the EBI FTP path to Google Cloud Storage.
#   - DoRothEA no longer ships a CSV; only an .rda (R data) is published. We
#     download the .rda and read it in Python via pyreadr in 04_dorothea.py.
SOURCES=(
  "hgnc_complete_set.txt|https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt"
  "gencode.v46.annotation.gtf.gz|https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_46/gencode.v46.annotation.gtf.gz"
  "GTEx_Analysis_v10_RNASeQCv2.4.2_gene_median_tpm.gct.gz|https://storage.googleapis.com/adult-gtex/bulk-gex/v10/rna-seq/GTEx_Analysis_v10_RNASeQCv2.4.2_gene_median_tpm.gct.gz"
  "dorothea_hs.rda|https://raw.githubusercontent.com/saezlab/dorothea/master/data/dorothea_hs.rda"
)

download_one() {
  local name="$1" url="$2" dest="${RAW_DIR}/$1"
  if [[ -s "${dest}" ]]; then
    echo "[skip] ${name} already present ($(du -h "${dest}" | cut -f1))"
    return 0
  fi
  echo "[download] ${name}"
  echo "           <- ${url}"
  # Download to a temp file, then atomically move on success so an interrupted
  # download never leaves a partial file that the skip-check would accept.
  local tmp="${dest}.part"
  if curl -fL --retry 3 --retry-delay 5 --progress-bar -o "${tmp}" "${url}"; then
    mv "${tmp}" "${dest}"
    echo "[done] ${name} ($(du -h "${dest}" | cut -f1))"
  else
    rm -f "${tmp}"
    echo "[ERROR] failed to download ${name} from ${url}" >&2
    return 1
  fi
}

echo "Downloading raw sources to ${RAW_DIR}"
failures=0
for entry in "${SOURCES[@]}"; do
  name="${entry%%|*}"
  url="${entry#*|}"
  # Attempt every source even if one fails, then report at the end.
  download_one "${name}" "${url}" || failures=$((failures + 1))
done

if [[ "${failures}" -gt 0 ]]; then
  echo "${failures} download(s) failed — see errors above." >&2
  exit 1
fi
echo "All downloads complete."
