"""etl/run_pipeline.py — DAG runner for the OmniGraph ETL pipeline.

Runs every ETL step in dependency order, importing each script's ``main()``
directly (no subprocess) and logging the outcome to a ``(:DataSource)`` node in
Neo4j. Steps whose blocking prerequisite failed *in this run* are skipped.

Usage:
    etl/.venv/bin/python etl/run_pipeline.py                  # full pipeline
    etl/.venv/bin/python etl/run_pipeline.py --from 07_string # resume from a step

IMPORTANT — from-empty bootstrap is not possible with the current scripts.
``04_dorothea`` matches ``(:Protein)`` as the REGULATES source while
``05_proteins`` derives its TF set from existing REGULATES edges, so on an empty
graph neither can seed the other (a pre-existing, out-of-scope ADR-0004 refactor
gap; see those modules' docstrings). On an ALREADY-POPULATED graph every step is
idempotent (MERGE / WHERE ... IS NULL), so this runner is for idempotent re-runs
and resumes, not cold builds.
"""

import argparse
import importlib.util
import sys
import time
from pathlib import Path

_ETL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_ETL_DIR))
from utils.neo4j_client import close_driver, get_session  # noqa: E402

# Load order (06_data_vision.md). Existing MVP topology first, then Phase 2.
ORDER = [
    "01_hgnc", "02_gencode", "03_gtex", "05_proteins", "04_dorothea",
    "06_uniprot_enrich", "07_string", "08_gwas", "09_clinvar",
    "10_ncbi_summaries", "11_gnomad",
]

# A step is aborted if a prerequisite that runs EARLIER in ORDER failed in this
# run. (The 05<->04 relationship is mutually circular in the current code and so
# is intentionally not encoded as a forward dependency — see the module note.)
BLOCKING: dict[str, set[str]] = {
    "06_uniprot_enrich": {"05_proteins"},
    "07_string": {"05_proteins"},
    "08_gwas": {"01_hgnc"},
    "09_clinvar": {"08_gwas"},
    "10_ncbi_summaries": {"01_hgnc"},
    "11_gnomad": {"01_hgnc"},
}


def _load_main(step: str):
    """Import an ETL module by file path (names start with digits) and return main."""
    spec = importlib.util.spec_from_file_location(step, _ETL_DIR / f"{step}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {step}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "main"):
        raise AttributeError(f"{step}.py has no main()")
    return module.main


def _log_datasource(step: str, status: str) -> None:
    with get_session() as session:
        session.run(
            "MERGE (d:DataSource {name: $name}) "
            "SET d.loaded_at = datetime(), d.status = $status, "
            "    d.pipeline = 'phase2'",
            name=step, status=status,
        ).consume()


def main() -> None:
    parser = argparse.ArgumentParser(description="OmniGraph ETL pipeline runner")
    parser.add_argument(
        "--from", dest="from_step", default=None,
        help="resume from this step (e.g. 07_string)",
    )
    args = parser.parse_args()

    steps = ORDER
    if args.from_step:
        if args.from_step not in ORDER:
            print(f"Unknown step '{args.from_step}'. Valid steps: {ORDER}")
            sys.exit(2)
        steps = ORDER[ORDER.index(args.from_step):]

    succeeded: set[str] = set()
    failed: set[str] = set()
    pipeline_start = time.time()

    for step in steps:
        blocked = BLOCKING.get(step, set()) & failed
        if blocked:
            print(f"\n=== SKIP {step}: blocking prerequisite failed: {sorted(blocked)} ===")
            failed.add(step)
            _log_datasource(step, "skipped")
            continue

        print(f"\n=== {step}  start {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
        step_start = time.time()
        try:
            _load_main(step)()
            succeeded.add(step)
            _log_datasource(step, "success")
            print(f"=== {step} OK ({time.time() - step_start:.1f}s) ===")
        except Exception as exc:  # noqa: BLE001 — one failed step must not kill the run
            failed.add(step)
            _log_datasource(step, "failed")
            print(f"=== {step} FAILED ({time.time() - step_start:.1f}s): {exc!r} ===")

    total = time.time() - pipeline_start
    print(
        f"\nPipeline finished in {total:.1f}s — "
        f"success={sorted(succeeded)} failed={sorted(failed)}"
    )
    close_driver()


if __name__ == "__main__":
    main()
