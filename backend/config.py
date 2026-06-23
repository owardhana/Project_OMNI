"""Application configuration loaded from environment / .env.

All settings come from environment variables (see .env.example). List-valued
settings (TISSUES, DOROTHEA_MIN_CONFIDENCE) are stored as comma-separated
strings and exposed as parsed lists via properties — pydantic-settings tries to
JSON-decode fields typed as ``list`` before validators run, which would choke on
a plain comma-separated value, so we keep the raw field as ``str``.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # OpenRouter
    OPENROUTER_API_KEY: str = ""

    # Neo4j
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "changeme"

    # NCBI (optional — raises E-utilities rate limit 3 -> 10 req/s)
    NCBI_API_KEY: str = ""

    # Models (OpenRouter slugs)
    TEXT2CYPHER_MODEL: str = "anthropic/claude-sonnet-4.6"
    SYNTHESIS_MODEL: str = "anthropic/claude-sonnet-4.6"
    CITATION_CHECK_MODEL: str = "anthropic/claude-haiku-4.5"
    # Phase 2: embedding model for semantic search (ADR-0008). 1536-dim.
    EMBEDDING_MODEL: str = "openai/text-embedding-3-small"

    # App config
    TISSUES: str = "whole_blood,liver,brain_prefrontal_cortex"
    DOROTHEA_MIN_CONFIDENCE: str = "A,B"
    CITATION_AGENT_BATCH_SIZE: int = 100
    CITATION_AGENT_CRON_HOUR: int = 0
    DEFAULT_GENE: str = "TP53"
    TISSUE_WEIGHT_THRESHOLD: float = 0.3

    # Signal-decay traversal (ADR-0005). Defaults chosen so a paramless /graph
    # call returns a bounded, legible subgraph.
    TRAVERSAL_DECAY: float = 0.7  # global per-hop decay (d)
    TRAVERSAL_MIN_SIGNAL: float = 0.05  # signal floor (epsilon)
    TRAVERSAL_MAX_NODES: int = 300  # hard cap (guardrail); raised 150->300 (ADR-0010
    # full proteome) so a gene seed has budget to reach proteins + metabolites, not
    # just regulated genes.
    PRODUCES_CONDUCTANCE: float = 0.9  # structural; tissue is NOT in conductance (ADR-0006)
    STRUCTURAL_CONDUCTANCE: float = 1.0  # TRANSLATES_TO / ENCODES

    # Phase 2 tunable scaling parameters (see docs/data-architecture.md). All env-driven —
    # never hardcode thresholds in ETL or traversal code.
    STRING_MIN_CONFIDENCE: float = 0.95  # STRING PPI combined_score threshold (~101k edges, full proteome)
    STRING_MAX_EXPAND_PER_NODE: int = 10  # max INTERACTS_WITH neighbours per frontier step
    # REGULATES is now dense-capped too (a hub TF regulates hundreds of genes and was
    # flooding gene-seeded views, starving the molecular backbone). Higher than the
    # STRING cap so the regulatory story still reads; top-k by DoRothEA confidence.
    REGULATES_MAX_EXPAND_PER_NODE: int = 25  # max REGULATES neighbours per frontier step
    # Backbone-guaranteed traversal (ADR-0011). The seed's own vertical chain (gene
    # -> transcript -> protein -> CATALYSES -> metabolite) is pinned via a pre-pass so
    # deep layers survive the breadth fan-out. This caps how many metabolites a single
    # pinned protein contributes (ranked deterministically by metabolite key) — guards
    # against a promiscuous enzyme pinning hundreds of nodes past max_nodes.
    BACKBONE_MAX_METABOLITES_PER_PROTEIN: int = 25
    GWAS_MIN_SIGNIFICANCE: float = 5e-8  # GWAS p-value cutoff (genome-wide significance)
    EMBEDDING_AGENT_BATCH_SIZE: int = 50  # nodes per embedding agent run
    EMBEDDING_AGENT_CRON_HOUR: int = 1  # 1am UTC (after citation agent at midnight)

    @property
    def tissues(self) -> list[str]:
        """Tissue keys, e.g. ['whole_blood', 'liver', 'brain_prefrontal_cortex']."""
        return [t.strip() for t in self.TISSUES.split(",") if t.strip()]

    @property
    def dorothea_min_confidence(self) -> list[str]:
        """Allowed DoRothEA confidence tiers, e.g. ['A', 'B']."""
        return [c.strip() for c in self.DOROTHEA_MIN_CONFIDENCE.split(",") if c.strip()]


settings = Settings()
