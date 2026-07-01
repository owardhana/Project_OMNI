#!/usr/bin/env bash
# Load dumps/neo4j.dump into the PROD Neo4j volume. Run this ON THE SERVER after
# copying the dump up. Overwrites the target database, so bring the stack up once
# first (to create the volume), then run this.
#
#   docker compose -f docker-compose.prod.yml --env-file deploy/.env.prod up -d neo4j
#   bash scripts/restore_graph.sh
set -euo pipefail
cd "$(dirname "$0")/.."

VOLUME="${NEO4J_VOLUME:-project_omni_neo4j_data}"   # see note in dump_graph.sh
[ -f dumps/neo4j.dump ] || { echo "ERROR: dumps/neo4j.dump not found (scp it here first)"; exit 1; }

echo "==> Stopping prod Neo4j (offline load)…"
docker compose -f docker-compose.prod.yml stop neo4j 2>/dev/null || docker stop omnigraph-neo4j 2>/dev/null || true

echo "==> Loading database 'neo4j' into volume ${VOLUME} (overwrite)…"
docker run --rm \
  -v "${VOLUME}:/data" \
  -v "$(pwd)/dumps:/dumps" \
  neo4j:5 \
  neo4j-admin database load neo4j --from-path=/dumps --overwrite-destination

echo "==> Starting prod Neo4j…"
docker compose -f docker-compose.prod.yml --env-file deploy/.env.prod start neo4j

echo ""
echo "Done. Verify once it's healthy:"
echo "  docker exec omnigraph-neo4j cypher-shell -u neo4j -p <PASSWORD> 'MATCH (n) RETURN count(n)'"
