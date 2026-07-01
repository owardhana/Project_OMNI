#!/usr/bin/env bash
# Dump the LOCAL Neo4j graph to dumps/neo4j.dump for transfer to the server.
# Neo4j Community dumps OFFLINE, so the running container is stopped for the dump
# and restarted after. Run this on your laptop (dev machine).
#
#   bash scripts/dump_graph.sh
#
# Then copy the dump up:  scp dumps/neo4j.dump ubuntu@<VM_IP>:~/Project_OMNI/dumps/
set -euo pipefail
cd "$(dirname "$0")/.."

# Volume name = "<compose-project>_neo4j_data"; project defaults to the repo dir name
# lower-cased (Project_OMNI -> project_omni). Override with NEO4J_VOLUME if different
# (find it with: docker volume ls | grep neo4j).
VOLUME="${NEO4J_VOLUME:-project_omni_neo4j_data}"
mkdir -p dumps

echo "==> Stopping local Neo4j (offline dump needs it stopped)…"
docker compose stop neo4j 2>/dev/null || docker stop omnigraph-neo4j 2>/dev/null || true

echo "==> Dumping database 'neo4j' from volume ${VOLUME}…"
docker run --rm \
  -v "${VOLUME}:/data" \
  -v "$(pwd)/dumps:/dumps" \
  neo4j:5 \
  neo4j-admin database dump neo4j --to-path=/dumps --overwrite-destination

echo "==> Restarting local Neo4j…"
docker compose start neo4j 2>/dev/null || docker start omnigraph-neo4j 2>/dev/null || true

echo ""
echo "Done -> dumps/neo4j.dump ($(du -h dumps/neo4j.dump 2>/dev/null | cut -f1))"
echo "Next:  scp dumps/neo4j.dump ubuntu@<VM_IP>:~/Project_OMNI/dumps/"
