#!/usr/bin/env bash
# Start Neo4j via Singularity on an HPC compute node (OnDemand interactive job).
#
# Usage:
#   bash hpc/start_neo4j.sh [SIF_PATH]
#
# SIF_PATH defaults to ~/omnigraph-neo4j.sif.
# Data directory defaults to ~/neo4j_data (created if absent).
# Override via env vars NEO4J_SIF, NEO4J_DATA_DIR, NEO4J_PASSWORD.
#
# On Linux HPC (unlike macOS Docker Desktop) bind mounts work fine for Neo4j.

set -euo pipefail

SIF="${NEO4J_SIF:-${HOME}/omnigraph-neo4j.sif}"
DATA_DIR="${NEO4J_DATA_DIR:-${HOME}/neo4j_data}"
PASSWORD="${NEO4J_PASSWORD:-changeme}"

# HPC nodes often have large RAM — give Neo4j more than the laptop defaults.
HEAP_MAX="${NEO4J_HEAP_MAX:-8G}"
PAGECACHE="${NEO4J_PAGECACHE:-8G}"

echo "=== OmniGraph Neo4j startup ==="
echo "  SIF      : ${SIF}"
echo "  data dir : ${DATA_DIR}"
echo "  heap     : ${HEAP_MAX}  pagecache: ${PAGECACHE}"
echo "  Bolt     : bolt://localhost:7687"
echo "  Browser  : http://localhost:7474"
echo ""

if [[ ! -f "${SIF}" ]]; then
  echo "ERROR: SIF not found at ${SIF}"
  echo "Pull it first:"
  echo "  singularity pull ${SIF} docker://owardhan/project_omni:neo4j-5"
  echo "  # or from St. Jude Harbor (if you pushed there):"
  echo "  singularity pull ${SIF} docker://svlprhpcreg01.stjude.org/<project>/omnigraph-neo4j:5"
  exit 1
fi

mkdir -p "${DATA_DIR}"

# Singularity runs as the current user (no root). Neo4j's entrypoint needs
# to write to /data (bound from DATA_DIR) and /logs. We bind /logs to a
# subdirectory of DATA_DIR so everything stays in one place.
mkdir -p "${DATA_DIR}/logs"

singularity run \
  --bind "${DATA_DIR}:/data" \
  --bind "${DATA_DIR}/logs:/logs" \
  --env NEO4J_AUTH="neo4j/${PASSWORD}" \
  --env NEO4J_server_memory_heap_initial__size="2G" \
  --env NEO4J_server_memory_heap_max__size="${HEAP_MAX}" \
  --env NEO4J_server_memory_pagecache_size="${PAGECACHE}" \
  --env NEO4J_dbms_security_procedures_unrestricted="apoc.*" \
  --env NEO4J_dbms_security_procedures_allowlist="apoc.*" \
  "${SIF}"
