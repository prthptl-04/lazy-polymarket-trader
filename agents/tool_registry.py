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
    Tool(
        name="research-agent",
        kind="skill",
        location=".claude/skills/research-agent/SKILL.md",
        purpose=(
            "Cookbook-00 research pattern, trust-gated. Every candidate URL routes through "
            "OrchestrationManager.request_scrape before becoming a citation. See research_agent.ResearchAgent."
        ),
    ),
    Tool(
        name="observability",
        kind="skill",
        location=".claude/skills/observability/SKILL.md",
        purpose=(
            "Read-only health monitoring: gh repo state + pytest summary + LiveFeedback ring. "
            "See observability.ObservabilityAgent. Owned by Forward Deployment."
        ),
    ),
    Tool(
        name="tool-evaluation",
        kind="skill",
        location=".claude/skills/tool-evaluation/SKILL.md",
        purpose=(
            "Exact-match regression harness for deterministic tools registered here. "
            "See tool_evaluation.ToolEvaluator and tool_evaluation.cases.default_evaluator."
        ),
    ),
    Tool(
        name="extended-thinking",
        kind="skill",
        location=".claude/skills/extended-thinking/SKILL.md",
        purpose=(
            "Auto-enable Claude thinking budget on Sonnet models via cache.prompt_cache.cached_create. "
            "Pass thinking_budget_tokens=N to override; default is 2000 on Sonnet, 0 on Opus."
        ),
    ),
    Tool(
        name="code-graph",
        kind="skill",
        location=".claude/skills/code-graph/SKILL.md",
        purpose=(
            "AST-derived codebase knowledge graph (MIT, ours — GitNexus replacement avoiding "
            "PolyForm-Noncommercial license issues). Use during architecture reviews and to "
            "power the dashboard's force-directed view."
        ),
    ),
    Tool(
        name="dashboard",
        kind="python",
        location="dashboard/server.py",
        purpose=(
            "Read-only FastAPI dashboard + autonomous-loop GO/STOP buttons. "
            "Single-page UI at 127.0.0.1:8765 by default. Run with `python -m dashboard`."
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
        skills=list(COMMON_SKILLS) + [
            Tool(
                name="bmad-architect",
                kind="skill",
                location=".claude/skills/bmad-architect/SKILL.md",
                purpose=(
                    "Winston persona (adapted from bmad-code-org/BMAD-METHOD, MIT). Use for architecture "
                    "decisions and trade-off matrices. Pairs with system-architect skill."
                ),
            ),
            Tool(
                name="bmad-developer",
                kind="skill",
                location=".claude/skills/bmad-developer/SKILL.md",
                purpose=(
                    "Amelia persona (adapted from BMAD, MIT). Test-first story execution. "
                    "Use when implementing an approved roadmap item end-to-end."
                ),
            ),
        ],
        tools=[
            *COMMON_TOOLS,
            Tool(
                name="trading.venues.polymarket_us.PolymarketUSVenue",
                kind="python",
                location="trading/venues/polymarket_us.py",
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
            Tool(
                name="research_agent.PlaywrightFetcher",
                kind="python",
                location="research_agent/playwright_fetcher.py",
                purpose=(
                    "Trust-gated Scrapling (BSD-3) wrapper. fetch_static for plain HTML/JSON, "
                    "fetch_dynamic for Cloudflare-protected pages. Every URL passes "
                    "OrchestrationManager.request_scrape FIRST."
                ),
            ),
            Tool(
                name="code_graph.build_graph",
                kind="python",
                location="code_graph/extractor.py",
                purpose=(
                    "AST-derived graph of this codebase. Use when designing module boundaries "
                    "or surfacing coupling for the architecture review."
                ),
            ),
        ],
    ),
    "forward_deployment": AgentToolset(
        agent_id="forward_deployment",
        skills=list(COMMON_SKILLS) + [
            Tool(
                name="bmad-qa-tester",
                kind="skill",
                location=".claude/skills/bmad-qa-tester/SKILL.md",
                purpose=(
                    "Test-skills module (adapted from bmad-code-org/BMAD-METHOD, MIT). "
                    "Adversarial review (Blind Hunter / Edge Case Hunter / Acceptance Auditor) + "
                    "test generation. Use before every publish to verify the test set covers the change."
                ),
            ),
        ],
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
            Tool(
                name="observability.ObservabilityAgent",
                kind="python",
                location="observability/agent.py",
                purpose=(
                    "Composes GitHubHealth + TestHealth + LiveFeedback into a single HealthReport. "
                    "Read-only. Run before publishes; consult during on-call."
                ),
            ),
            Tool(
                name="tool_evaluation.cases.default_evaluator",
                kind="python",
                location="tool_evaluation/cases.py",
                purpose=(
                    "Seed evaluator covering finance.* and observability.* helpers. "
                    "Call .run() to get an EvaluationReport; expand cases when contracts change."
                ),
            ),
        ],
    ),
}


def toolset_for(agent_id: str) -> AgentToolset:
    if agent_id not in REGISTRY:
        raise KeyError(f"unknown agent_id {agent_id!r}; known: {list(REGISTRY)}")
    return REGISTRY[agent_id]
