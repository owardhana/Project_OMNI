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
    # Compartment-aware PPI (ADR-0015): when true, traversal only crosses an
    # INTERACTS_WITH edge if the two proteins SHARE a subcellular compartment
    # (Protein.subcellular_locs, loaded by 17_location). Proteins with unknown
    # localization are never filtered out (conservative). Default off; a per-request
    # override + a frontend toggle expose it to users.
    COMPARTMENT_PPI_FILTER: bool = False
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
    # Metabolite "bridge" connectivity (ADR-0012, amends ADR-0011). Default OFF — the
    # ADR-0011 leaf rule (a discovered metabolite is a terminal display node) is the
    # tuned default. When ON, a discovered metabolite may expand ONCE to its
    # co-catalysing proteins (shared-substrate links), gated so hub cofactors can't
    # flood: a metabolite expands only if its CATALYSES degree (persisted by
    # 14_metabolomics as Metabolite.catalyses_degree) is <= the threshold below, and
    # its CATALYSES expansion is dense-capped per node. The degree gate is DATA-DRIVEN
    # (re-derived every ETL run) — not a hand-maintained cofactor list (ADR-0011's
    # reason to defer). Same gate also tames a cofactor SEED flooding ring 1.
    METABOLITE_BRIDGE_ENABLED: bool = False
    CATALYSES_MAX_EXPAND_PER_NODE: int = 8  # max co-catalysing proteins per metabolite/ring
    METABOLITE_MAX_CATALYSES_DEGREE: int = 30  # metabolites above this degree never expand (cofactor cutoff)
    GWAS_MIN_SIGNIFICANCE: float = 5e-8  # GWAS p-value cutoff (genome-wide significance)
    EMBEDDING_AGENT_BATCH_SIZE: int = 50  # nodes per embedding agent run
    EMBEDDING_AGENT_CRON_HOUR: int = 1  # 1am UTC (after citation agent at midnight)
    # Nightly embedding crawl hits the OpenRouter embeddings API (costs $). Default
    # OFF — populate on demand via POST /admin/agents/embedding/run instead. The
    # semantic_search chat tool queries whatever vectors already exist regardless.
    EMBEDDING_AGENT_CRON_ENABLED: bool = False

    # --- Literature extraction (Feature 2, P1) — all tunable, never hardcode ---
    # Master switch. OFF by default: the extractor spends on NCBI E-utils + the LLM,
    # so the admin trigger refuses unless this is true. Nothing runs unattended.
    EXTRACTION_AGENT_ENABLED: bool = False
    # Model for the per-sentence relation verdict. Default is a FREE OpenRouter slug
    # (NVIDIA Nemotron 3 Ultra) so the always-on backfill costs $0 — verified present
    # in the live OpenRouter model list (ADR-0002 slug-verification discipline). A
    # free/reasoning model trades some yield (weaker JSON adherence, rate limits) for
    # zero cost; the pipeline fails safe on unparseable output (drops, never corrupts).
    # Swap to a paid slug (e.g. anthropic/claude-haiku-4.5) for higher-precision runs.
    EXTRACTION_MODEL: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    # Reasoning models emit a chain-of-thought preamble before the JSON verdict; ask
    # OpenRouter to exclude it so the parser sees clean output. No-op on non-reasoning
    # models. Turn off only if a chosen model rejects the reasoning param.
    EXTRACTION_EXCLUDE_REASONING: bool = True
    PUBMED_DELTA_TERM: str = "humans[MeSH Terms]"  # broad biomedical corpus scope
    # E-utils date field the cursor windows walk. Default 'edat' (Entrez date = when the
    # record was ADDED to PubMed): a precise, per-record, monotonic key. NOT 'pdat'
    # (publication date) — PubMed defaults year-only pub dates to Jan 1, so 'pdat'
    # single-day counts pile up (~123k on YYYY/01/01 vs ~4k for edat), blowing past the
    # esearch 9,999 no-history cap and silently truncating the backfill. edat also catches
    # late-indexed papers a pub-date walk would miss. The actual publication date is still
    # read from each article's metadata; edat is only the windowing/partition key.
    EXTRACTION_DATE_TYPE: str = "edat"
    PUBMED_DELTA_DAYS: int = 1        # esearch reldate window (nightly = 1)
    PUBMED_DELTA_RETMAX: int = 200    # max PMIDs per delta run (scaffold cap)
    EXTRACTION_CONFIDENCE_FLOOR: float = 0.5  # candidates below this are not surfaced
    EXTRACTION_EFETCH_BATCH: int = 100  # PMIDs per efetch call
    # Bounded concurrency for the per-(sentence,pair) LLM verdict calls. Sized so enough
    # calls are in flight to saturate the per-minute rate limit given the model's latency
    # (~27s free-tier × 15/min ≈ 7 concurrent), not to burst past it — the rate limiter
    # below is the real throttle. Serial (=1) is also fine for a tiny nightly delta.
    EXTRACTION_LLM_CONCURRENCY: int = 8
    # OpenRouter's FREE tier caps requests account-wide per minute (observed 16/min for
    # free models). Pace call starts just under that so the backfill drips steadily
    # instead of 429-storming and burning its budget on retries. Raise for a paid model.
    EXTRACTION_LLM_RATE_PER_MIN: float = 15.0
    # Per-verdict LLM timeout. The OpenAI SDK default is ~10 min; a free reasoning model
    # can stream very slowly or sit queued, so bound it — a hung verdict is dropped (and
    # counted as an llm_error → chunk retried) rather than blocking a whole chunk. Only
    # applied to the extraction call, not chat/synthesis.
    EXTRACTION_LLM_TIMEOUT_S: float = 120.0

    # --- Feature 2 P3 — date-cursor pipeline (nightly catch-up + historical backfill) ---
    # Both directions walk PubMed by publication date in chunks, persisting progress on a
    # singleton :ExtractionCursor node so a crash/redeploy resumes at chunk granularity
    # (stage_verdict's MERGE is idempotent, so redoing a partial chunk is safe).
    EXTRACTION_BACKFILL_FLOOR_DATE: str = "2005-01-01"  # oldest pubdate the backfill will reach
    EXTRACTION_BACKFILL_CHUNK_DAYS: int = 7   # backward-walk window width (probe-shrunk if too dense)
    EXTRACTION_FORWARD_CHUNK_DAYS: int = 1    # nightly forward-walk window width
    EXTRACTION_FORWARD_LAG_DAYS: int = 2      # trailing buffer for PubMed indexing lag (don't chase "today")
    EXTRACTION_MAX_PMIDS_PER_CHUNK: int = 5000  # halve the window until a chunk's esearch count fits this
    # HTTP retry/backoff for NCBI E-utils (honours 429 Retry-After); LLM calls reuse this.
    EXTRACTION_HTTP_MAX_RETRIES: int = 4
    EXTRACTION_HTTP_BACKOFF_S: float = 1.0    # base backoff, exponential per attempt
    EXTRACTION_BACKFILL_CRON_HOUR: int = 3    # nightly forward-catchup cron hour (UTC)

    # --- Feature 2 P2 — promotion + provenance-tier discount (ADR-0013) ---
    # Promoted literature edges conduct less signal than canonical ones (a
    # single-sentence claim < consortium data). Applied multiplicatively in traversal.
    LITERATURE_CONDUCTANCE_FACTOR: float = 0.5
    # Auto-promote is UNCALIBRATED until the precision harness (RUN_EXTRACTION_EVAL)
    # produces a number — default OFF; use manual approve/reject. When on, a candidate
    # promotes iff confidence >= threshold AND >= N independent affirming PMIDs AND no
    # contradicting evidence.
    VALIDATION_AUTO_PROMOTE_ENABLED: bool = False
    VALIDATION_AUTO_PROMOTE_CONFIDENCE: float = 0.75
    VALIDATION_MIN_INDEPENDENT_PMIDS: int = 2

    # --- Feature 2 P3 — admin review dashboard (ADR-0014) ---
    # Gates the /admin router. Empty (default) = open, for local single-user dev; set a
    # non-empty secret on any shared/public host (the frontend sends it as X-Admin-Token,
    # and Caddy basic-auth sits in front as a second layer).
    ADMIN_TOKEN: str = ""
    # Fail-closed switch for public hosts (ADR-0017). When true, an empty ADMIN_TOKEN
    # makes the /admin router REFUSE every request (503) instead of falling open —
    # so a forgotten token on a public deploy locks admin down rather than exposing it.
    # Default false preserves the local single-user dev convenience; set true in prod.
    ADMIN_FAIL_CLOSED: bool = False

    @property
    def tissues(self) -> list[str]:
        """Tissue keys, e.g. ['whole_blood', 'liver', 'brain_prefrontal_cortex']."""
        return [t.strip() for t in self.TISSUES.split(",") if t.strip()]

    @property
    def dorothea_min_confidence(self) -> list[str]:
        """Allowed DoRothEA confidence tiers, e.g. ['A', 'B']."""
        return [c.strip() for c in self.DOROTHEA_MIN_CONFIDENCE.split(",") if c.strip()]


settings = Settings()
