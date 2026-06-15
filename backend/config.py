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

    # App config
    TISSUES: str = "whole_blood,liver,brain_prefrontal_cortex"
    DOROTHEA_MIN_CONFIDENCE: str = "A,B"
    CITATION_AGENT_BATCH_SIZE: int = 100
    CITATION_AGENT_CRON_HOUR: int = 0
    DEFAULT_GENE: str = "TP53"
    TISSUE_WEIGHT_THRESHOLD: float = 0.3

    @property
    def tissues(self) -> list[str]:
        """Tissue keys, e.g. ['whole_blood', 'liver', 'brain_prefrontal_cortex']."""
        return [t.strip() for t in self.TISSUES.split(",") if t.strip()]

    @property
    def dorothea_min_confidence(self) -> list[str]:
        """Allowed DoRothEA confidence tiers, e.g. ['A', 'B']."""
        return [c.strip() for c in self.DOROTHEA_MIN_CONFIDENCE.split(",") if c.strip()]


settings = Settings()
