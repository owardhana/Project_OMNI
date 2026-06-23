#!/usr/bin/env bash
#
# Download all raw source files for the OmniGraph ETL pipeline into data/raw/.
# Idempotent: files already present (non-empty) are skipped. Re-run safely.
#
# Sources:
#   HGNC          gene symbols + Ensembl ID mapping (+ uniprot_ids)
#   GENCODE v46   gene + transcript structure (GTF)
#   GENCODE v46   SwissProt metadata: transcript (ENST) -> UniProt (ADR-0004)
#   GTEx v10      tissue median TPM (GCT)
#   DoRothEA      TF -> target regulons with confidence tiers
#   --- Phase 2 (docs/data-architecture.md) ---
#   STRING v12    protein-protein interactions (INTERACTS_WITH edges)
#   GWAS Catalog  variant-trait associations (Variant + Disease nodes)
#   ClinVar       variant clinical significance enrichment
#   gnomAD v4     gene loss-of-function constraint (pLI)
#   EFO           disease/phenotype ontology
#   --- Phase 3 (docs/data-architecture.md) ---
#   TCGA Xena     pan-cancer FPKM expression + phenotype (DIFFERENTIALLY_EXPRESSED)
#   COSMIC CGC    Cancer Gene Census v99 (cancer_gene / cosmic_tier flags)
#   cttv mappings TCGA cancer code -> EFO ontology id
#   Recon3D       human metabolic reconstruction SBML (Metabolite + CATALYSES)
#   HMDB          metabolite identifiers (hmdb_id / chebi_id / name lookup)

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
  "gencode.v46.metadata.SwissProt.gz|https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_46/gencode.v46.metadata.SwissProt.gz"
  "GTEx_Analysis_v10_RNASeQCv2.4.2_gene_median_tpm.gct.gz|https://storage.googleapis.com/adult-gtex/bulk-gex/v10/rna-seq/GTEx_Analysis_v10_RNASeQCv2.4.2_gene_median_tpm.gct.gz"
  "dorothea_hs.rda|https://raw.githubusercontent.com/saezlab/dorothea/master/data/dorothea_hs.rda"
  # --- Phase 2 sources (docs/data-architecture.md). Same curl + skip-if-present pattern. ---
  "9606.protein.links.detailed.v12.0.txt.gz|https://stringdb-downloads.org/download/protein.links.detailed.v12.0/9606.protein.links.detailed.v12.0.txt.gz"
  "gwas-catalog-associations_ontology-annotated-full.zip|https://ftp.ebi.ac.uk/pub/databases/gwas/releases/latest/gwas-catalog-associations_ontology-annotated-full.zip"
  "ClinVarVariantSummary.txt.gz|https://ftp.ncbi.nlm.nih.gov/pub/clinvar/tab_delimited/variant_summary.txt.gz"
  "gnomad_v4_constraint.tsv|https://storage.googleapis.com/gcp-public-data--gnomad/release/4.1/constraint/gnomad.v4.1.constraint_metrics.tsv"
  "efo.json|https://github.com/EBISPOT/efo/releases/latest/download/efo.json"
  # --- Phase 3 sources (docs/data-architecture.md). Same curl + skip-if-present pattern. ---
  # TCGA Pan-Cancer gene expression + phenotype (UCSC Xena / Toil public hubs, no auth).
  # CONSUMED by 13_tcga.py. The matrix is Toil RSEM FPKM, values already log2(fpkm+0.001);
  # the phenotype's `_primary_disease` joins to the curated crosswalk (see below).
  "tcga_RSEM_gene_fpkm.gz|https://toil-xena-hub.s3.us-east-1.amazonaws.com/download/tcga_RSEM_gene_fpkm.gz"
  "TCGA_phenotype_denseDataOnlyDownload.tsv.gz|https://tcga-pancan-atlas-hub.s3.us-east-1.amazonaws.com/download/TCGA_phenotype_denseDataOnlyDownload.tsv.gz"
  # COSMIC Cancer Gene Census. Requires a free Sanger account (the unauthenticated
  # endpoint returns an HTML login page), so download it MANUALLY from the COSMIC site
  # into data/raw/. 12_cosmic.py auto-detects whichever form lands: the v104 tar
  # (Cosmic_CancerGeneCensus_Tsv_*.tar -> *.tsv.gz with GENE_SYMBOL/TIER), a plain
  # *.tsv.gz, or a legacy cosmic_cancer_gene_census.csv. URL kept as source documentation.
  "cosmic_cancer_gene_census.csv|https://cancer.sanger.ac.uk/cosmic/file_download/GRCh38/cosmic/v99/cancer_gene_census.csv"
  # TCGA cancer type -> EFO mapping (Open Targets archive). Kept as the documented
  # upstream provenance of etl/reference/tcga_disease_to_efo.tsv; 13_tcga.py reads the
  # curated graph-verified crosswalk, not this file directly (see crosswalk header).
  "cancer2EFO_mappings.tsv|https://raw.githubusercontent.com/opentargets-archive/evidence_datasource_parsers/master/resources/cancer2EFO_mappings.tsv"
  # Recon3D — CONSUMED by 14_metabolomics.py. The vmh.life 3D.01 zip ships a MATLAB
  # COBRA model (.mat), which 14_metabolomics.py reads via scipy.io.loadmat (NOT SBML).
  # NOTE: CATALYSES (Protein->Metabolite) is gated on the proteome already in the graph
  # — with a partial proteome (Protein=117) the metabolite layer loads but is sparsely
  # connected until the full proteome is loaded (05_proteins/06_uniprot_enrich).
  "Recon3D_301.zip|https://www.vmh.life/files/reconstructions/Recon/3D.01/Recon3D_301.zip"
  # HMDB metabolite identifiers (zip; ~6.4GB unzipped). Used by 14_metabolomics.py to
  # fill canonical name/inchikey for HMDB-keyed metabolites (streamed, never extracted).
  "hmdb_metabolites.zip|https://hmdb.ca/system/downloads/current/hmdb_metabolites.zip"
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
