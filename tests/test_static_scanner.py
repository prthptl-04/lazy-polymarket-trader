from pathlib import Path

import pytest

from vulnerability_detector.static_scanner import StaticScanner


def _write(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def _scan(tmp_path: Path) -> list:
    return StaticScanner(tmp_path).scan()


# -------- POLY-001 print/log secrets --------

def test_detects_printing_polymarket_private_key(tmp_path: Path):
    _write(tmp_path, "trading/leaky.py", "import os\nPOLYMARKET_PRIVATE_KEY = os.environ['POLYMARKET_PRIVATE_KEY']\nprint(POLYMARKET_PRIVATE_KEY)\n")
    findings = _scan(tmp_path)
    assert any(f.category == "POLY-001" for f in findings)


def test_does_not_flag_print_of_unrelated_variable(tmp_path: Path):
    _write(tmp_path, "trading/clean.py", "x = 1\nprint(x)\n")
    findings = _scan(tmp_path)
    assert not any(f.category == "POLY-001" for f in findings)


# -------- POLY-006 subprocess shell=True --------

def test_detects_subprocess_shell_true(tmp_path: Path):
    _write(tmp_path, "trading/x.py", "import subprocess\nsubprocess.run('ls', shell=True)\n")
    findings = _scan(tmp_path)
    assert any(f.category == "POLY-006" for f in findings)


def test_subprocess_argv_list_is_clean(tmp_path: Path):
    _write(tmp_path, "trading/x.py", "import subprocess\nsubprocess.run(['ls', '-la'], capture_output=True)\n")
    findings = _scan(tmp_path)
    assert not any(f.category == "POLY-006" for f in findings)


# -------- POLY-008 SQL injection --------

def test_detects_sql_via_fstring(tmp_path: Path):
    _write(tmp_path, "memory/x.py", "import sqlite3\nconn = sqlite3.connect(':memory:')\nname = 'x'\nconn.execute(f'SELECT * FROM t WHERE n = {name}')\n")
    findings = _scan(tmp_path)
    assert any(f.category == "POLY-008" for f in findings)


def test_detects_sql_via_concat(tmp_path: Path):
    _write(tmp_path, "memory/x.py", "import sqlite3\nconn = sqlite3.connect(':memory:')\nname = 'x'\nconn.execute('SELECT * FROM t WHERE n = ' + name)\n")
    findings = _scan(tmp_path)
    assert any(f.category == "POLY-008" for f in findings)


def test_parameterized_sql_is_clean(tmp_path: Path):
    _write(tmp_path, "memory/x.py", "import sqlite3\nconn = sqlite3.connect(':memory:')\nname = 'x'\nconn.execute('SELECT * FROM t WHERE n = ?', (name,))\n")
    findings = _scan(tmp_path)
    assert not any(f.category == "POLY-008" for f in findings)


# -------- POLY-009 unsafe deserialization --------

def test_detects_pickle_loads(tmp_path: Path):
    _write(tmp_path, "x.py", "import pickle\npickle.loads(b'data')\n")
    findings = _scan(tmp_path)
    assert any(f.category == "POLY-009" for f in findings)


def test_detects_eval_call(tmp_path: Path):
    _write(tmp_path, "x.py", "x = eval('1+1')\n")
    findings = _scan(tmp_path)
    assert any(f.category == "POLY-009" for f in findings)


def test_yaml_safe_load_is_clean(tmp_path: Path):
    _write(tmp_path, "x.py", "import yaml\nyaml.safe_load('a: 1')\n")
    findings = _scan(tmp_path)
    assert not any(f.category == "POLY-009" for f in findings)


def test_yaml_load_with_loader_is_flagged(tmp_path: Path):
    _write(tmp_path, "x.py", "import yaml\nyaml.load('a: 1')\n")
    findings = _scan(tmp_path)
    assert any(f.category == "POLY-009" for f in findings)


# -------- POLY-010 hardcoded secrets --------

def test_detects_ethereum_private_key_literal(tmp_path: Path):
    secret = "0x" + "ab" * 32
    _write(tmp_path, "x.py", f"k = '{secret}'\n")
    findings = _scan(tmp_path)
    assert any(f.category == "POLY-010" for f in findings)


def test_detects_anthropic_key_literal(tmp_path: Path):
    _write(tmp_path, "x.py", "k = 'sk-ant-abc123XYZ456abc123XYZ456'\n")
    findings = _scan(tmp_path)
    assert any(f.category == "POLY-010" for f in findings)


def test_comment_with_key_pattern_is_ignored(tmp_path: Path):
    # Commented-out example shouldn't trigger.
    _write(tmp_path, "x.py", "# example: 0x" + "ab" * 32 + "\n")
    findings = _scan(tmp_path)
    assert not any(f.category == "POLY-010" for f in findings)


# -------- POLY-011 disabled HTTPS verification --------

def test_detects_ssl_check_hostname_false(tmp_path: Path):
    _write(tmp_path, "x.py", "import ssl\nctx = ssl.create_default_context()\nctx.check_hostname = False\n")
    findings = _scan(tmp_path)
    assert any(f.category == "POLY-011" for f in findings)


def test_detects_requests_verify_false(tmp_path: Path):
    _write(tmp_path, "x.py", "import requests\nrequests.get('https://x', verify=False)\n")
    findings = _scan(tmp_path)
    assert any(f.category == "POLY-011" for f in findings)


# -------- POLY-005 headless wallet --------

def test_detects_headless_browser_in_wallet_context(tmp_path: Path):
    _write(tmp_path, "trading/browser_fallback.py", "from browser_use import Browser\nBrowser(headless=True)\n")
    findings = _scan(tmp_path)
    assert any(f.category == "POLY-005" for f in findings)


def test_does_not_flag_headless_outside_wallet_context(tmp_path: Path):
    _write(tmp_path, "scratch/random.py", "from browser_use import Browser\nBrowser(headless=True)\n")
    findings = _scan(tmp_path)
    assert not any(f.category == "POLY-005" for f in findings)


# -------- POLY-003 lane crossing --------

def test_detects_trading_importing_from_undocumented_verification_module(tmp_path: Path):
    # `verification.outcome_grader` is on the documented public-interface allowlist,
    # so importing it is fine. Importing something else from verification/ is a finding.
    _write(tmp_path, "trading/bad.py", "from verification.private_helper import secret\n")
    findings = _scan(tmp_path)
    assert any(f.category == "POLY-003" for f in findings)


def test_imports_of_documented_public_interfaces_are_clean(tmp_path: Path):
    _write(tmp_path, "trading/ok.py", "from verification.outcome_grader import OutcomeGrader\n")
    findings = _scan(tmp_path)
    assert not any(f.category == "POLY-003" for f in findings)


def test_tests_directory_skips_lane_crossing_check(tmp_path: Path):
    _write(tmp_path, "tests/test_x.py", "from trading.execution import Executor\n")
    findings = _scan(tmp_path)
    assert not any(f.category == "POLY-003" for f in findings)


def test_lane_exempt_path_is_clean(tmp_path: Path):
    # memory/ is on the exempt list — utility, not a specialist lane.
    _write(tmp_path, "memory/x.py", "from verification.private_helper import secret\n")
    findings = _scan(tmp_path)
    assert not any(f.category == "POLY-003" for f in findings)


def test_tests_directory_skips_hardcoded_secret_check(tmp_path: Path):
    _write(tmp_path, "tests/test_x.py", "FAKE = 'sk-ant-abc123XYZ456abc123XYZ456'\n")
    findings = _scan(tmp_path)
    assert not any(f.category == "POLY-010" for f in findings)


# -------- scanner end-to-end --------

def test_scanner_excludes_venv_and_dotgit(tmp_path: Path):
    _write(tmp_path, ".venv/lib/sneaky.py", "k = '0x" + "ab" * 32 + "'\n")
    _write(tmp_path, ".git/hooks/post-commit.py", "exec('x')\n")
    findings = _scan(tmp_path)
    assert findings == []


def test_scanner_handles_syntax_error_files_gracefully(tmp_path: Path):
    _write(tmp_path, "broken.py", "def x(:\n")
    _write(tmp_path, "ok.py", "subprocess.run('ls', shell=True)\nimport subprocess\n")
    # Should not raise; broken file is skipped, ok.py still scanned.
    findings = _scan(tmp_path)
    assert any(f.category == "POLY-006" for f in findings)


def test_vendor_clones_are_not_scanned(tmp_path):
    """Upstream `<name>-src/` clones are gitignored and never published, so
    their findings are somebody else's code, not ours."""
    _write(tmp_path, "ours.py", "x = 1\n")
    _write(tmp_path, ".claude/skills/agents-cli-src/evil.py", "exec('danger')\n")

    findings = StaticScanner(tmp_path).scan()
    assert not any("-src" in f.file for f in findings)


def test_vendor_clone_exclusion_does_not_hide_our_own_code(tmp_path):
    """The suffix rule must not become a blanket amnesty."""
    _write(tmp_path, "mine.py", "exec('danger')\n")

    findings = StaticScanner(tmp_path).scan()
    assert any(f.file == "mine.py" for f in findings)
