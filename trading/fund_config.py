"""Fund configuration, from TOML with environment overrides.

The watchlist used to live as a Python literal in `dashboard/__main__.py`,
which meant changing what the fund trades was a code edit. It is data, so it
lives in `config/fund.toml` now.

`tomllib` is stdlib on 3.11+, so this costs no dependency.

Precedence: environment > TOML > defaults. Environment wins because that is
where deployment-specific values (bankroll, kill-switch limit) belong and
because it lets a value be changed without touching a tracked file.

Every default here is deliberately conservative. A config file that is absent
or malformed yields a fund that trades **nothing** — an empty watchlist is a
safe failure, while a default watchlist would mean a missing file silently
starts trading names nobody chose.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


DEFAULT_CONFIG_PATH = Path("config/fund.toml")


@dataclass(frozen=True)
class FundConfig:
    equity_watchlist: tuple[str, ...] = ()
    crypto_watchlist: tuple[str, ...] = ()
    bankroll_usd: float = 1_000.0
    cycle_interval_seconds: float = 300.0
    max_daily_loss_usd: float = 50.0
    max_candidates_per_cycle: int = 5
    lookback_bars: int = 60
    account_equity_usd: float = 0.0        # for the PDT threshold check
    data_provider: str = "none"            # none | static | <vendor>
    resume_max_age_seconds: float = 3600.0
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_tradable(self) -> bool:
        return bool(self.equity_watchlist or self.crypto_watchlist)

    def summary(self) -> dict:
        return {
            "equity_watchlist": list(self.equity_watchlist),
            "crypto_watchlist": list(self.crypto_watchlist),
            "bankroll_usd": self.bankroll_usd,
            "cycle_interval_seconds": self.cycle_interval_seconds,
            "max_daily_loss_usd": self.max_daily_loss_usd,
            "data_provider": self.data_provider,
            "tradable": self.is_tradable,
            "warnings": list(self.warnings),
        }


def load_config(path: Path | str | None = None) -> FundConfig:
    """Load the fund config. Never raises — a broken file yields a safe fund."""
    target = Path(path) if path else Path(
        os.environ.get("FUND_CONFIG_PATH", DEFAULT_CONFIG_PATH)
    )
    warnings: list[str] = []
    raw: dict[str, Any] = {}

    if target.exists():
        try:
            raw = tomllib.loads(target.read_text())
        except (tomllib.TOMLDecodeError, OSError) as e:
            warnings.append(
                f"config at {target} could not be read ({type(e).__name__}); "
                "falling back to defaults — the fund will trade nothing"
            )
    else:
        warnings.append(
            f"no config at {target}; the fund will trade nothing until a "
            "watchlist is configured"
        )

    fund = raw.get("fund", {}) if isinstance(raw.get("fund"), dict) else {}
    watch = raw.get("watchlist", {}) if isinstance(raw.get("watchlist"), dict) else {}
    data = raw.get("data", {}) if isinstance(raw.get("data"), dict) else {}

    equity = _symbols(watch.get("equity"), "FUND_EQUITY_WATCHLIST")
    crypto = _symbols(watch.get("crypto"), "FUND_CRYPTO_WATCHLIST")

    config = FundConfig(
        equity_watchlist=equity,
        crypto_watchlist=crypto,
        bankroll_usd=_number("FUND_BANKROLL_USD", fund.get("bankroll_usd"), 1_000.0, warnings),
        cycle_interval_seconds=_number(
            "FUND_CYCLE_SECONDS", fund.get("cycle_interval_seconds"), 300.0, warnings
        ),
        max_daily_loss_usd=_number(
            "FUND_MAX_DAILY_LOSS_USD", fund.get("max_daily_loss_usd"), 50.0, warnings
        ),
        max_candidates_per_cycle=int(
            _number("FUND_MAX_CANDIDATES", fund.get("max_candidates_per_cycle"), 5, warnings)
        ),
        lookback_bars=int(_number("FUND_LOOKBACK_BARS", fund.get("lookback_bars"), 60, warnings)),
        account_equity_usd=_number(
            "FUND_ACCOUNT_EQUITY_USD", fund.get("account_equity_usd"), 0.0, warnings
        ),
        data_provider=str(
            os.environ.get("FUND_DATA_PROVIDER") or data.get("provider") or "none"
        ).lower(),
        resume_max_age_seconds=_number(
            "FUND_RESUME_MAX_AGE_SECONDS", fund.get("resume_max_age_seconds"), 3600.0, warnings
        ),
        warnings=tuple(warnings + _sanity_warnings(equity, crypto, fund, data)),
    )
    return config


def _sanity_warnings(equity, crypto, fund: dict, data: dict) -> list[str]:
    out: list[str] = []
    if not equity and not crypto:
        out.append("watchlist is empty — no candidates will be considered")
    provider = str(
        os.environ.get("FUND_DATA_PROVIDER") or data.get("provider") or "none"
    ).lower()
    if provider == "none" and (equity or crypto):
        out.append(
            "a watchlist is configured but data.provider is 'none' — "
            "no price history will be available, so every candidate will be "
            "pre-screened out for having no exit plan"
        )
    bankroll = _number("FUND_BANKROLL_USD", fund.get("bankroll_usd"), 1_000.0)
    equity_usd = _number("FUND_ACCOUNT_EQUITY_USD", fund.get("account_equity_usd"), 0.0)
    if 0 < equity_usd < 25_000:
        out.append(
            f"account equity ${equity_usd:,.0f} is under $25,000 — the PDT rule "
            "binds: 3 day trades per 5 business days on equities"
        )
    loss_cap = _number("FUND_MAX_DAILY_LOSS_USD", fund.get("max_daily_loss_usd"), 50.0)
    if bankroll and loss_cap > bankroll * 0.25:
        out.append(
            f"max_daily_loss_usd (${loss_cap:,.0f}) exceeds 25% of bankroll "
            f"(${bankroll:,.0f}) — the kill-switch will rarely fire before real damage"
        )
    return out


def _symbols(value: Any, env_key: str) -> tuple[str, ...]:
    override = os.environ.get(env_key)
    if override is not None:
        value = [s.strip() for s in override.split(",")]
    if not isinstance(value, (list, tuple)):
        return ()
    # Uppercased and de-duplicated while preserving order.
    seen, out = set(), []
    for item in value:
        symbol = str(item).strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            out.append(symbol)
    return tuple(out)


def _number(
    env_key: str,
    value: Any,
    default: float,
    warnings: Optional[list[str]] = None,
) -> float:
    """Env > file > default, with a malformed env value falling back to the FILE.

    The subtle part: an unparseable override must not skip past the configured
    value to the library default. A typo in `FUND_MAX_DAILY_LOSS_USD` would
    otherwise reset the kill-switch to a number nobody chose, silently. It
    falls back one step and says so.
    """
    raw = os.environ.get(env_key)
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            if warnings is not None:
                warnings.append(
                    f"{env_key}={raw!r} is not a number; using the configured "
                    "value instead"
                )
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        if warnings is not None:
            warnings.append(
                f"config value for {env_key} is not a number; using default {default}"
            )
        return default


def is_thesis_stale(created: Optional[float], *, max_age_seconds: float, now: float) -> bool:
    """True when an interrupted thesis is too old to act on.

    A deliberation reasons about prices at a moment in time. Resuming one built
    on yesterday's tape would apply a stale conclusion to a market that has
    moved, so aged theses are abandoned rather than resumed.
    """
    if created is None:
        return True
    return (now - created) > max_age_seconds
