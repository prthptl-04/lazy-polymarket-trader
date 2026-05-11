"""browser-use fallback for wallet-connect and UI-only Polymarket flows.

Per the System Architect Skill: wallet-signing flows MUST run headed so the user
can intervene. CLOB API is preferred for everything else.
"""

from __future__ import annotations


def open_wallet_connect_session():
    """Open a headed browser-use session positioned at Polymarket's connect flow.

    Returns the browser handle so the caller can drive it. We do NOT click
    sign-and-send automatically — the user approves wallet actions manually.
    """
    try:
        from browser_use import Browser
    except ImportError as e:
        raise RuntimeError(
            "browser-use is not installed. Run `uv sync && uvx browser-use install`."
        ) from e

    # Headed, NOT headless — per the safety rule in .claude/skills/system-architect/SKILL.md
    return Browser()
