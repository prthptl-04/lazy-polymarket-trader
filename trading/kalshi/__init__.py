"""Kalshi KXBTC15M — the 15-minute BTC binary.

Deliberately separate from the equity path. `ExitPlan` has an entry, a stop, a
target and an ATR; a contract that settles itself has none of those four, and
generalising the one to cover the other would put an `if is_binary` in every
invariant the equity path has learned the hard way.
"""
