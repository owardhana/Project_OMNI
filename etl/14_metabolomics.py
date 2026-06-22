"""ETL 14 — Metabolomics: Metabolite nodes + CATALYSES edges from Recon3D (ADR-0009).

Topology from a bulk file (06_data_vision.md Pattern 1 / 09_data_catalog.md rows
13-14). Parses the Recon3D human metabolic reconstruction COBRA model
(``Recon3D_301.mat``, read with scipy.io.loadmat) to extract:
  - (:Metabolite {hmdb_id|chebi_id, name, formula, charge, inchikey})  from `mets`
  - (:Protein {uniprot_id})-[:CATALYSES {role, reaction_id}]->(:Metabolite)
        role = "substrate" (reactant, S<0) | "product" (S>0)

The distributed Recon3D archive ships MATLAB ``.mat`` (a COBRA struct), NOT SBML, so
this reads the model directly: `mets`/`metNames`/`metFormulas`/`metHMDBID`/
`metCHEBIID`/`metCharges`/`metInChIString`, the `rxnGeneMat` (reaction×gene) and the
`S` stoichiometric matrix (metabolite×reaction; sign gives reactant/product).

Gene mapping: Recon3D genes are Entrez ids (e.g. "8639.1"); graph Gene nodes carry
no Entrez id, so we crosswalk Entrez→Ensembl via the HGNC file, then Ensembl→UniProt
via the existing graph topology (ENCODES / PRODUCES+TRANSLATES_TO). Proteins absent
from the graph are skipped — never created here.

⚠ The CATALYSES edges depend on the proteome already in the graph. If Protein is
small (the MVP loaded only ~117), CATALYSES will be SPARSE and the metabolite layer
will be largely DISCONNECTED — the metabolite NODES still load (they do not depend on
CATALYSES), but signal-decay traversal cannot flow into them until the full proteome
is loaded (05_proteins/06_uniprot_enrich). The run prints the actual CATALYSES count.

HMDB enrichment (optional): if ``hmdb_metabolites.zip`` is present, its XML is streamed
(iterparse) to fill canonical name / inchikey for the HMDB-keyed metabolites. Recon3D
ids are old-form (HMDB00015); HMDB uses HMDB0000015 + secondary_accessions, so both
are normalised to the integer id before joining.

METABOLOMICS_MIN_REACTIONS env var (default 1) drops metabolites participating in
fewer than N reactions (counted from S, intrinsic to the model — not CATALYSES).

Requires scipy (etl/requirements.txt).

    etl/.venv/bin/python etl/14_metabolomics.py
"""

import os
import re
import sys
import time
import io
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.neo4j_client import close_driver, get_session  # noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = _PROJECT_ROOT / "data" / "raw"
RECON_CANDIDATES = ["Recon3D*.zip", "Recon3D*.mat"]
HMDB_ZIP = RAW_DIR / "hmdb_metabolites.zip"
HGNC_FILE = RAW_DIR / "hgnc_complete_set.txt"
NODE_BATCH = 5000
EDGE_BATCH = 2000
GENE_LOOKUP_BATCH = 500
SOURCE_DB = "Recon3D"
SOURCE_VERSION = "3.01_mat"

_HMDB_NUM_RE = re.compile(r"HMDB0*(\d+)", re.IGNORECASE)
_CHEBI_RE = re.compile(r"(\d+)")
_COMPARTMENT_RE = re.compile(r"\[[a-z]\]$")
_HMDB_NS = "{http://www.hmdb.ca}"


# --- value normalisation -----------------------------------------------------

def _cell_str(x) -> str:
    """A Recon3D cell value -> stripped str ('' when empty)."""
    if isinstance(x, np.ndarray):
        if x.size == 0:
            return ""
        x = x.item() if x.size == 1 else x.flat[0]
    if x is None:
        return ""
    s = str(x).strip()
    return "" if s.lower() == "nan" else s


def _norm_hmdb(raw: str) -> str | None:
    m = _HMDB_NUM_RE.search(raw or "")
    return f"HMDB{int(m.group(1)):07d}" if m else None


def _norm_chebi(raw: str) -> str | None:
    if not raw:
        return None
    m = _CHEBI_RE.search(raw)
    return f"CHEBI:{m.group(1)}" if m else None


def _hmdb_int(hmdb_id: str) -> int | None:
    m = _HMDB_NUM_RE.search(hmdb_id or "")
    return int(m.group(1)) if m else None


# --- inputs ------------------------------------------------------------------

def _resolve_recon() -> Path:
    for pat in RECON_CANDIDATES:
        hits = sorted(RAW_DIR.glob(pat))
        if hits:
            return hits[-1]
    raise FileNotFoundError(f"No Recon3D file in {RAW_DIR} (looked for {RECON_CANDIDATES}).")


def _load_recon_model(path: Path):
    import scipy.io as sio  # local import: scipy is only needed here
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            members = [n for n in zf.namelist() if n.lower().endswith(".mat")]
            # prefer the full model (Recon3D_301.mat) over the reduced Model file
            members.sort(key=lambda n: ("model" in n.lower(), len(n)))
            if not members:
                print(f"ABORT: no .mat inside {path.name}: {zf.namelist()}")
                sys.exit(1)
            buf = io.BytesIO(zf.read(members[0]))
            print(f"Recon3D: reading {members[0]} from {path.name}")
            mat = sio.loadmat(buf, squeeze_me=True, struct_as_record=False)
    else:
        print(f"Recon3D: reading {path.name}")
        mat = sio.loadmat(str(path), squeeze_me=True, struct_as_record=False)
    var = next(k for k in mat if not k.startswith("__"))
    return mat[var]


def _load_entrez_to_ensembl() -> dict[str, str]:
    """{entrez_id (str) -> ensembl_gene_id} from the HGNC complete set."""
    if not HGNC_FILE.exists():
        print(f"ABORT: {HGNC_FILE} missing (needed for Entrez->Ensembl crosswalk).")
        sys.exit(1)
    df = pd.read_csv(HGNC_FILE, sep="\t", dtype=str, low_memory=False,
                     usecols=lambda c: c in ("entrez_id", "ensembl_gene_id"))
    out: dict[str, str] = {}
    for ent, ens in zip(df.get("entrez_id", []), df.get("ensembl_gene_id", [])):
        if isinstance(ent, str) and isinstance(ens, str) and ent.strip() and ens.strip():
            out[ent.split(".")[0].strip()] = ens.strip()
    return out


# --- graph helpers (reused) --------------------------------------------------

def _map_genes_to_uniprot(ensembl_ids: set[str]) -> dict[str, str]:
    """{ensembl_id -> uniprot_id} via existing graph topology, batched (one per gene)."""
    query = """
    UNWIND $eids AS eid
    CALL {
      WITH eid
      MATCH (g:Gene {ensembl_id: eid})-[:ENCODES|PRODUCES|TRANSLATES_TO*1..2]->(p:Protein)
      RETURN p.uniprot_id AS uniprot_id LIMIT 1
    }
    RETURN eid AS ensembl_id, uniprot_id
    """
    mapping: dict[str, str] = {}
    ids = list(ensembl_ids)
    with get_session() as session:
        for i in range(0, len(ids), GENE_LOOKUP_BATCH):
            for row in session.run(query, eids=ids[i : i + GENE_LOOKUP_BATCH]).data():
                if row["uniprot_id"]:
                    mapping[row["ensembl_id"]] = row["uniprot_id"]
    return mapping


# --- HMDB streaming enrichment ----------------------------------------------

def _stream_hmdb(needed_ids: set[int]) -> dict[int, dict]:
    """{int hmdb id -> {name, formula, inchikey}} for the needed ids only, by
    streaming the (multi-GB) HMDB XML with iterparse + root.clear() (bounded mem)."""
    if not HMDB_ZIP.exists() or not needed_ids:
        return {}
    out: dict[int, dict] = {}
    with zipfile.ZipFile(HMDB_ZIP) as zf:
        xml_name = next((n for n in zf.namelist() if n.lower().endswith(".xml")), None)
        if xml_name is None:
            return {}
        with zf.open(xml_name) as fh:
            context = ET.iterparse(fh, events=("start", "end"))
            _, root = next(context)
            for event, elem in context:
                if event != "end" or elem.tag != _HMDB_NS + "metabolite":
                    continue
                ids: set[int] = set()
                a = elem.find(_HMDB_NS + "accession")
                if a is not None and a.text:
                    n = _hmdb_int(a.text)
                    if n is not None:
                        ids.add(n)
                sec = elem.find(_HMDB_NS + "secondary_accessions")
                if sec is not None:
                    for x in sec.findall(_HMDB_NS + "accession"):
                        n = _hmdb_int(x.text or "")
                        if n is not None:
                            ids.add(n)
                hit = ids & needed_ids
                if hit:
                    def _txt(tag):
                        e = elem.find(_HMDB_NS + tag)
                        return e.text.strip() if e is not None and e.text else None
                    rec = {"name": _txt("name"), "formula": _txt("chemical_formula"),
                           "inchikey": _txt("inchikey")}
                    for n in hit:
                        out[n] = rec
                elem.clear()
                root.clear()
    return out


# --- writers -----------------------------------------------------------------

_MERGE_BY_HMDB = """
UNWIND $rows AS m
MERGE (met:Metabolite {hmdb_id: m.hmdb_id})
  ON CREATE SET met.created_at = timestamp()
SET met.chebi_id = m.chebi_id, met.name = m.name, met.formula = m.formula,
    met.charge = m.charge, met.inchikey = m.inchikey,
    met.node_type = 'metabolite', met.layer_z = 900,
    met.source_db = $source_db, met.source_version = $source_version
"""
_MERGE_BY_CHEBI = """
UNWIND $rows AS m
MERGE (met:Metabolite {chebi_id: m.chebi_id})
  ON CREATE SET met.created_at = timestamp()
SET met.hmdb_id = m.hmdb_id, met.name = m.name, met.formula = m.formula,
    met.charge = m.charge, met.inchikey = m.inchikey,
    met.node_type = 'metabolite', met.layer_z = 900,
    met.source_db = $source_db, met.source_version = $source_version
"""


def _write_metabolites(session, rows: list[dict]) -> None:
    hmdb_rows = [r for r in rows if r["key_field"] == "hmdb_id"]
    chebi_rows = [r for r in rows if r["key_field"] == "chebi_id"]
    for query, batch in ((_MERGE_BY_HMDB, hmdb_rows), (_MERGE_BY_CHEBI, chebi_rows)):
        for i in range(0, len(batch), NODE_BATCH):
            session.run(query, rows=batch[i : i + NODE_BATCH],
                        source_db=SOURCE_DB, source_version=SOURCE_VERSION).consume()


def _write_catalyses(session, rows: list[dict]) -> None:
    query = """
    UNWIND $rows AS e
    MATCH (p:Protein {uniprot_id: e.uniprot_id})
    MATCH (met:Metabolite)
      WHERE (e.key_field = 'hmdb_id'  AND met.hmdb_id  = e.met_key)
         OR (e.key_field = 'chebi_id' AND met.chebi_id = e.met_key)
    MERGE (p)-[r:CATALYSES {role: e.role, reaction_id: e.rxn_id}]->(met)
      ON CREATE SET r.source_db = $source_db, r.source_version = $source_version
    """
    for i in range(0, len(rows), EDGE_BATCH):
        session.run(query, rows=rows[i : i + EDGE_BATCH],
                    source_db=SOURCE_DB, source_version=SOURCE_VERSION).consume()


def _apply_min_reactions(session, met_reaction_count: dict[str, int]) -> int:
    """Delete loaded metabolites whose intrinsic S-matrix reaction participation is
    below METABOLOMICS_MIN_REACTIONS (default 1 keeps all)."""
    min_reactions = int(os.getenv("METABOLOMICS_MIN_REACTIONS", "1"))
    if min_reactions <= 1:
        return 0
    low = [k for k, n in met_reaction_count.items() if n < min_reactions]
    if not low:
        return 0
    rec = session.run(
        "UNWIND $keys AS k MATCH (m:Metabolite) "
        "WHERE m.hmdb_id = k OR m.chebi_id = k DETACH DELETE m RETURN count(m) AS c",
        keys=low,
    ).single()
    return rec["c"] if rec else 0


def main() -> None:
    start = time.time()
    recon = _resolve_recon()
    model = _load_recon_model(recon)

    mets = [_cell_str(x) for x in model.mets]
    met_names = [_cell_str(x) for x in model.metNames]
    met_formulas = [_cell_str(x) for x in model.metFormulas]
    met_hmdb = [_cell_str(x) for x in model.metHMDBID]
    met_chebi = [_cell_str(x) for x in model.metCHEBIID]
    charges = np.asarray(model.metCharges).ravel() if hasattr(model, "metCharges") else None
    genes = [_cell_str(x) for x in model.genes]
    n_met, n_gene = len(mets), len(genes)
    print(f"Recon3D: {n_met} mets, {len(model.rxns)} rxns, {n_gene} genes")

    # S (mets x rxns) and rxnGeneMat (rxns x genes). loadmat returns these as either
    # scipy.sparse or dense ndarrays depending on the model; coerce uniformly.
    from scipy import sparse
    S = sparse.csc_matrix(model.S)
    rxn_gene = sparse.csr_matrix(model.rxnGeneMat)

    # --- metabolite records, deduped per chemical (base id, no compartment) ---
    by_base: dict[str, dict] = {}
    met_idx_to_base: dict[int, str] = {}
    for i, mid in enumerate(mets):
        hmdb = _norm_hmdb(met_hmdb[i])
        chebi = _norm_chebi(met_chebi[i])
        if not hmdb and not chebi:
            continue
        base = _COMPARTMENT_RE.sub("", mid)
        met_idx_to_base[i] = base
        charge = int(charges[i]) if charges is not None and i < len(charges) and not np.isnan(charges[i]) else None
        rec = by_base.get(base)
        if rec is None:
            by_base[base] = {
                "hmdb_id": hmdb, "chebi_id": chebi,
                "name": met_names[i] or None, "formula": met_formulas[i] or None,
                "charge": charge, "inchikey": None, "indices": [i],
            }
        else:
            rec["hmdb_id"] = rec["hmdb_id"] or hmdb       # prefer existing; fill gaps
            rec["chebi_id"] = rec["chebi_id"] or chebi
            rec["name"] = rec["name"] or (met_names[i] or None)
            rec["formula"] = rec["formula"] or (met_formulas[i] or None)
            rec["indices"].append(i)
    print(f"Metabolites with HMDB/ChEBI id (deduped per chemical): {len(by_base)}")

    # intrinsic reaction participation per chemical (distinct rxns over its indices,
    # via S row slices — S is CSC metsxrxns, so use CSR for fast row access).
    S_csr = S.tocsr()
    base_rxn_count: dict[str, int] = {}
    for base, rec in by_base.items():
        rxns: set[int] = set()
        for i in rec["indices"]:
            rxns.update(S_csr.indices[S_csr.indptr[i]:S_csr.indptr[i + 1]])
        base_rxn_count[base] = len(rxns)

    # --- HMDB enrichment (fill canonical name / inchikey for hmdb-keyed mets) ---
    needed = {_hmdb_int(r["hmdb_id"]) for r in by_base.values() if r["hmdb_id"]}
    needed.discard(None)
    print(f"HMDB ids to enrich: {len(needed)}")
    hmdb_data = _stream_hmdb(needed)  # may be {} if zip absent
    enriched = 0
    for rec in by_base.values():
        n = _hmdb_int(rec["hmdb_id"]) if rec["hmdb_id"] else None
        info = hmdb_data.get(n) if n is not None else None
        if info:
            enriched += 1
            if info.get("name"):
                rec["name"] = info["name"]            # HMDB canonical name wins
            rec["inchikey"] = info.get("inchikey")
            rec["formula"] = rec["formula"] or info.get("formula")
    print(f"HMDB matches applied: {enriched} / {len(needed)} (0 if HMDB zip absent)")

    # finalise metabolite rows + per-key reaction count
    metabolite_rows: list[dict] = []
    key_rxn_count: dict[str, int] = {}
    for base, rec in by_base.items():
        key_field = "hmdb_id" if rec["hmdb_id"] else "chebi_id"
        key = rec["hmdb_id"] if rec["hmdb_id"] else rec["chebi_id"]
        metabolite_rows.append({
            "key_field": key_field, "key": key,
            "hmdb_id": rec["hmdb_id"], "chebi_id": rec["chebi_id"],
            "name": rec["name"] or key, "formula": rec["formula"],
            "charge": rec["charge"], "inchikey": rec["inchikey"],
        })
        key_rxn_count[key] = base_rxn_count[base]

    # --- Entrez->Ensembl->UniProt mapping for CATALYSES ---
    entrez_to_ensembl = _load_entrez_to_ensembl()
    gene_entrez = [g.split(".")[0] for g in genes]  # column index -> entrez id
    all_ensembl = {entrez_to_ensembl[e] for e in gene_entrez
                   if e and e in entrez_to_ensembl}
    ensembl_to_uniprot = _map_genes_to_uniprot(all_ensembl)
    print(f"Recon3D genes -> Ensembl: {len(all_ensembl)} / {len(set(gene_entrez))}; "
          f"-> graph Protein: {len(ensembl_to_uniprot)}")

    # --- CATALYSES edges (Protein -> Metabolite) ---
    rxns = [_cell_str(x) for x in model.rxns]
    edges: dict[tuple, dict] = {}
    for j in range(len(rxns)):
        gcols = rxn_gene.indices[rxn_gene.indptr[j]:rxn_gene.indptr[j + 1]]
        uniprots = set()
        for col in gcols:
            ens = entrez_to_ensembl.get(gene_entrez[col])
            uni = ensembl_to_uniprot.get(ens) if ens else None
            if uni:
                uniprots.add(uni)
        if not uniprots:
            continue
        col_start, col_end = S.indptr[j], S.indptr[j + 1]
        for row, val in zip(S.indices[col_start:col_end], S.data[col_start:col_end]):
            base = met_idx_to_base.get(row)
            if base is None:
                continue
            rec = by_base[base]
            key = rec["hmdb_id"] or rec["chebi_id"]
            key_field = "hmdb_id" if rec["hmdb_id"] else "chebi_id"
            role = "substrate" if val < 0 else "product"
            for uni in uniprots:
                edges[(uni, key, role)] = {
                    "uniprot_id": uni, "met_key": key, "key_field": key_field,
                    "role": role, "rxn_id": rxns[j],
                }
    edge_rows = list(edges.values())
    print(f"Metabolite nodes: {len(metabolite_rows)}; CATALYSES edges: {len(edge_rows)}")

    with get_session() as session:
        _write_metabolites(session, metabolite_rows)
        _write_catalyses(session, edge_rows)
        deleted = _apply_min_reactions(session, key_rxn_count)
        session.run(
            "MERGE (ds:DataSource {name: $name}) "
            "SET ds.loaded_at = datetime(), ds.source_db = $source_db, "
            "    ds.source_version = $source_version, "
            "    ds.metabolites = $mets, ds.catalyses_edges = $edges, "
            "    ds.hmdb_enriched = $enriched, ds.pruned_low_reaction = $deleted",
            name="14_metabolomics", source_db=SOURCE_DB, source_version=SOURCE_VERSION,
            mets=len(metabolite_rows), edges=len(edge_rows), enriched=enriched,
            deleted=deleted,
        ).consume()

    elapsed = time.time() - start
    print(f"Metabolite nodes merged: {len(metabolite_rows)} (pruned {deleted})")
    print(f"CATALYSES edges merged: {len(edge_rows)}")
    if not edge_rows:
        print("⚠ 0 CATALYSES edges — the metabolite layer is DISCONNECTED. This is "
              "expected while the graph holds only a partial proteome (load the full "
              "proteome via 05_proteins/06_uniprot_enrich to connect it).")
    print(f"Time elapsed: {elapsed:.1f}s")
    close_driver()


if __name__ == "__main__":
    main()
