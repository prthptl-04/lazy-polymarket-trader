from cache.prompt_cache import (
    DEFAULT_SONNET_THINKING_BUDGET,
    cached_create,
    resolve_thinking_budget,
)


class _FakeClient:
    def __init__(self) -> None:
        self.last_kwargs = None

    class _Messages:
        def __init__(self, parent): self.parent = parent
        def create(self, **kwargs):
            self.parent.last_kwargs = kwargs
            class _Resp: pass
            return _Resp()

    @property
    def messages(self): return self._Messages(self)


def test_budget_auto_enables_on_sonnet():
    assert resolve_thinking_budget("claude-sonnet-4-6", None) == DEFAULT_SONNET_THINKING_BUDGET


def test_budget_disabled_on_opus():
    assert resolve_thinking_budget("claude-opus-4-7", None) == 0


def test_explicit_zero_overrides_default():
    assert resolve_thinking_budget("claude-sonnet-4-6", 0) == 0


def test_explicit_positive_overrides_default():
    assert resolve_thinking_budget("claude-sonnet-4-6", 3000) == 3000
    assert resolve_thinking_budget("claude-opus-4-7", 1500) == 1500


def test_cached_create_attaches_thinking_on_sonnet():
    fake = _FakeClient()
    cached_create(fake, system="s", messages=[{"role": "user", "content": "hi"}], model="claude-sonnet-4-6")
    assert "thinking" in fake.last_kwargs
    assert fake.last_kwargs["thinking"]["budget_tokens"] == DEFAULT_SONNET_THINKING_BUDGET


def test_cached_create_no_thinking_on_opus_by_default():
    fake = _FakeClient()
    cached_create(fake, system="s", messages=[{"role": "user", "content": "hi"}], model="claude-opus-4-7")
    assert "thinking" not in fake.last_kwargs


def test_cached_create_still_caches_system_prompt():
    fake = _FakeClient()
    cached_create(fake, system="s", messages=[{"role": "user", "content": "hi"}], model="claude-opus-4-7")
    system_block = fake.last_kwargs["system"][0]
    assert system_block["cache_control"] == {"type": "ephemeral"}
