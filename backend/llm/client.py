"""OpenRouter LLM client (OpenAI-compatible).

A single AsyncOpenAI client pointed at OpenRouter. Model slugs come from config
(verified canonical OpenRouter slugs — see docs/adr/0002-openrouter-model-slugs.md).
"""

from openai import AsyncOpenAI

from backend.config import settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

SYNTHESIS_MODEL = settings.SYNTHESIS_MODEL
CITATION_CHECK_MODEL = settings.CITATION_CHECK_MODEL

_EMBED_MAX_CHARS = 8000  # keep well under the embedding model's token limit

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=settings.OPENROUTER_API_KEY,
        )
    return _client


async def embed_text(text: str) -> list[float]:
    """Embed a single text with the configured embedding model (ADR-0008).

    Shared by the EmbeddingAgent (batch node enrichment) and the chat
    ``semantic_search`` tool (embedding the live query). Each call hits the
    OpenRouter embeddings API — cheap, but not free."""
    response = await get_client().embeddings.create(
        model=settings.EMBEDDING_MODEL, input=text[:_EMBED_MAX_CHARS]
    )
    return response.data[0].embedding


async def complete(model: str, messages: list[dict], **kwargs) -> str:
    """Run a chat completion and return the assistant text (never None).

    OpenRouter can return an HTTP 200 whose body carries ``choices: null`` plus an
    ``error`` (e.g. an upstream free-tier rate limit) instead of raising. Guard against
    that so it surfaces as a retryable exception rather than a bare
    ``'NoneType' object is not subscriptable`` — callers treat exceptions as transient
    (retry/backoff) and unparseable *text* as a drop, so this must raise, not return ""."""
    response = await get_client().chat.completions.create(
        model=model, messages=messages, **kwargs
    )
    if not response.choices:
        err = getattr(response, "error", None)
        raise RuntimeError(f"completion returned no choices (model={model}): {err or response}")
    return response.choices[0].message.content or ""


async def stream_chat(model: str, messages: list[dict], tools: list[dict] | None = None):
    """Stream one turn. Yields ('text', delta) for content tokens, then a final
    ('message', {role, content, tool_calls}) once the turn completes — so the caller
    can forward tokens live AND inspect tool_calls to drive the agent loop. Tool-call
    fragments arrive as indexed deltas and are reassembled here."""
    kwargs: dict = {"model": model, "messages": messages, "stream": True}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    stream = await get_client().chat.completions.create(**kwargs)

    content_parts: list[str] = []
    tool_acc: dict[int, dict] = {}  # index -> {id, name, arguments(str)}
    async for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.content:
            content_parts.append(delta.content)
            yield ("text", delta.content)
        for tc in (delta.tool_calls or []):
            slot = tool_acc.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
            if tc.id:
                slot["id"] = tc.id
            if tc.function and tc.function.name:
                slot["name"] = tc.function.name
            if tc.function and tc.function.arguments:
                slot["arguments"] += tc.function.arguments
    calls = [tool_acc[i] for i in sorted(tool_acc)]
    yield ("message", {
        "role": "assistant",
        "content": "".join(content_parts) or None,
        "tool_calls": calls,
    })
