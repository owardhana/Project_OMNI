# ADR 0002 — Use OpenRouter canonical model slugs

Status: Accepted (2026-06-15)

## Context

04_decisions.md and the build prompt specify these model IDs:

- Text2Cypher / synthesis: `anthropic/claude-sonnet-4-6`
- Citation relevance check: `anthropic/claude-haiku-4-5-20251001`

These are Anthropic-API-style IDs. OpenRouter (the chosen LLM gateway) uses its
own slug naming. Verified against `https://openrouter.ai/api/v1`:

- `anthropic/claude-sonnet-4-6` → resolved OK (OpenRouter normalises it).
- `anthropic/claude-haiku-4-5-20251001` → **HTTP 400 "is not a valid model ID"**.

OpenRouter's model list exposes:

- `anthropic/claude-sonnet-4.6`
- `anthropic/claude-haiku-4.5`

Both confirmed working with a live chat-completion call.

## Decision

Use the canonical dotted OpenRouter slugs everywhere (`.env`, `.env.example`,
`backend/config.py` defaults):

- `TEXT2CYPHER_MODEL=anthropic/claude-sonnet-4.6`
- `SYNTHESIS_MODEL=anthropic/claude-sonnet-4.6`
- `CITATION_CHECK_MODEL=anthropic/claude-haiku-4.5`

This is the same models the spec intended (Sonnet 4.6, Haiku 4.5) — only the slug
format changes to what OpenRouter accepts. Primary-source evidence (live 400 +
model list) overrides the spec's literal string.

## Consequences

- Model IDs are read from config, so Phase 4's LLM client needs no special-casing.
- If OpenRouter renames slugs later, update `.env` only; defaults in config are a
  fallback.
