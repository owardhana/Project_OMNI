"""Closed-world entity dictionary (gazetteer) for literature extraction.

The graph *is* the dictionary (Feature 2 decision 1/2): every canonical id already
lives on a node, so entity-linking is a bounded lookup, not open ontology
disambiguation. This module builds a surface-form -> {id, kind} index from the graph
and matches it against sentence text. A mention that doesn't resolve to an existing
node is simply not matched (dropped, never minted).

Matcher is pure-Python n-gram sliding-window (longest-match, non-overlapping):
- fine for the *nightly* volume (a few k abstracts); an aho-corasick swap is a P3
  *backfill* concern (millions of papers) — YAGNI until then.
- deterministic + offline-testable: a Gazetteer is built from a list[Entry], so
  tests construct one from fixtures with no Neo4j.

Ambiguity guard: short all-caps symbols (MET, SET, CA2) are common English words, so a
case rule + min-length + stoplist gate them. A surface that resolves to several
entries is kept as multiple candidates for the caller to disambiguate or drop.

NOT YET VERIFIED AGAINST THE LIVE GRAPH — two checks are owed before this counts as
working (the offline tests prove the *matcher*, not the *data*):
  1. Alias resolution ("p53" -> TP53) needs `Gene.aliases` populated; re-run
     `etl/01_hgnc.py` (the alias patch) first, then confirm a known alias resolves via
     `build_gazetteer_from_graph`. On today's graph `aliases` is empty -> canonical
     symbols only.
  2. Audit single-token `Disease.name` surfaces for generic words beyond GENERIC_TERMS
     (GWAS-mapped EFO traits are noisy) and extend the floor / make it data-driven.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Surface forms shorter than this are only matched case-sensitively (see _case_ok).
MIN_CASE_INSENSITIVE_LEN = 5

# Tiny hard stoplist of symbols that are also frequent English/lab words — matched
# ONLY when the sentence casing is exact. Data-driven expansion is a follow-up; this
# is the belt-and-suspenders floor (mirrors the ADR-0012 cofactor-floor pattern).
AMBIGUOUS_SURFACES = {
    "MET", "SET", "CA2", "PCA", "REST", "MAX", "AGO", "IMPACT", "CAD", "GC",
    "T", "APP", "ARC", "CAT", "COPE", "MICE", "SDS", "PIGS", "SHE", "CS",
}

# Generic disease words (from GWAS-mapped EFO `Disease.name`) that are ≥5 chars and
# lowercase, so they'd sail through the case gate and match every occurrence in prose
# — a precision hole for IMPLICATED_IN. Never matched as a STANDALONE surface; a
# longer dict phrase ("breast cancer") still wins via longest-match. This is the
# hardcoded floor — a data-driven audit of single-token disease surfaces is the real
# fix (parallel to ADR-0012's degree-gate-over-hand-list philosophy).
GENERIC_TERMS = {
    "cancer", "carcinoma", "tumor", "tumour", "obesity", "pain", "death",
    "response", "disease", "syndrome", "disorder", "infection", "inflammation",
    "deficiency", "failure", "injury", "aging", "ageing",
}

# Token = alphanumerics with hyphen/period allowed only *between* alnums, so symbols
# like TP53, HLA-A, 5-HT tokenize whole while sentence-final punctuation ("mellitus.")
# is excluded from the token.
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[.\-][A-Za-z0-9]+)*")


@dataclass(frozen=True)
class Entry:
    """One resolvable surface form -> a canonical graph node."""
    surface: str          # e.g. "p53"
    node_id: str          # canonical id, e.g. "ENSG00000141510"
    kind: str             # gene | protein | disease | metabolite
    canonical: str        # canonical display, e.g. "TP53"


@dataclass
class Match:
    """A dictionary hit in a piece of text."""
    surface: str
    start: int            # token index (inclusive)
    end: int              # token index (exclusive)
    candidates: list[Entry]  # >1 when the surface is ambiguous across nodes


@dataclass
class Gazetteer:
    """Surface-form index + n-gram matcher. Build via ``add`` or the classmethod."""
    _index: dict[str, list[Entry]] = field(default_factory=dict)  # lower-surface -> entries
    _max_ngram: int = 1

    def add(self, entry: Entry) -> None:
        key = entry.surface.lower()
        self._index.setdefault(key, []).append(entry)
        n = len(_tokenize(entry.surface))
        if n > self._max_ngram:
            self._max_ngram = n

    @classmethod
    def from_entries(cls, entries: list[Entry]) -> "Gazetteer":
        g = cls()
        for e in entries:
            g.add(e)
        return g

    def __len__(self) -> int:
        return len(self._index)

    def match(self, text: str) -> list[Match]:
        """Longest-match, non-overlapping hits over the text's tokens."""
        tokens = _tokenize_spans(text)
        out: list[Match] = []
        i = 0
        while i < len(tokens):
            hit = self._longest_at(text, tokens, i)
            if hit is None:
                i += 1
                continue
            out.append(hit)
            i = hit.end
        return out

    def _longest_at(self, text: str, tokens: list[tuple[str, int, int]], i: int) -> Match | None:
        # Try the longest n-gram first so "type 2 diabetes" wins over "diabetes".
        upper = min(self._max_ngram, len(tokens) - i)
        for n in range(upper, 0, -1):
            surface_tokens = tokens[i : i + n]
            surface = " ".join(t[0] for t in surface_tokens)
            entries = self._index.get(surface.lower())
            if not entries:
                continue
            if not _passes_gate(surface, entries):
                continue
            return Match(surface=surface, start=i, end=i + n, candidates=entries)
        return None


# --- gates -------------------------------------------------------------------

def _passes_gate(surface: str, entries: list[Entry]) -> bool:
    """Filter false positives from short/ambiguous/generic surfaces."""
    if surface.isdigit():
        # bare numbers are never entity mentions; they appear as junk single-token
        # Disease.name values ('1','2','3') and would match every number in prose.
        return False
    if surface.lower() in GENERIC_TERMS:
        # generic disease word standalone -> drop (a longer phrase would have won
        # the longest-match already).
        return False
    if surface.upper() in AMBIGUOUS_SURFACES:
        # only a canonical entry whose surface case matches exactly may pass; the
        # caller sees it, but we require the sentence token to have been all-caps.
        return any(e.surface == surface for e in entries)
    if len(surface) < MIN_CASE_INSENSITIVE_LEN:
        # short surface (e.g. "EGFR") must match an entry's exact casing to avoid
        # colliding with lowercase common words.
        return any(e.surface == surface for e in entries)
    return True


# --- tokenization ------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text)


def _tokenize_spans(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end()) for m in _TOKEN_RE.finditer(text)]


# --- build from the live graph ----------------------------------------------

# Pulls canonical surface + synonyms per embeddable/linkable kind. Genes contribute
# hgnc_symbol + aliases[] (loaded by the 01_hgnc patch); proteins their hgnc_symbol;
# diseases their name (EFO-label recall is a known gap — synonyms are a follow-up).
# Plain top-level UNION (no CALL wrapper — nothing to import; portable across Neo4j
# versions). All three branches return the same columns.
_BUILD_QUERY = """
MATCH (g:Gene) WHERE g.hgnc_symbol IS NOT NULL
RETURN g.hgnc_symbol AS canonical, g.ensembl_id AS id, 'gene' AS kind,
       [g.hgnc_symbol] + coalesce(g.aliases, []) AS surfaces
UNION
MATCH (p:Protein) WHERE p.hgnc_symbol IS NOT NULL
RETURN p.hgnc_symbol AS canonical, p.uniprot_id AS id, 'protein' AS kind,
       [p.hgnc_symbol] AS surfaces
UNION
MATCH (d:Disease) WHERE d.name IS NOT NULL
RETURN d.name AS canonical, d.ontology_id AS id, 'disease' AS kind,
       [d.name] AS surfaces
"""


async def build_gazetteer_from_graph(session) -> Gazetteer:
    """Build a Gazetteer from the live graph. ``session`` is an open async Neo4j
    session (caller owns its lifecycle)."""
    rows = await (await session.run(_BUILD_QUERY)).data()
    entries: list[Entry] = []
    for r in rows:
        for surface in r["surfaces"]:
            if surface:
                entries.append(
                    Entry(surface=surface, node_id=r["id"],
                          kind=r["kind"], canonical=r["canonical"])
                )
    return Gazetteer.from_entries(entries)
