"""HGNC identifier mapping for ETL scripts.

Loads ``hgnc_complete_set.txt`` once and exposes bidirectional lookups between
Ensembl gene IDs and HGNC symbols. Used by ETL scripts that need to resolve one
identifier space to the other (e.g. DoRothEA gives symbols, GTEx gives Ensembl).
"""

from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HGNC_PATH = _PROJECT_ROOT / "data" / "raw" / "hgnc_complete_set.txt"


def strip_version(ensembl_id: str) -> str:
    """Strip a trailing ``.NN`` version suffix (ENSG00000139618.19 -> ...618)."""
    if not ensembl_id:
        return ensembl_id
    return ensembl_id.split(".", 1)[0]


class IdMapper:
    """Bidirectional Ensembl gene ID <-> HGNC symbol mapper."""

    def __init__(self, hgnc_path: Path | str = DEFAULT_HGNC_PATH):
        self.hgnc_path = Path(hgnc_path)
        self._ensembl_to_symbol: dict[str, str] = {}
        self._symbol_to_ensembl: dict[str, str] = {}
        self._loaded = False

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
            usecols=["symbol", "ensembl_gene_id"],
            low_memory=False,
        )
        df = df.dropna(subset=["symbol", "ensembl_gene_id"])
        df = df[df["ensembl_gene_id"].str.strip() != ""]
        for symbol, ensembl in zip(df["symbol"], df["ensembl_gene_id"]):
            ensembl = strip_version(ensembl.strip())
            symbol = symbol.strip()
            self._ensembl_to_symbol[ensembl] = symbol
            self._symbol_to_ensembl[symbol] = ensembl
        self._loaded = True

    def ensembl_to_hgnc(self, ensembl_id: str) -> str | None:
        """Return the HGNC symbol for an Ensembl gene ID (version-insensitive)."""
        self._load()
        return self._ensembl_to_symbol.get(strip_version(ensembl_id))

    def hgnc_to_ensembl(self, symbol: str) -> str | None:
        """Return the Ensembl gene ID for an HGNC symbol."""
        self._load()
        return self._symbol_to_ensembl.get(symbol)
