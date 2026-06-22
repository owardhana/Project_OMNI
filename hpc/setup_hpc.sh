#!/usr/bin/env bash
# One-time HPC environment setup for OmniGraph.
# Run this once after cloning the repo onto HPC.
# Safe to re-run — pip install and downloads are idempotent.
#
# Usage (from repo root):
#   bash hpc/setup_hpc.sh
#
# IMPORTANT — load a modern Python before running this script:
#   module load python/3.11     (or python/3.10, python/3.9 — check with: module avail python)
#   bash hpc/setup_hpc.sh
#
# neo4j Python driver 5.x requires Python >= 3.8.
# The HPC default Python is often 3.6 or older; the module system has newer versions.

set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── 0. Python version gate ─────────────────────────────────────────────────
PY_VERSION="$(python3 -c 'import sys; print(sys.version_info[:2])')"
PY_MAJOR="$(python3 -c 'import sys; print(sys.version_info.major)')"
PY_MINOR="$(python3 -c 'import sys; print(sys.version_info.minor)')"

echo "=== OmniGraph HPC setup ==="
echo "  repo   : ${REPO}"
echo "  python : $(python3 --version) at $(which python3)"

if [[ "${PY_MAJOR}" -lt 3 ]] || { [[ "${PY_MAJOR}" -eq 3 ]] && [[ "${PY_MINOR}" -lt 8 ]]; }; then
  echo ""
  echo "ERROR: Python 3.8+ required (neo4j driver 5.x). Found: $(python3 --version)"
  echo ""
  echo "Fix — load a newer Python module first, then re-run:"
  echo ""
  echo "  module avail python          # list available versions"
  echo "  module load python/3.11      # load 3.11 (adjust to what's available)"
  echo "  bash hpc/setup_hpc.sh        # re-run this script"
  echo ""
  echo "At St. Jude HPC, common module names include:"
  echo "  python/3.11  python/3.10  Python/3.11.0  conda/24  miniconda3"
  echo "Check with: module spider python"
  exit 1
fi

echo "  ✓ Python ${PY_MAJOR}.${PY_MINOR} — OK"

# ── 1. Python venv ─────────────────────────────────────────────────────────
cd "${REPO}"
if [[ ! -d .venv ]]; then
  echo ""
  echo "[1/4] Creating Python virtual environment (.venv/)..."
  python3 -m venv .venv
else
  echo ""
  echo "[1/4] .venv already exists — skipping creation."
fi
source .venv/bin/activate
echo "      Active: $(python3 --version) at $(which python3)"

# ── 2. Install dependencies ────────────────────────────────────────────────
echo ""
echo "[2/4] Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r backend/requirements.txt
pip install --quiet -r etl/requirements.txt
echo "      Done."

# ── 3. .env ───────────────────────────────────────────────────────────────
echo ""
if [[ ! -f .env ]]; then
  echo "[3/4] No .env found — copying from .env.example."
  cp .env.example .env
  echo ""
  echo "  ┌─────────────────────────────────────────────────────────────┐"
  echo "  │  ACTION REQUIRED: edit .env before running ETL              │"
  echo "  │    OPENROUTER_API_KEY=sk-or-...                             │"
  echo "  │    NEO4J_PASSWORD=changeme   (match container NEO4J_AUTH)   │"
  echo "  │    NEO4J_URI=bolt://localhost:7687  (no change needed)      │"
  echo "  └─────────────────────────────────────────────────────────────┘"
else
  echo "[3/4] .env exists — skipping."
fi

# ── 4. Download raw data ───────────────────────────────────────────────────
echo ""
echo "[4/4] Downloading raw source files (public datasets)..."
echo "      Requires outbound internet from this node."
echo "      If blocked here, run on the login node instead:"
echo "        bash etl/00_download.sh"
bash etl/00_download.sh

echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  Setup complete. Next steps:                                    ║"
echo "║                                                                 ║"
echo "║  1. Launch Neo4j via OnDemand (leave Command blank):            ║"
echo "║     URL: docker://owardhan/project_omni:neo4j-5                 ║"
echo "║                                                                 ║"
echo "║  2. Wait ~30 sec, then in this terminal:                        ║"
echo "║     curl http://localhost:7474   # should return JSON           ║"
echo "║                                                                 ║"
echo "║  3. Run ETL (builds graph from raw data):                       ║"
echo "║     source .venv/bin/activate                                   ║"
echo "║     PYTHONPATH=. python etl/run_pipeline.py                     ║"
echo "║                                                                 ║"
echo "║  4. Run test suite:                                             ║"
echo "║     PYTHONPATH=. pytest backend/tests/ -v                       ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
