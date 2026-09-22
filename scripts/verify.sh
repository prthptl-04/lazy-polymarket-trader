#!/usr/bin/env bash
#
# One command, fixed order, same answer every time.
#
# The idea is QuantMind's `scripts/verify.sh` (LLMQuant/quant-mind, MIT): a
# repository should carry its own definition of "green" rather than leaving it
# in a contributor's head. Before this, the gates in CLAUDE.md #3 and #10 were
# run ad hoc, in whatever order someone remembered, which is how a step gets
# quietly skipped on the day it would have caught something.
#
# Order is deliberate — cheapest and most-likely-to-fail first, so a broken
# build is reported in seconds rather than after a full scan:
#
#   1. tests          the contract
#   2. self-checks    the modules that ship a __main__ demo
#   3. ui build       type errors the Python suite cannot see
#   4. vuln scan      CLAUDE.md #10, blocks a publish at high/critical
#
# Runs every step even after one fails, then exits non-zero: a report that
# stops at the first failure hides how much else is broken. Nothing mutates.

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

PY=".venv/bin/python3"
[ -x "$PY" ] || PY="python3"
fail=0

step() { printf '\n\033[1m-- %s\033[0m\n' "$1"; }

step "1/4  tests"
$PY -m pytest -q || fail=1

step "2/4  module self-checks"
for m in roundtable.replay roundtable.knowledge trading.catalysts \
         monitoring.paper_report backtest.committee; do
  if $PY -m "$m" --demo >/dev/null 2>&1; then echo "  ok   $m"
  else echo "  FAIL $m"; fail=1; fi
done

step "3/4  ui build"
if [ -d ui/node_modules ]; then
  (cd ui && npm run build >/dev/null 2>&1) && echo "  ok   ui builds" \
    || { echo "  FAIL ui build"; fail=1; }
else
  echo "  skip ui (npm install not run)"
fi

step "4/4  vulnerability scan"
$PY - <<'PYEOF' || fail=1
from vulnerability_detector import VulnerabilityDetectionAgent
findings = getattr(VulnerabilityDetectionAgent(root=".").run(), "findings", [])
blocking = [f for f in findings
            if str(getattr(f, "severity", "")).lower() in ("high", "critical")]
print(f"  {len(findings)} finding(s), {len(blocking)} blocking")
for f in blocking:
    print("  BLOCKING:", f)
raise SystemExit(1 if blocking else 0)
PYEOF

if [ "$fail" -eq 0 ]; then printf '\n\033[32mverify passed\033[0m\n'
else printf '\n\033[31mverify FAILED\033[0m\n'; fi
exit "$fail"
