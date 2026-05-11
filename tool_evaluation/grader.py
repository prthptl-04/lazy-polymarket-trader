"""Per-case scoring. Mirrors the cookbook's exact-match contract.

A case passes when either:
  - the call returns and `actual == expected`, OR
  - the call raises an exception that is an instance of `expects_exception`.

Anything else fails the case.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class CaseResult:
    passed: bool
    actual: Any
    error: str | None
    duration_seconds: float


def _equal(actual: Any, expected: Any, tolerance: float) -> bool:
    if isinstance(actual, float) or isinstance(expected, float):
        try:
            return abs(float(actual) - float(expected)) <= tolerance
        except (TypeError, ValueError):
            return False
    return actual == expected


def score_case(
    fn: Callable[..., Any],
    *,
    args: tuple = (),
    kwargs: dict | None = None,
    expected: Any = None,
    expects_exception: type[BaseException] | None = None,
    tolerance: float = 1e-9,
) -> CaseResult:
    import time

    kwargs = kwargs or {}
    started = time.perf_counter()
    try:
        actual = fn(*args, **kwargs)
        elapsed = time.perf_counter() - started
        if expects_exception is not None:
            return CaseResult(False, actual, f"expected {expects_exception.__name__}, returned {actual!r}", elapsed)
        return CaseResult(_equal(actual, expected, tolerance), actual, None, elapsed)
    except BaseException as e:
        elapsed = time.perf_counter() - started
        if expects_exception is not None and isinstance(e, expects_exception):
            return CaseResult(True, None, f"{type(e).__name__}: {e}", elapsed)
        return CaseResult(False, None, f"{type(e).__name__}: {e}", elapsed)
