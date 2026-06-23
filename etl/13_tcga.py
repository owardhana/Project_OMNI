"""ETL 13 — TCGA differential expression: DIFFERENTIALLY_EXPRESSED edges.

Topology from bulk files (docs/data-architecture.md Pattern 1 / docs/data-architecture.md row
12). Computes a per-gene, per-tumor-type differential-expression signal from the
UCSC Xena / Toil TCGA Pan-Cancer RSEM FPKM matrix and MERGEs:

  (:Gene {ensembl_id})-[:DIFFERENTIALLY_EXPRESSED {tumor_type}]->(:Disease {ontology_id})
      log2fc, direction ("up"/"down"), source_db, source_version, loaded_at

DESIGN (matched-normal, not a tissue proxy)
The Toil matrix values are already log2(fpkm+0.001), so a fold change is just a
difference of medians. For each cohort we split its samples into tumour
(TCGA sample_type_id 01-09) and adjacent solid-tissue NORMAL (10-19) using the
phenotype file, and compute:

    log2fc = median_tumour(log2fpkm) - median_normal(log2fpkm)

This replaces the earlier GTEx-whole-blood "proxy normal", which was dimensionally
inconsistent (a blood tissue-weight is not an expression baseline for solid
tumours). Cohorts without >= TCGA_MIN_NORMALS adjacent normals are SKIPPED — we do
not invent a baseline. This is still a simplified signal (KNOWN RISKS,
docs/data-architecture.md: real DE needs DESeq2/edgeR on counts), but it is a
genuine tumour-vs-normal contrast. The signed log2fc semantics are unchanged, so
no backend/conductance change is required.

Cohort -> disease ontology id comes from the curated, graph-verified crosswalk
etl/reference/tcga_disease_to_efo.tsv (see its header for why the raw Open Targets
cttv acronym map could not be used directly). Both endpoints must already exist —
genes from 01_hgnc, Disease nodes from 07_efo/08_gwas. Genes/diseases absent from
the graph are skipped and counted (never created here).

Format discipline (ADR-0003): every input file is checked for a usable set of
columns and the script aborts (printing the columns it DID find) rather than
silently mis-parsing the (release-variable) Xena layout.

    etl/.venv/bin/python etl/13_tcga.py
"""

import os
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.neo4j_client import close_driver, get_session  # noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_RAW = _PROJECT_ROOT / "data" / "raw"
EXPR_FILE = _RAW / "tcga_RSEM_gene_fpkm.gz"
PHENO_FILE = _RAW / "TCGA_phenotype_denseDataOnlyDownload.tsv.gz"
CROSSWALK_FILE = Path(__file__).resolve().parent / "reference" / "tcga_disease_to_efo.tsv"

EDGE_BATCH = 2000
SOURCE_DB = "TCGA_XENA"
SOURCE_VERSION = "toil_rsem_fpkm"

# The Xena phenotype layout has changed across releases; accept the first present
# of each candidate set, abort if none match (ADR-0003 discipline).
SAMPLE_COL_CANDIDATES = ["sample", "sampleID", "submitter_id.samples", "bcr_sample_barcode"]
TYPE_COL_CANDIDATES = [
    "_primary_disease", "cancer type abbreviation", "cancer_type_abbreviation",
    "project_id", "disease_code", "acronym",
]
# Sample-type id (01=primary tumour ... 11=solid normal). Falls back to parsing the
# barcode suffix if the column is absent.
SAMPLE_TYPE_COL_CANDIDATES = ["sample_type_id", "sample_type.samples"]


def _strip_ensembl_version(eid: str) -> str:
    return eid.split(".")[0] if isinstance(eid, str) else eid


def _first_present(columns, candidates):
    for c in candidates:
        if c in columns:
            return c
    return None


def _sample_type_id(barcode: str, explicit) -> int | None:
    """Return the integer TCGA sample-type id (1..29), from the explicit phenotype
    column when present, else parsed from the barcode 4th field (TCGA-XX-YYYY-01)."""
    if explicit is not None and str(explicit).strip() not in ("", "nan"):
        try:
            return int(float(str(explicit).strip()))
        except ValueError:
            pass
    if isinstance(barcode, str):
        parts = barcode.split("-")
        if len(parts) >= 4:
            digits = "".join(ch for ch in parts[3] if ch.isdigit())
            if digits:
                return int(digits[:2])
    return None


def _load_crosswalk() -> dict[str, str]:
    """{lower(primary_disease) -> ontology_id} from the curated graph-verified TSV."""
    if not CROSSWALK_FILE.exists():
        print(f"ABORT: crosswalk {CROSSWALK_FILE} not found.")
        sys.exit(1)
    df = pd.read_csv(CROSSWALK_FILE, sep="\t", dtype=str, comment="#")
    required = {"primary_disease", "ontology_id"}
    if not required.issubset(df.columns):
        print(f"ABORT: crosswalk missing columns {required - set(df.columns)}.")
        print(f"Columns present: {list(df.columns)}")
        sys.exit(1)
    mapping: dict[str, str] = {}
    for name, oid in zip(df["primary_disease"], df["ontology_id"]):
        if isinstance(name, str) and isinstance(oid, str) and name.strip() and oid.strip():
            mapping[name.strip().lower()] = oid.strip()
    return mapping


def _crosswalk_codes() -> dict[str, str]:
    """{lower(primary_disease) -> tcga_code} for the DIFFERENTIALLY_EXPRESSED.tumor_type key."""
    df = pd.read_csv(CROSSWALK_FILE, sep="\t", dtype=str, comment="#")
    out: dict[str, str] = {}
    if "tcga_code" in df.columns:
        for name, code in zip(df["primary_disease"], df["tcga_code"]):
            if isinstance(name, str) and isinstance(code, str) and name.strip() and code.strip():
                out[name.strip().lower()] = code.strip()
    return out


def _graph_gene_ids() -> set[str]:
    with get_session() as session:
        rows = session.run("MATCH (g:Gene) RETURN g.ensembl_id AS eid").data()
    return {_strip_ensembl_version(r["eid"]) for r in rows if r["eid"]}


def _graph_disease_ids() -> set[str]:
    with get_session() as session:
        rows = session.run("MATCH (d:Disease) RETURN d.ontology_id AS oid").data()
    return {r["oid"] for r in rows if r["oid"]}


MERGE_QUERY = """
UNWIND $rows AS row
MATCH (g:Gene {ensembl_id: row.ensembl_id})
MATCH (d:Disease {ontology_id: row.efo_id})
MERGE (g)-[r:DIFFERENTIALLY_EXPRESSED {tumor_type: row.tumor_type}]->(d)
SET r.log2fc = row.log2fc,
    r.direction = row.direction,
    r.n_tumor = row.n_tumor,
    r.n_normal = row.n_normal,
    r.source_db = $source_db,
    r.source_version = $source_version,
    r.loaded_at = timestamp()
"""


def main() -> None:
    start = time.time()
    for f in (EXPR_FILE, PHENO_FILE, CROSSWALK_FILE):
        if not f.exists():
            raise FileNotFoundError(f"{f} not found. Run etl/00_download.sh first.")

    threshold = float(os.getenv("TCGA_MIN_LOG2FC", "1.0"))
    min_tumors = int(os.getenv("TCGA_MIN_TUMORS", "10"))
    min_normals = int(os.getenv("TCGA_MIN_NORMALS", "10"))
    print(f"TCGA |log2FC| threshold: >= {threshold}; "
          f"min tumours {min_tumors}, min normals {min_normals}")

    # --- 1. phenotype: sample -> (cohort name, sample-type id) ---
    pheno = pd.read_csv(PHENO_FILE, sep="\t", dtype=str, low_memory=False)
    sample_col = _first_present(pheno.columns, SAMPLE_COL_CANDIDATES)
    type_col = _first_present(pheno.columns, TYPE_COL_CANDIDATES)
    sttype_col = _first_present(pheno.columns, SAMPLE_TYPE_COL_CANDIDATES)
    if sample_col is None or type_col is None:
        print("ABORT: TCGA phenotype missing a usable sample/disease column.")
        print(f"  sample candidates {SAMPLE_COL_CANDIDATES} -> {sample_col}")
        print(f"  disease candidates {TYPE_COL_CANDIDATES} -> {type_col}")
        print(f"Columns present: {list(pheno.columns)}")
        sys.exit(1)

    crosswalk = _load_crosswalk()
    codes = _crosswalk_codes()
    print(f"Crosswalk cohorts: {len(crosswalk)} "
          f"(cols: sample='{sample_col}', disease='{type_col}', "
          f"sample_type='{sttype_col}')")

    # sample -> (ontology_id, tcga_code, type_id)
    sample_info: dict[str, tuple[str, str, int]] = {}
    unmapped_names: set[str] = set()
    for _, prow in pheno.iterrows():
        sid = prow[sample_col]
        name = prow[type_col]
        if not isinstance(sid, str) or not sid.strip() or not isinstance(name, str):
            continue
        key = name.strip().lower()
        oid = crosswalk.get(key)
        if oid is None:
            unmapped_names.add(name.strip())
            continue
        tid = _sample_type_id(sid, prow[sttype_col] if sttype_col else None)
        if tid is None:
            continue
        sample_info[sid.strip()] = (oid, codes.get(key, key.upper()), tid)
    if unmapped_names:
        print(f"Phenotype disease names not in crosswalk (skipped): "
              f"{sorted(unmapped_names)}")

    # --- 2. expression matrix (rows=genes, cols=samples), read as float32 ---
    header_cols = pd.read_csv(EXPR_FILE, sep="\t", nrows=0, index_col=0).columns
    dtypes = {c: "float32" for c in header_cols}
    expr = pd.read_csv(EXPR_FILE, sep="\t", index_col=0, dtype=dtypes, low_memory=False)
    print(f"Expression matrix shape (genes x samples): {expr.shape}")
    expr.index = [_strip_ensembl_version(g) for g in expr.index]

    graph_genes = _graph_gene_ids()
    graph_diseases = _graph_disease_ids()
    print(f"Graph: {len(graph_genes)} genes, {len(graph_diseases)} diseases")

    # group expression columns by cohort -> {ontology_id, code, tumour cols, normal cols}
    cohorts: dict[str, dict] = {}
    for col in expr.columns:
        info = sample_info.get(col)
        if not info:
            continue
        oid, code, tid = info
        c = cohorts.setdefault(code, {"oid": oid, "tumor": [], "normal": []})
        if 1 <= tid <= 9:
            c["tumor"].append(col)
        elif 10 <= tid <= 19:
            c["normal"].append(col)

    edges: list[dict] = []
    per_type_counts: dict[str, int] = {}
    skipped_genes = 0
    skipped_no_normal: list[str] = []
    skipped_disease_absent: list[str] = []

    for code, c in sorted(cohorts.items()):
        oid = c["oid"]
        nt, nn = len(c["tumor"]), len(c["normal"])
        if oid not in graph_diseases:
            skipped_disease_absent.append(f"{code}({oid})")
            continue
        if nt < min_tumors or nn < min_normals:
            skipped_no_normal.append(f"{code}(t={nt},n={nn})")
            continue
        tumor_median = expr[c["tumor"]].median(axis=1, skipna=True)
        normal_median = expr[c["normal"]].median(axis=1, skipna=True)
        log2fc = (tumor_median - normal_median)
        n_edges = 0
        for eid, fc in log2fc.items():
            if eid not in graph_genes:
                skipped_genes += 1
                continue
            if pd.isna(fc) or abs(float(fc)) < threshold:
                continue
            edges.append({
                "ensembl_id": eid,
                "efo_id": oid,
                "tumor_type": code,
                "log2fc": round(float(fc), 4),
                "direction": "up" if fc > 0 else "down",
                "n_tumor": nt,
                "n_normal": nn,
            })
            n_edges += 1
        per_type_counts[code] = n_edges
        print(f"  {code}: {nt} tumour / {nn} normal -> {n_edges} edges (disease {oid})")

    if skipped_no_normal:
        print(f"Cohorts skipped (too few tumour/normal): {skipped_no_normal}")
    if skipped_disease_absent:
        print(f"Cohorts skipped (disease absent from graph): {skipped_disease_absent}")
    print(f"Total DIFFERENTIALLY_EXPRESSED edges to write: {len(edges)}")

    with get_session() as session:
        for i in range(0, len(edges), EDGE_BATCH):
            session.run(
                MERGE_QUERY, rows=edges[i : i + EDGE_BATCH],
                source_db=SOURCE_DB, source_version=SOURCE_VERSION,
            ).consume()
        session.run(
            "MERGE (ds:DataSource {name: $name}) "
            "SET ds.loaded_at = datetime(), ds.source_db = $source_db, "
            "    ds.source_version = $source_version, "
            "    ds.edges_written = $edges, ds.per_tumor_type = $per_type",
            name="13_tcga", source_db=SOURCE_DB, source_version=SOURCE_VERSION,
            edges=len(edges),
            per_type=[f"{k}={v}" for k, v in sorted(per_type_counts.items())],
        ).consume()

    elapsed = time.time() - start
    print(f"DIFFERENTIALLY_EXPRESSED edges merged: {len(edges)}")
    print(f"Time elapsed: {elapsed:.1f}s")
    close_driver()


if __name__ == "__main__":
    main()
