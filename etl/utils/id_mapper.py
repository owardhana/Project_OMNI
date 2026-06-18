"""HGNC identifier mapping for ETL scripts.

Loads ``hgnc_complete_set.txt`` once and exposes bidirectional lookups between
Ensembl gene IDs and HGNC symbols. Used by ETL scripts that need to resolve one
identifier space to the other (e.g. DoRothEA gives symbols, GTEx gives Ensembl).
"""

import gzip
import re
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HGNC_PATH = _PROJECT_ROOT / "data" / "raw" / "hgnc_complete_set.txt"
DEFAULT_GTF_PATH = _PROJECT_ROOT / "data" / "raw" / "gencode.v46.annotation.gtf.gz"
DEFAULT_SWISSPROT_PATH = (
    _PROJECT_ROOT / "data" / "raw" / "gencode.v46.metadata.SwissProt.gz"
)

# GTF attribute extractors. CDS lines carry both transcript_id and protein_id, so
# they give the ENSP<->ENST link the SwissProt metadata (ENST->UniProt) completes.
_TX_ID_RE = re.compile(r'transcript_id "([^"]+)"')
_PROTEIN_ID_RE = re.compile(r'protein_id "([^"]+)"')


def strip_version(ensembl_id: str) -> str:
    """Strip a trailing ``.NN`` version suffix (ENSG00000139618.19 -> ...618)."""
    if not ensembl_id:
        return ensembl_id
    return ensembl_id.split(".", 1)[0]


class IdMapper:
    """Bidirectional Ensembl gene ID <-> HGNC symbol mapper."""

    def __init__(
        self,
        hgnc_path: Path | str = DEFAULT_HGNC_PATH,
        gtf_path: Path | str = DEFAULT_GTF_PATH,
        swissprot_path: Path | str = DEFAULT_SWISSPROT_PATH,
    ):
        self.hgnc_path = Path(hgnc_path)
        self.gtf_path = Path(gtf_path)
        self.swissprot_path = Path(swissprot_path)
        self._ensembl_to_symbol: dict[str, str] = {}
        self._symbol_to_ensembl: dict[str, str] = {}
        self._symbol_to_uniprot: dict[str, str] = {}
        self._ensp_to_uniprot: dict[str, str] = {}
        self._loaded = False
        self._ensp_loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        if not self.hgnc_path.exists():
            raise FileNotFoundError(
                f"HGNC file not found at {self.hgnc_path}. "
                "Run etl/00_download.sh first."
            )
        df = pd.read_csv(
            self.hgnc_path,
            sep="\t",
            dtype=str,
            usecols=["symbol", "ensembl_gene_id", "uniprot_ids"],
            low_memory=False,
        )
        df = df.dropna(subset=["symbol", "ensembl_gene_id"])
        df = df[df["ensembl_gene_id"].str.strip() != ""]
        for symbol, ensembl, uniprot in zip(
            df["symbol"], df["ensembl_gene_id"], df["uniprot_ids"]
        ):
            ensembl = strip_version(ensembl.strip())
            symbol = symbol.strip()
            self._ensembl_to_symbol[ensembl] = symbol
            self._symbol_to_ensembl[symbol] = ensembl
            # uniprot_ids may be pipe-separated; take the first (canonical) entry.
            if isinstance(uniprot, str) and uniprot.strip():
                self._symbol_to_uniprot[symbol] = uniprot.split("|")[0].strip()
        self._loaded = True

    def ensembl_to_hgnc(self, ensembl_id: str) -> str | None:
        """Return the HGNC symbol for an Ensembl gene ID (version-insensitive)."""
        self._load()
        return self._ensembl_to_symbol.get(strip_version(ensembl_id))

    def hgnc_to_ensembl(self, symbol: str) -> str | None:
        """Return the Ensembl gene ID for an HGNC symbol."""
        self._load()
        return self._symbol_to_ensembl.get(symbol)

    def hgnc_to_uniprot(self, symbol: str) -> str | None:
        """Return the canonical UniProt accession for an HGNC symbol, or None.

        HGNC's ``uniprot_ids`` may list several pipe-separated accessions; the
        first is taken as canonical (ADR-0004).
        """
        self._load()
        return self._symbol_to_uniprot.get(symbol)

    def _load_ensp(self) -> None:
        """Build ENSP -> UniProt by chaining GENCODE GTF (ENSP<->ENST on CDS lines)
        with the SwissProt metadata (ENST -> UniProt). Lazy; parses the GTF once."""
        if self._ensp_loaded:
            return
        for path in (self.swissprot_path, self.gtf_path):
            if not path.exists():
                raise FileNotFoundError(
                    f"{path} not found. Run etl/00_download.sh first."
                )
        # ENST (unversioned) -> UniProt accession, from the SwissProt metadata.
        sp = pd.read_csv(
            self.swissprot_path, sep="\t", header=None,
            names=["enst", "uniprot", "uniprot_v"], dtype=str, compression="gzip",
        )
        enst_to_uniprot: dict[str, str] = {}
        for enst, uniprot in zip(sp["enst"], sp["uniprot"]):
            if isinstance(enst, str) and isinstance(uniprot, str):
                enst_to_uniprot[strip_version(enst.strip())] = uniprot.strip()
        # ENSP (unversioned) -> UniProt via the transcript that encodes it.
        ensp_to_uniprot: dict[str, str] = {}
        with gzip.open(self.gtf_path, "rt") as fh:
            for line in fh:
                if line.startswith("#") or 'protein_id "' not in line:
                    continue
                prot = _PROTEIN_ID_RE.search(line)
                tx = _TX_ID_RE.search(line)
                if not (prot and tx):
                    continue
                ensp = strip_version(prot.group(1))
                if ensp in ensp_to_uniprot:
                    continue
                uniprot = enst_to_uniprot.get(strip_version(tx.group(1)))
                if uniprot:
                    ensp_to_uniprot[ensp] = uniprot
        self._ensp_to_uniprot = ensp_to_uniprot
        self._ensp_loaded = True

    def ensp_to_uniprot(self, ensp_id: str) -> str | None:
        """Return the UniProt accession for an Ensembl protein ID, or None.

        Accepts STRING-style ids with a taxon prefix (``9606.ENSP...``) and
        version suffixes (``ENSP00000493376.2``). Never guesses — an unmapped id
        returns None so the caller can log and skip it.
        """
        self._load_ensp()
        head, sep, tail = ensp_id.partition(".")
        raw = tail if (sep and head.isdigit()) else ensp_id  # strip "9606." taxon
        return self._ensp_to_uniprot.get(strip_version(raw))
