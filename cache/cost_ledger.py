"""What the committee costs to run, split by the mode it was running in.

A fund that spends more on thinking than it makes from trading is losing money
with extra steps, and until this existed nothing in the system could tell. The
ledger answers two questions per mode:

    burned   — USD of model spend
    earned   — USD realised by the trades that spend produced

**Prices are ESTIMATES and say so.** Published per-token rates move, and a
hard-coded number that silently goes stale is worse than one labelled. Every
rate can be overridden from the environment, and `pricing_source` in the
summary reports which rates were actually used.

Cache accounting follows Anthropic's documented multipliers — a cache READ
costs a tenth of an input token, a cache WRITE costs a quarter more than one.
That is the entire reason rule #2 exists, so the ledger has to show it: if
caching is working, cached reads dominate and the bill is a fraction of what
the raw token count implies.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# USD per MILLION tokens. Opus-class defaults; override per deployment.
DEFAULT_INPUT_PER_MTOK = 15.0
DEFAULT_OUTPUT_PER_MTOK = 75.0

# Anthropic's documented cache multipliers, relative to the input rate.
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25

# Gemini is the failover and runs on the free tier. Priced at zero, flagged as
# such, so a month of failover does not look like a month of free lunch.
FREE_PROVIDERS = {"gemini"}

MODES = ("paper", "live")


def _env_float(name: str, default: float) -> tuple[float, bool]:
    raw = os.environ.get(name)
    if raw is None:
        return default, False
    try:
        return float(raw), True
    except ValueError:
        return default, False


@dataclass
class Rates:
    input_per_mtok: float = DEFAULT_INPUT_PER_MTOK
    output_per_mtok: float = DEFAULT_OUTPUT_PER_MTOK
    overridden: bool = False

    @classmethod
    def from_env(cls) -> "Rates":
        i, io = _env_float("LLM_PRICE_INPUT_PER_MTOK", DEFAULT_INPUT_PER_MTOK)
        o, oo = _env_float("LLM_PRICE_OUTPUT_PER_MTOK", DEFAULT_OUTPUT_PER_MTOK)
        return cls(input_per_mtok=i, output_per_mtok=o, overridden=io or oo)

    def cost_usd(self, *, input_tokens: int, output_tokens: int,
                 cache_read: int = 0, cache_write: int = 0) -> float:
        """Cache reads and writes are priced OFF the input rate, and the plain
        input count is assumed to exclude them — which is how the API reports
        it. Double-counting here would overstate the bill by the cache."""
        per_in = self.input_per_mtok / 1_000_000
        per_out = self.output_per_mtok / 1_000_000
        return (
            input_tokens * per_in
            + cache_read * per_in * CACHE_READ_MULTIPLIER
            + cache_write * per_in * CACHE_WRITE_MULTIPLIER
            + output_tokens * per_out
        )


def usage_of(response: Any) -> dict[str, int]:
    """Token counts from an Anthropic response. Absent fields read as zero —
    a provider that reports nothing must not crash the trading loop."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return {"input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_write": 0}

    def pick(*names: str) -> int:
        for n in names:
            v = getattr(usage, n, None)
            if v is None and isinstance(usage, dict):
                v = usage.get(n)
            if v:
                return int(v)
        return 0

    return {
        "input_tokens": pick("input_tokens"),
        "output_tokens": pick("output_tokens"),
        "cache_read": pick("cache_read_input_tokens"),
        "cache_write": pick("cache_creation_input_tokens"),
    }


@dataclass
class CostLedger:
    """Records model spend, tagged with the trading mode that caused it."""

    memory: Any = None
    rates: Rates = field(default_factory=Rates.from_env)
    # Asked at record time rather than passed down every call site, so a mode
    # flip is picked up by the next call without threading an argument through
    # the round table.
    mode_provider: Optional[Callable[[], str]] = None

    def current_mode(self) -> str:
        try:
            mode = (self.mode_provider() if self.mode_provider else "paper") or "paper"
        except Exception:
            mode = "paper"
        return mode if mode in MODES else "paper"

    def record(self, *, provider: str, model: str, response: Any = None,
               usage: Optional[dict] = None, mode: Optional[str] = None,
               thesis_id: Optional[str] = None) -> dict:
        counts = usage if usage is not None else usage_of(response)
        free = provider in FREE_PROVIDERS
        cost = 0.0 if free else self.rates.cost_usd(
            input_tokens=counts["input_tokens"], output_tokens=counts["output_tokens"],
            cache_read=counts["cache_read"], cache_write=counts["cache_write"],
        )
        row = {
            "provider": provider, "model": model,
            "mode": mode or self.current_mode(),
            "thesis_id": thesis_id, "cost_usd": round(cost, 6), **counts,
        }
        if self.memory is not None:
            try:
                self.memory.record_llm_cost(**row)
            except Exception:
                # Losing a cost row must never fail a deliberation.
                pass
        return row

    def summary(self, *, earnings: Optional[dict[str, Optional[float]]] = None) -> dict:
        """Per-mode spend, and what that spend earned.

        `earnings` maps mode → realised USD, or None where the mode has never
        executed. None is reported as unknown rather than as zero: a mode that
        has never traded has not broken even, it has not been tested.
        """
        rows = {}
        if self.memory is not None:
            try:
                rows = self.memory.llm_cost_summary()
            except Exception:
                rows = {}
        earnings = earnings or {}

        out: dict[str, Any] = {"modes": {}, "pricing": {
            "input_per_mtok": self.rates.input_per_mtok,
            "output_per_mtok": self.rates.output_per_mtok,
            "source": "environment override" if self.rates.overridden
                      else "built-in estimate — set LLM_PRICE_*_PER_MTOK to correct it",
            "cache_read_multiplier": CACHE_READ_MULTIPLIER,
            "cache_write_multiplier": CACHE_WRITE_MULTIPLIER,
        }}
        for mode in MODES:
            spend = rows.get(mode) or {}
            burned = round(float(spend.get("cost_usd") or 0.0), 4)
            earned = earnings.get(mode)
            calls = int(spend.get("calls") or 0)
            out["modes"][mode] = {
                "calls": calls,
                "input_tokens": int(spend.get("input_tokens") or 0),
                "output_tokens": int(spend.get("output_tokens") or 0),
                "cache_read": int(spend.get("cache_read") or 0),
                "cache_write": int(spend.get("cache_write") or 0),
                "burned_usd": burned,
                "earned_usd": earned,
                "net_usd": None if earned is None else round(earned - burned, 4),
                "cost_per_call_usd": round(burned / calls, 6) if calls else None,
                # Below 1.0 the committee is being outspent by itself.
                "earn_per_dollar": (
                    None if earned is None or burned <= 0 else round(earned / burned, 2)
                ),
            }
        total_burn = sum(m["burned_usd"] for m in out["modes"].values())
        out["total_burned_usd"] = round(total_burn, 4)
        return out

    def brief(self, summary: Optional[dict] = None) -> str:
        """One compact block for the seats' evidence. Plain text on purpose —
        it is read by a model, not rendered."""
        s = summary or self.summary()
        lines = []
        for mode in MODES:
            m = s["modes"][mode]
            if not m["calls"]:
                continue
            earned = "unknown (no closed trades yet)" if m["earned_usd"] is None \
                else f"${m['earned_usd']:.2f}"
            ratio = "" if m["earn_per_dollar"] is None \
                else f", ${m['earn_per_dollar']:.2f} earned per $1 of model spend"
            lines.append(
                f"{mode}: {m['calls']} model calls, ${m['burned_usd']:.2f} spent, "
                f"{earned} realised{ratio}")
        if not lines:
            return ""
        lines.append(
            "Every seat you speak as costs money. A deliberation that adds nothing "
            "to the decision is a loss even when the trade wins — abstain rather "
            "than pad, and keep reasoning short where the evidence is thin.")
        return "\n".join(lines)
