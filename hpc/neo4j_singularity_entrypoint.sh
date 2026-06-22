#!/usr/bin/env bash
# OmniGraph — Neo4j entrypoint for HPC Singularity.
#
# Singularity auto-binds $HOME from the HPC host filesystem, so writing data
# to $HOME/neo4j_data makes it persistent across container sessions.
# The original Docker image stored data at /data (container-local, lost on exit).
#
# This script is set as ENTRYPOINT in hpc/Dockerfile.neo4j-apoc.
# It sets env vars, creates the data dir, then hands off to the real
# Neo4j Docker entrypoint so all normal Neo4j startup logic still runs.

set -euo pipefail

# Data directory: persist in $HOME so Singularity's auto-bind keeps it alive.
# Override with NEO4J_DATA env var if you want a different location.
NEO4J_DATA="${NEO4J_DATA:-${HOME}/neo4j_data}"
mkdir -p "${NEO4J_DATA}"

# HPC nodes typically have 128-512GB RAM. Default to 8G each; override via env.
export NEO4J_server_memory_heap_max__size="${NEO4J_HEAP_MAX:-8G}"
export NEO4J_server_memory_pagecache_size="${NEO4J_PAGECACHE:-8G}"

# Tell Neo4j to use our persistent data directory.
export NEO4J_server_directories_data="${NEO4J_DATA}"

# Auth: neo4j/<password>. Must match NEO4J_PASSWORD in .env.
export NEO4J_AUTH="${NEO4J_AUTH:-neo4j/changeme}"

# APOC already allowed via image ENV; repeat here for clarity.
export NEO4J_dbms_security_procedures_unrestricted="apoc.*"
export NEO4J_dbms_security_procedures_allowlist="apoc.*"

echo "=== OmniGraph Neo4j (HPC Singularity) ==="
echo "  Data dir : ${NEO4J_DATA}"
echo "  Heap     : ${NEO4J_server_memory_heap_max__size}"
echo "  Pagecache: ${NEO4J_server_memory_pagecache_size}"
echo "  Auth     : ${NEO4J_AUTH%%/*} / <password>"
echo "  Bolt     : bolt://localhost:7687"
echo "  Browser  : http://localhost:7474"
echo ""

# Hand off to the real Neo4j Docker entrypoint so all startup logic runs.
exec /sbin/tini -g -- /startup/docker-entrypoint.sh neo4j
