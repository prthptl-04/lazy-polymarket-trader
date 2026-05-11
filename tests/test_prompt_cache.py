from cache.prompt_cache import cache_usage_summary, cached_system_block


def test_system_block_has_cache_control():
    block = cached_system_block("system prompt")
    assert block["type"] == "text"
    assert block["cache_control"] == {"type": "ephemeral"}
    assert block["text"] == "system prompt"


def test_cache_usage_summary_handles_missing_usage():
    class _NoUsage:
        usage = None

    summary = cache_usage_summary(_NoUsage())
    assert summary == {"cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}


def test_cache_usage_summary_reads_fields():
    class _Usage:
        cache_creation_input_tokens = 12
        cache_read_input_tokens = 34

    class _Resp:
        usage = _Usage()

    summary = cache_usage_summary(_Resp())
    assert summary == {"cache_creation_input_tokens": 12, "cache_read_input_tokens": 34}
