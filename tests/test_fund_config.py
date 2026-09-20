"""Fund config + wiring + the stale-thesis resume policy.

- Blind: a missing or broken config must yield a fund that trades NOTHING.
  A default watchlist would mean an unread config silently trades names nobody
  chose.
- Acceptance: TOML parses, env overrides win, the fund assembles.
- Edge: stale theses are abandoned rather than surfaced forever.
"""

import time
from datetime import datetime

import pytest

from dashboard.fund_wiring import build_data_provider, build_fund
from memory.store import MemoryStore
from trading.fund import FundLoop
from trading.fund_config import FundConfig, is_thesis_stale, load_config
from trading.market_data import StaticProvider, VenueQuoteProvider
from trading.sessions import EASTERN


TOML = """
[fund]
bankroll_usd = 5000
cycle_interval_seconds = 120
max_daily_loss_usd = 100
max_candidates_per_cycle = 3
account_equity_usd = 8000

[watchlist]
equity = ["aapl", "MSFT", "aapl"]
crypto = ["BTC"]

[data]
provider = "static"
"""


def _write(tmp_path, text=TOML):
    path = tmp_path / "fund.toml"
    path.write_text(text)
    return path


# ---------------- safe defaults ----------------

def test_missing_config_trades_nothing(tmp_path):
    cfg = load_config(tmp_path / "absent.toml")
    assert cfg.equity_watchlist == ()
    assert cfg.crypto_watchlist == ()
    assert cfg.is_tradable is False
    assert any("no config" in w for w in cfg.warnings)


def test_broken_toml_trades_nothing(tmp_path):
    cfg = load_config(_write(tmp_path, "this is [not valid toml"))
    assert cfg.is_tradable is False
    assert any("could not be read" in w for w in cfg.warnings)


def test_empty_watchlist_is_warned(tmp_path):
    cfg = load_config(_write(tmp_path, "[fund]\nbankroll_usd = 100\n"))
    assert any("watchlist is empty" in w for w in cfg.warnings)


# ---------------- parsing ----------------

def test_toml_is_parsed(tmp_path):
    cfg = load_config(_write(tmp_path))
    assert cfg.bankroll_usd == 5000.0
    assert cfg.cycle_interval_seconds == 120.0
    assert cfg.max_candidates_per_cycle == 3
    assert cfg.data_provider == "static"


def test_symbols_are_uppercased_and_deduped(tmp_path):
    cfg = load_config(_write(tmp_path))
    assert cfg.equity_watchlist == ("AAPL", "MSFT")
    assert cfg.crypto_watchlist == ("BTC",)


def test_env_overrides_toml(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_BANKROLL_USD", "777")
    monkeypatch.setenv("FUND_EQUITY_WATCHLIST", "nvda, tsla")
    cfg = load_config(_write(tmp_path))

    assert cfg.bankroll_usd == 777.0
    assert cfg.equity_watchlist == ("NVDA", "TSLA")


def test_bad_env_value_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_BANKROLL_USD", "not-a-number")
    assert load_config(_write(tmp_path)).bankroll_usd == 5000.0


def test_config_path_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_CONFIG_PATH", str(_write(tmp_path)))
    assert load_config().bankroll_usd == 5000.0


# ---------------- sanity warnings ----------------

def test_sub_25k_account_is_warned_about_pdt(tmp_path):
    cfg = load_config(_write(tmp_path))
    assert any("PDT rule binds" in w for w in cfg.warnings)


def test_watchlist_without_a_provider_is_warned(tmp_path):
    text = TOML.replace('provider = "static"', 'provider = "none"')
    cfg = load_config(_write(tmp_path, text))
    assert any("pre-screened out" in w for w in cfg.warnings)


def test_oversized_loss_cap_is_warned(tmp_path):
    text = """
[fund]
bankroll_usd = 1000
max_daily_loss_usd = 900
[watchlist]
equity = ["AAPL"]
"""
    cfg = load_config(_write(tmp_path, text))
    assert any("kill-switch will rarely fire" in w for w in cfg.warnings)


def test_summary_shape(tmp_path):
    s = load_config(_write(tmp_path)).summary()
    for key in ("equity_watchlist", "bankroll_usd", "data_provider",
                "tradable", "warnings"):
        assert key in s


# ---------------- wiring ----------------

def test_fund_is_built_without_a_watchlist_because_the_scout_finds_candidates(tmp_path):
    """An empty watchlist is the normal case now — the scout screens the tape."""
    cfg = load_config(tmp_path / "absent.toml")
    sched = build_fund(config=cfg, anthropic_client=object())
    assert sched is not None
    assert sched.fund.scout is not None
    assert sched.fund.equity_watchlist == ()


def test_a_configured_watchlist_overrides_the_scout(tmp_path):
    from trading.fund_config import FundConfig
    sched = build_fund(config=FundConfig(equity_watchlist=("AAPL",)),
                       anthropic_client=object())
    assert sched.fund.scout is None
    assert sched.fund.equity_watchlist == ("AAPL",)


def test_fund_not_built_without_a_client(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = load_config(_write(tmp_path))
    assert build_fund(config=cfg) is None


def test_fund_assembles_with_config_and_client(tmp_path):
    cfg = load_config(_write(tmp_path))
    sched = build_fund(config=cfg, anthropic_client=object(),
                       memory=MemoryStore(db_path=str(tmp_path / "m.db")))

    assert sched is not None
    assert sched.cycle_interval_seconds == 120.0
    assert sched.fund.equity_watchlist == ("AAPL", "MSFT")
    assert sched.fund.kill_switch.max_daily_loss_usd == 100.0
    assert sched.fund.router.pdt.account_equity_usd == 8000.0


def test_assembled_fund_defaults_to_paper():
    """Flipping to live is the rule #13 checklist, not a config key."""
    cfg = FundConfig(equity_watchlist=("AAPL",), bankroll_usd=1000.0)
    sched = build_fund(config=cfg, anthropic_client=object())
    assert sched.venue.name == "paper"


def test_provider_none_is_quotes_only():
    cfg = FundConfig(equity_watchlist=("AAPL",), data_provider="none")
    provider = build_data_provider(cfg, venue=object())
    assert isinstance(provider, VenueQuoteProvider)


def test_provider_static_is_static():
    cfg = FundConfig(equity_watchlist=("AAPL",), data_provider="static")
    assert isinstance(build_data_provider(cfg, venue=object()), StaticProvider)


def test_unknown_provider_degrades_to_quotes_only():
    cfg = FundConfig(equity_watchlist=("AAPL",), data_provider="not-a-vendor")
    assert isinstance(build_data_provider(cfg, venue=object()), VenueQuoteProvider)


def test_massive_is_a_real_provider_now():
    """It used to be the example of an unknown vendor; it is wired as of 52b574d.

    It is no longer the TOP of the stack: quotes come from the venue, because
    the price that matters is the one the broker would fill at. Massive remains
    the source of bars and news behind it.
    """
    from trading.market_data import VenueQuoteProvider
    from trading.massive_provider import MassiveProvider
    cfg = FundConfig(equity_watchlist=("AAPL",), data_provider="massive")
    provider = build_data_provider(cfg, venue=object())
    assert isinstance(provider, VenueQuoteProvider)
    assert isinstance(provider.fallback, MassiveProvider)


def test_shipped_config_trades_nothing():
    """The committed config/fund.toml must not start trading on its own."""
    cfg = load_config("config/fund.toml")
    assert cfg.is_tradable is False


# ---------------- stale thesis policy ----------------

def test_staleness_check():
    now = 1_000_000.0
    assert is_thesis_stale(now - 10, max_age_seconds=3600, now=now) is False
    assert is_thesis_stale(now - 7200, max_age_seconds=3600, now=now) is True
    assert is_thesis_stale(None, max_age_seconds=3600, now=now) is True


@pytest.mark.asyncio
async def test_fresh_theses_are_surfaced(tmp_path):
    store = MemoryStore(db_path=str(tmp_path / "m.db"))
    store.save_deliberation("fresh", "AAPL", "equity", "in_progress", {})

    loop = FundLoop(router=None, pipeline=None, round_table=None, data=None,
                    memory=store, resume_max_age_seconds=3600)
    assert await loop.resume_unfinished() == ["fresh"]


@pytest.mark.asyncio
async def test_stale_theses_are_abandoned_not_surfaced(tmp_path):
    """Otherwise every GO reports the same weeks-old ghosts forever."""
    store = MemoryStore(db_path=str(tmp_path / "m.db"))
    store.save_deliberation("old", "AAPL", "equity", "in_progress", {})

    loop = FundLoop(router=None, pipeline=None, round_table=None, data=None,
                    memory=store, resume_max_age_seconds=60)

    surfaced = await loop.resume_unfinished(now=time.time() + 7200)
    assert surfaced == []
    assert store.get_deliberation("old")["status"] == "abandoned"
    assert store.unfinished_deliberations() == []


@pytest.mark.asyncio
async def test_resume_without_memory_is_empty():
    loop = FundLoop(router=None, pipeline=None, round_table=None, data=None)
    assert await loop.resume_unfinished() == []
