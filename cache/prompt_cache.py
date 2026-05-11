from typing import Any

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_SONNET_THINKING_BUDGET = 2000


def cached_system_block(text: str) -> dict[str, Any]:
    """Wrap a system prompt so it participates in Anthropic prompt caching."""
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


def resolve_thinking_budget(model: str, thinking_budget_tokens: int | None) -> int:
    """Pick a final thinking budget. Returns 0 to mean 'do not enable thinking'.

    - Explicit positive int → use it.
    - Explicit 0 → disable.
    - None (default): enable with DEFAULT_SONNET_THINKING_BUDGET when model
      is Sonnet; disable otherwise.
    """
    if thinking_budget_tokens is not None:
        return max(0, int(thinking_budget_tokens))
    if "sonnet" in model.lower():
        return DEFAULT_SONNET_THINKING_BUDGET
    return 0


def cached_create(
    client,
    *,
    system: str,
    messages: list[dict[str, Any]],
    model: str = DEFAULT_MODEL,
    max_tokens: int = 2048,
    tools: list[dict] | None = None,
    thinking_budget_tokens: int | None = None,
) -> Any:
    """Wrapper around client.messages.create that always cache-tags the system prompt.

    Per CLAUDE.md rule #2, every Anthropic call must route through this function.

    Extended thinking (cookbook: extended_thinking_with_tool_use) auto-enables
    when the model is Sonnet; pass thinking_budget_tokens explicitly to override.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [cached_system_block(system)],
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
    budget = resolve_thinking_budget(model, thinking_budget_tokens)
    if budget > 0:
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
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
