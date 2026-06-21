#!/usr/bin/env bash
# Pull the Neo4j+APOC Docker image and convert it to a Singularity SIF file.
# Run this on the HPC LOGIN NODE (has internet access) before starting an
# interactive compute job.
#
# Usage:
#   bash hpc/pull_and_convert.sh [IMAGE_SOURCE]
#
# IMAGE_SOURCE options:
#   docker://owardhan/project_omni:neo4j-5              (Docker Hub — default, APOC baked in)
#   docker://svlprhpcreg01.stjude.org/<proj>/omnigraph-neo4j:5  (Harbor, if pushed there)
#   docker://neo4j:5                                    (plain neo4j, no APOC baked in)
#
# Output: ~/omnigraph-neo4j.sif

set -euo pipefail

DEFAULT_IMAGE="docker://owardhan/project_omni:neo4j-5"
IMAGE="${1:-${DEFAULT_IMAGE}}"
OUTPUT="${HOME}/omnigraph-neo4j.sif"

echo "Pulling: ${IMAGE}"
echo "Output:  ${OUTPUT}"
echo ""

if [[ -f "${OUTPUT}" ]]; then
  echo "SIF already exists at ${OUTPUT}. Delete it first to re-pull."
  exit 0
fi

singularity pull "${OUTPUT}" "${IMAGE}"

echo ""
echo "Done. SIF written to ${OUTPUT}"
echo "Transfer it to compute nodes via the shared filesystem (it's already in \$HOME)."
echo ""
echo "If you pulled plain neo4j:5 (no APOC baked in), set this env var when running:"
echo "  NEO4J_PLUGINS='[\"apoc\"]'"
echo "  (Neo4j will download APOC on first startup — requires compute node internet)"
