from typing import Any

DEFAULT_MODEL = "claude-opus-4-7"


def cached_system_block(text: str) -> dict[str, Any]:
    """Wrap a system prompt so it participates in Anthropic prompt caching."""
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


def cached_create(
    client,
    *,
    system: str,
    messages: list[dict[str, Any]],
    model: str = DEFAULT_MODEL,
    max_tokens: int = 2048,
    tools: list[dict] | None = None,
) -> Any:
    """Wrapper around client.messages.create that always cache-tags the system prompt.

    Per CLAUDE.md rule #2, every Anthropic call must route through this function.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [cached_system_block(system)],
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
    return client.messages.create(**kwargs)


def cache_usage_summary(response) -> dict[str, int]:
    """Extract cache hit/miss token counts from a Messages API response."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return {"cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    return {
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
    }
