"""Parse pytest output into a structured TestHealth record."""

from __future__ import annotations

import re
from dataclasses import dataclass


_SUMMARY_RE = re.compile(
    r"(?:(?P<passed>\d+)\s+passed)?"
    r"(?:.*?(?P<failed>\d+)\s+failed)?"
    r"(?:.*?(?P<errors>\d+)\s+error[s]?)?"
    r"(?:.*?(?P<skipped>\d+)\s+skipped)?"
    r"(?:.*?in\s+(?P<duration>[\d.]+)s)?"
)


@dataclass(frozen=True)
class TestHealth:
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_seconds: float = 0.0
    raw_summary_line: str = ""

    @property
    def healthy(self) -> bool:
        return self.failed == 0 and self.errors == 0


def parse_pytest_summary(output: str) -> TestHealth:
    """Pull the summary line out of a `pytest -q` output blob.

    pytest's last line is something like '49 passed in 0.20s' or
    '47 passed, 2 failed in 0.30s'. We grab counts off that.
    """
    summary_line = ""
    for line in reversed(output.splitlines()):
        line = line.strip()
        if not line:
            continue
        if "passed" in line or "failed" in line or "error" in line:
            summary_line = line.lstrip("=").strip("= ").strip()
            break
    if not summary_line:
        return TestHealth(raw_summary_line=output.strip().splitlines()[-1] if output.strip() else "")

    m = _SUMMARY_RE.search(summary_line)
    if not m:
        return TestHealth(raw_summary_line=summary_line)

    def _int(name: str) -> int:
        v = m.group(name)
        return int(v) if v else 0

    duration_str = m.group("duration")
    duration = float(duration_str) if duration_str else 0.0

    return TestHealth(
        passed=_int("passed"),
        failed=_int("failed"),
        errors=_int("errors"),
        skipped=_int("skipped"),
        duration_seconds=duration,
        raw_summary_line=summary_line,
    )
