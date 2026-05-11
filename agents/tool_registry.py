"""Declarative registry of skills, MCPs, and Python tools available per specialist.

This is the single source of truth the OrchestrationManager consults when it
builds the briefing for each specialist. When a new skill/MCP/repo is approved
via headed-browser discovery, add an entry here.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Tool:
    name: str
    kind: str           # "skill" | "mcp" | "python" | "cli"
    location: str       # path, package name, or URL
    purpose: str        # one-line description shown to the agent


@dataclass(frozen=True)
class AgentToolset:
    agent_id: str
    skills: list[Tool] = field(default_factory=list)
    tools: list[Tool] = field(default_factory=list)


# Skills/tools available to every specialist (the manager merges these in).
COMMON_SKILLS: list[Tool] = [
    Tool(
        name="system-architect",
        kind="skill",
        location=".claude/skills/system-architect/SKILL.md",
        purpose="Decision tree + permission/safety model + Polymarket guidance. Consult before adding integrations or retry strategies.",
    ),
    Tool(
        name="web-scraping",
        kind="skill",
        location=".claude/skills/web-scraper/SKILL.md",
        purpose=(
            "yfe404/web-scraper skill (MIT). Adaptive recon + scraping strategy picker. "
            "ALL scrape requests must route through OrchestrationManager.request_scrape — "
            "the trust policy + GitHub authenticator gate every source."
        ),
    ),
    Tool(
        name="vulnerability-detector",
        kind="skill",
        location=".claude/skills/vulnerability-detector/SKILL.md",
        purpose=(
            "Polymarket-bot-specific vulnerability detection (categories POLY-001..POLY-011). "
            "Owned by Forward Deployment; runs deterministically and gates GitHubAgent.publish."
        ),
    ),
    Tool(
        name="financial-applications",
        kind="skill",
        location=".claude/skills/financial-applications/SKILL.md",
        purpose=(
            "Polymarket binary-market financial math: Kelly sizing on YES/NO prices, edge in bps, "
            "Brier calibration, Sharpe/VaR/max-drawdown over the local trade log. Backed by the "
            "deterministic `finance/` package — shared utility, no lane."
        ),
    ),
]

COMMON_TOOLS: list[Tool] = [
    Tool(
        name="memory.store.MemoryStore",
        kind="python",
        location="memory/store.py",
        purpose="Cross-session SQLite memory. ALWAYS call recent_lessons(agent_id) before deciding what to do.",
    ),
    Tool(
        name="cache.prompt_cache.cached_create",
        kind="python",
        location="cache/prompt_cache.py",
        purpose="Mandatory wrapper around Anthropic client.messages.create. Never bypass.",
    ),
]


REGISTRY: dict[str, AgentToolset] = {
    "product": AgentToolset(
        agent_id="product",
        skills=list(COMMON_SKILLS),
        tools=[
            *COMMON_TOOLS,
            Tool(
                name="product.gap_analysis.detect_gaps",
                kind="python",
                location="product/gap_analysis.py",
                purpose="Convert recurring monitoring feedback into structured gap tickets.",
            ),
            Tool(
                name="monitoring.live_feedback.LiveFeedback",
                kind="python",
                location="monitoring/live_feedback.py",
                purpose="Read-only: review runtime feedback events the Forward Deployment agent has recorded.",
            ),
        ],
    ),
    "architect": AgentToolset(
        agent_id="architect",
        skills=list(COMMON_SKILLS),
        tools=[
            *COMMON_TOOLS,
            Tool(
                name="trading.polymarket_client.PolymarketClient",
                kind="python",
                location="trading/polymarket_client.py",
                purpose="CLOB REST wrapper. Prefer this for market data and order placement.",
            ),
            Tool(
                name="trading.browser_fallback.open_wallet_connect_session",
                kind="python",
                location="trading/browser_fallback.py",
                purpose="Headed browser-use fallback for wallet-connect / UI-only Polymarket flows.",
            ),
            Tool(
                name="trading.execution.Executor",
                kind="python",
                location="trading/execution.py",
                purpose="Paper-by-default execution path. Live trading is gated on env + funded wallet.",
            ),
            Tool(
                name="finance.kelly.kelly_size_usd",
                kind="python",
                location="finance/kelly.py",
                purpose=(
                    "Position sizing for binary markets. Returns a KellyResult bounded by "
                    "VerifiedOutcomeCriteria.max_position_usd. Use this BEFORE building a ProposedTrade."
                ),
            ),
        ],
    ),
    "forward_deployment": AgentToolset(
        agent_id="forward_deployment",
        skills=list(COMMON_SKILLS),
        tools=[
            *COMMON_TOOLS,
            Tool(
                name="verification.outcome_grader.OutcomeGrader",
                kind="python",
                location="verification/outcome_grader.py",
                purpose="Deterministic gate. Every proposed trade must pass evaluate() before execution.",
            ),
            Tool(
                name="verification.criteria.VerifiedOutcomeCriteria",
                kind="python",
                location="verification/criteria.py",
                purpose="Risk caps + edge thresholds. Adjust here, not inline.",
            ),
            Tool(
                name="pytest",
                kind="cli",
                location="pytest -q",
                purpose="Run the test suite before marking any phase done (CLAUDE.md rule #3).",
            ),
            Tool(
                name="github_publisher.GitHubAgent",
                kind="python",
                location="github_publisher/agent.py",
                purpose=(
                    "Deterministic publish gate. Call publish(tests_passed=True) after pytest is green. "
                    "Refuses non-private repos, refuses commits containing secret-like paths, requires "
                    "explicit approved=True on the first publish to a new repo."
                ),
            ),
            Tool(
                name="vulnerability_detector.VulnerabilityDetectionAgent",
                kind="python",
                location="vulnerability_detector/agent.py",
                purpose=(
                    "Run before every publish. agent.run() returns a Report; if blocked_publish=True, "
                    "DO NOT call GitHubAgent.publish — fix the high/critical findings first."
                ),
            ),
            Tool(
                name="finance.risk_metrics",
                kind="python",
                location="finance/risk_metrics.py",
                purpose=(
                    "brier_score, max_drawdown, sharpe_ratio, value_at_risk. Compute over "
                    "memory.trade_log between trading sessions and surface to the Product Agent "
                    "as gap tickets when thresholds breach."
                ),
            ),
            Tool(
                name="finance.pnl",
                kind="python",
                location="finance/pnl.py",
                purpose="compute_pnl + equity_curve_from_trades — input for risk_metrics.",
            ),
        ],
    ),
}


def toolset_for(agent_id: str) -> AgentToolset:
    if agent_id not in REGISTRY:
        raise KeyError(f"unknown agent_id {agent_id!r}; known: {list(REGISTRY)}")
    return REGISTRY[agent_id]
