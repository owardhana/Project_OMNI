#!/usr/bin/env bash
# One-time HPC environment setup for OmniGraph.
# Run this once after cloning the repo onto HPC.
# Safe to re-run — pip install and downloads are idempotent.
#
# Usage (from repo root):
#   bash hpc/setup_hpc.sh
#
# What this does:
#   1. Creates a Python virtual env at .venv/ (uses system Python 3.11+)
#   2. Installs backend + ETL dependencies
#   3. Copies .env.example -> .env if no .env exists
#   4. Downloads raw source files (public datasets) into data/raw/
#   5. Prints next steps

set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== OmniGraph HPC setup ==="
echo "  repo: ${REPO}"

# ── 1. Python venv ─────────────────────────────────────────────────────────
cd "${REPO}"
if [[ ! -d .venv ]]; then
  echo "[1/4] Creating Python virtual environment (.venv/)..."
  # HPC clusters often have multiple Python versions via modules.
  # Load Python 3.11+ if needed: module load python/3.11
  python3 -m venv .venv
fi
source .venv/bin/activate
echo "[1/4] Python: $(python3 --version) at $(which python3)"

# ── 2. Install dependencies ────────────────────────────────────────────────
echo "[2/4] Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r backend/requirements.txt
pip install --quiet -r etl/requirements.txt 2>/dev/null || true   # ETL may share backend reqs

echo "[2/4] Done."

# ── 3. .env ───────────────────────────────────────────────────────────────
if [[ ! -f .env ]]; then
  echo "[3/4] No .env found — copying from .env.example. Edit it before running ETL."
  cp .env.example .env
  # Override the Neo4j URI for HPC (Neo4j runs on the same node, same localhost).
  # Change NEO4J_PASSWORD to match what you passed to start_neo4j.sh.
  echo ""
  echo "  >>> EDIT .env — set OPENROUTER_API_KEY and NEO4J_PASSWORD"
  echo ""
else
  echo "[3/4] .env exists — skipping."
fi

# ── 4. Download raw data ───────────────────────────────────────────────────
echo "[4/4] Downloading raw source files (public datasets)..."
echo "      This requires outbound internet from the compute node."
echo "      If blocked, download on the login node instead and rsync here."
bash etl/00_download.sh

echo ""
echo "=== Setup complete. Next steps: ==="
echo "  1. In a separate terminal/window: bash hpc/start_neo4j.sh"
echo "  2. Wait ~30 seconds for Neo4j to start."
echo "  3. Run ETL:    source .venv/bin/activate && PYTHONPATH=. python etl/run_pipeline.py"
echo "  4. Run tests:  source .venv/bin/activate && PYTHONPATH=. pytest backend/tests/ -v"
echo "  5. Run API:    source .venv/bin/activate && PYTHONPATH=. uvicorn backend.main:app --reload"
