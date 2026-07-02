"""Regression test for etl/08_gwas.py parse_traits (fragmented Disease.name bug).

GWAS Catalog separates multiple mapped traits with ", " (comma-space); individual
trait names can contain BARE commas (chemical names). Splitting on a bare "," used to
fragment those names into junk Disease.name values ("1", "4-androsten-3alpha", ...).
These lock the ", "-split + numeric-guard fix.

Loaded via importlib because the module name starts with a digit (not importable).
"""

import importlib.util
import sys
import types
from pathlib import Path

# 08_gwas.py imports pandas + utils.neo4j_client at module load — neither is used by
# the pure parse_traits. Stub them so the parser imports under the backend venv (which
# has no pandas) without a DB connection.
sys.modules.setdefault("pandas", types.ModuleType("pandas"))
_neo_stub = types.ModuleType("utils.neo4j_client")
_neo_stub.get_session = _neo_stub.close_driver = lambda *a, **k: None  # type: ignore[attr-defined]
sys.modules.setdefault("utils.neo4j_client", _neo_stub)

_MODULE = Path(__file__).resolve().parents[2] / "etl" / "08_gwas.py"


def _parse_traits():
    spec = importlib.util.spec_from_file_location("gwas08", _MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.parse_traits


_EFO = "http://www.ebi.ac.uk/efo/EFO_0001360"
_EFO2 = "http://www.ebi.ac.uk/efo/EFO_0004530"


def test_bare_comma_chemical_name_stays_intact():
    pt = _parse_traits()
    out = pt(_EFO, "1,4-dihydro-1-Methyl-4-oxo-3-pyridinecarboxamide measurement")
    assert out == [("EFO_0001360", "1,4-dihydro-1-Methyl-4-oxo-3-pyridinecarboxamide measurement")]


def test_steroid_name_not_fragmented():
    pt = _parse_traits()
    out = pt(_EFO, "4-androsten-3alpha,17beta-diol measurement")
    assert out == [("EFO_0001360", "4-androsten-3alpha,17beta-diol measurement")]


def test_multi_trait_splits_and_aligns():
    pt = _parse_traits()
    out = pt(f"{_EFO}, {_EFO2}", "cardiotoxicity, response to anthracycline-based chemotherapy")
    assert out == [
        ("EFO_0001360", "cardiotoxicity"),
        ("EFO_0004530", "response to anthracycline-based chemotherapy"),
    ]


def test_numeric_name_falls_back_to_id():
    pt = _parse_traits()
    assert pt(_EFO, "2") == [("EFO_0001360", "EFO_0001360")]
    assert pt(_EFO, "") == [("EFO_0001360", "EFO_0001360")]
