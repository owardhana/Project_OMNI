"""System prompt for natural-language -> Cypher translation.

Phase 2: the schema block is generated dynamically from the live graph via
``apoc.meta.schema()`` and cached for the process lifetime (apoc.meta.schema can
take 5-30s on a large graph — it is called ONCE at startup, never per request).
The rules and examples remain hand-curated. A static schema is kept as a fallback
when APOC is unavailable, and powers the synchronous TEXT2CYPHER_SYSTEM_PROMPT
back-compat constant.
"""

import logging

from backend.db.neo4j_client import get_session

logger = logging.getLogger(__name__)

# OmniGraph biological labels rendered into the schema block (operational/log
# labels like DataSource, CitationRun, EmbeddingRun are excluded).
_OMNI_LABELS = {"Gene", "Transcript", "Protein", "Variant", "Disease"}
# Internal properties never exposed to the LLM (too large / operational).
_EXCLUDE_PROPS = {"embedding", "citation_attempted", "source_agent",
                  "agent_version", "run_timestamp", "embedding_model"}

_PROMPT_HEADER = (
    "You are a Neo4j Cypher expert for OmniGraph, a multi-omics knowledge graph "
    "of human biology. Translate the user's question into a single, READ-ONLY "
    "Cypher query."
)

# Static fallback schema (used if apoc.meta.schema() is unavailable). Mirrors the
# real graph including the Phase 2 Variant/Disease nodes and edge types.
_STATIC_SCHEMA = """\
Nodes:
- (:Gene {ensembl_id, hgnc_symbol, hgnc_id, description, chromosome, biotype, summary_text, pli_score})
- (:Transcript {ensembl_tx_id, hgnc_symbol, biotype, length_bp})
- (:Protein {uniprot_id, hgnc_symbol, subtype, summary_text, subcellular_loc, molecular_weight, go_terms})
    A transcription factor is a Protein with subtype='transcription_factor'.
- (:Variant {rsid, chromosome, position_grch38, consequence_type, clinical_significance})
- (:Disease {ontology_id, name, description})
    ontology_id is an EFO/MONDO/Orphanet id (e.g. 'MONDO_0005148'); match on name.

Relationships:
- (:Protein)-[:REGULATES {mode, confidence, confidence_tier, source_db, pmids}]->(:Gene)
- (:Gene)-[:PRODUCES {tw_whole_blood, tw_liver, tw_brain_prefrontal_cortex, source_db, pmids}]->(:Transcript)
- (:Transcript)-[:TRANSLATES_TO]->(:Protein)
- (:Gene)-[:ENCODES]->(:Protein)
- (:Protein)-[:INTERACTS_WITH {combined_score, experimental_score, coexpression_score, source_db}]->(:Protein)
- (:Variant)-[:ASSOCIATED_WITH {p_value, source_db, pmids}]->(:Disease)
- (:Variant)-[:IN_GENE {consequence_type, source_db}]->(:Gene)
- (:Gene)-[:IMPLICATED_IN]->(:Disease)"""

CURATED_RULES = """\
1. A transcription factor is a (:Protein). REGULATES is (:Protein)->(:Gene), never (:Gene)->(:Gene).
2. ALWAYS filter REGULATES edges with: confidence_tier IN ['A','B'].
3. Look up proteins and genes by hgnc_symbol (e.g. {hgnc_symbol: 'TP53'}). The same symbol may name both a gene and its protein.
4. For tissue-specific expression, filter on tw_<tissue> > 0.3 (blood -> tw_whole_blood, liver -> tw_liver, brain -> tw_brain_prefrontal_cortex).
5. INTERACTS_WITH is undirected protein-protein interaction — match it without a direction arrow: (a)-[:INTERACTS_WITH]-(b).
6. Diseases are matched by name (case-insensitively, e.g. toLower(d.name) CONTAINS 'diabetes'); a Variant ASSOCIATED_WITH a Disease, IN_GENE a Gene; gene->disease shortcuts use IMPLICATED_IN.
7. Variant clinical_significance values look like 'Pathogenic', 'Likely pathogenic', 'Benign'. position_grch38 is an int.
8. Return the pmids property on any edge that has it, so citations can be shown.
9. The query MUST be read-only. Never use MERGE, CREATE, DELETE, SET, REMOVE.
10. Output ONLY the Cypher query — no explanation, no markdown fences."""

CURATED_EXAMPLES = """\
Q: What transcription factors regulate TP53?
A: MATCH (tf:Protein)-[r:REGULATES]->(target:Gene {hgnc_symbol: 'TP53'})
WHERE r.confidence_tier IN ['A','B']
RETURN tf.hgnc_symbol AS regulator, r.mode AS mode, r.confidence AS confidence, r.pmids AS pmids
ORDER BY r.confidence DESC

Q: What transcripts does BRCA2 produce in liver?
A: MATCH (g:Gene {hgnc_symbol: 'BRCA2'})-[r:PRODUCES]->(t:Transcript)
WHERE r.tw_liver > 0.3
RETURN t.ensembl_tx_id AS transcript, t.biotype AS biotype, r.tw_liver AS liver_weight, r.pmids AS pmids
ORDER BY r.tw_liver DESC

Q: Which TFs repress MYC?
A: MATCH (tf:Protein)-[r:REGULATES]->(target:Gene {hgnc_symbol: 'MYC'})
WHERE r.confidence_tier IN ['A','B'] AND r.mode = 'repressor'
RETURN tf.hgnc_symbol AS repressor, r.confidence AS confidence, r.pmids AS pmids
ORDER BY r.confidence DESC

Q: Which proteins interact with TP53?
A: MATCH (p:Protein {hgnc_symbol: 'TP53'})-[r:INTERACTS_WITH]-(partner:Protein)
RETURN partner.hgnc_symbol AS partner, r.combined_score AS score
ORDER BY r.combined_score DESC LIMIT 20

Q: What genes are associated with type 2 diabetes?
A: MATCH (d:Disease)<-[:ASSOCIATED_WITH]-(v:Variant)-[:IN_GENE]->(g:Gene)
WHERE toLower(d.name) CONTAINS 'type 2 diabetes'
RETURN DISTINCT g.hgnc_symbol AS gene, count(v) AS variant_count
ORDER BY variant_count DESC

Q: Find pathogenic variants in TP53.
A: MATCH (v:Variant)-[:IN_GENE]->(g:Gene {hgnc_symbol: 'TP53'})
WHERE v.clinical_significance IN ['Pathogenic', 'Likely pathogenic']
RETURN v.rsid AS variant, v.clinical_significance AS significance

Q: What proteins interact with EGFR that also regulate cancer-related genes?
A: MATCH (egfr:Protein {hgnc_symbol: 'EGFR'})-[:INTERACTS_WITH]-(p:Protein)
MATCH (p)-[:REGULATES]->(g:Gene)
WHERE g.cancer_gene = true AND EXISTS { (p)-[:REGULATES]->() }
RETURN DISTINCT p.hgnc_symbol AS protein, collect(g.hgnc_symbol) AS regulated_cancer_genes"""


def _build_prompt(schema_block: str) -> str:
    return (
        f"{_PROMPT_HEADER}\n\n"
        f"# Schema\n\n{schema_block}\n\n"
        f"# Rules\n\n{CURATED_RULES}\n\n"
        f"# Examples\n\n{CURATED_EXAMPLES}\n"
    )


def _render_schema(schema: dict) -> str:
    """Render apoc.meta.schema() output into a compact node/relationship block,
    filtered to OmniGraph labels and excluding internal properties."""
    node_lines: list[str] = []
    rel_lines: list[str] = []
    seen_rels: set[tuple[str, str, str]] = set()

    for label, info in sorted(schema.items()):
        if not isinstance(info, dict) or info.get("type") != "node":
            continue
        if label not in _OMNI_LABELS:
            continue
        props = [
            f"{p}: {pinfo.get('type', '')}"
            for p, pinfo in sorted((info.get("properties") or {}).items())
            if p not in _EXCLUDE_PROPS
        ]
        node_lines.append(f"- (:{label} {{{', '.join(props)}}})")

        for rel, rinfo in (info.get("relationships") or {}).items():
            if rinfo.get("direction") != "out":
                continue
            targets = rinfo.get("labels") or []
            if isinstance(targets, str):  # APOC usually returns a list; be safe
                targets = [targets]
            for target in targets:
                if target not in _OMNI_LABELS:
                    continue
                key = (label, rel, target)
                if key in seen_rels:
                    continue
                seen_rels.add(key)
                rprops = [
                    f"{p}: {pinfo.get('type', '')}"
                    for p, pinfo in sorted((rinfo.get("properties") or {}).items())
                    if p not in _EXCLUDE_PROPS
                ]
                block = f" {{{', '.join(rprops)}}}" if rprops else ""
                rel_lines.append(f"- (:{label})-[:{rel}{block}]->(:{target})")

    if not node_lines:
        return _STATIC_SCHEMA
    rendered = "Nodes:\n" + "\n".join(node_lines)
    if rel_lines:
        rendered += "\n\nRelationships:\n" + "\n".join(sorted(rel_lines))
    return rendered


async def get_schema_block() -> str:
    """Generate the schema description from the live graph via apoc.meta.schema()."""
    try:
        async with get_session() as session:
            result = await session.run("CALL apoc.meta.schema() YIELD value RETURN value")
            record = await result.single()
        return _render_schema(record["value"]) if record else _STATIC_SCHEMA
    except Exception as exc:  # noqa: BLE001 — fall back rather than break the agent
        logger.warning("get_schema_block: apoc.meta.schema failed (%s); using static", exc)
        return _STATIC_SCHEMA


_cached_schema_block: str = ""


async def ensure_schema_cached() -> None:
    """Populate the module-level schema cache once (call at app startup)."""
    global _cached_schema_block
    if not _cached_schema_block:
        _cached_schema_block = await get_schema_block()
        logger.info("Text2Cypher schema cached (%d chars)", len(_cached_schema_block))


async def build_text2cypher_prompt() -> str:
    """The full Text2Cypher system prompt using the cached dynamic schema."""
    schema = _cached_schema_block or await get_schema_block()
    return _build_prompt(schema)


# Synchronous back-compat constant (static schema). Live calls should prefer
# build_text2cypher_prompt(), which uses the cached dynamic schema.
TEXT2CYPHER_SYSTEM_PROMPT = _build_prompt(_STATIC_SCHEMA)
