# Lazy Polymarket Trader — Project Rules

Three managed agents (Product, Architect, Forward Deployment) cooperate via an orchestrator to operate an autonomous Polymarket trading bot. These rules are binding for every agent and every session.

## 1. Agent ownership (no merge conflicts)

| Agent | Owns (edit) | Reads (no edit) |
|---|---|---|
| Product Agent | `product/` | `monitoring/`, `verification/criteria.py` |
| Software Architect | `trading/`, `cache/` | `product/`, `monitoring/` |
| Forward Deployment | `verification/`, `monitoring/`, `tests/` | everything |

Cross-directory edits require an explicit handoff routed through `agents/orchestrator.py`. If an agent thinks it needs to edit outside its lane, it must emit a request to the orchestrator and stop.

## 2. Production Managed Cache is always on

Every call to `anthropic.Anthropic().messages.create(...)` MUST be routed through `cache.prompt_cache.cached_create(...)`. System prompts and large static context blocks are wrapped with `cache_control: {"type": "ephemeral"}`. Direct SDK calls that bypass the helper are a bug.

## 3. Verification gate before any phase is "done"

Before marking a phase complete, the Forward Deployment Agent must:

1. Run `pytest -q` — all tests green.
2. Run `verification.outcome_grader.OutcomeGrader.evaluate(...)` against the acceptance criteria defined in `verification/criteria.py`.
3. Report `passed=True` for every proposed trade in the phase.

A failing grader or red test blocks the phase. No exceptions.

## 4. Paper-trading is the default

`PAPER_TRADING=true` in `.env`. Live trading requires:

- Explicit user confirmation in-session.
- A funded `POLYMARKET_FUNDER_ADDRESS`.
- Risk caps configured in `verification/criteria.py` (max position size, max daily loss).

`trading/execution.py` MUST refuse to call live order endpoints unless all three are satisfied.

## 5. Secrets

`.env` is gitignored. Only `.env.example` is tracked. Never commit a private key, never echo one to stdout, never log one. If you see a key in a diff, stop and warn the user.

## 6. Skill usage

The custom System Architect Skill at `.claude/skills/system-architect/SKILL.md` should be consulted for architecture decisions (new integration, error-handling pattern, retry strategy). It is authored for this codebase specifically.

## 7. Orchestration Manager briefs every specialist

The `agents/orchestration_manager.py` `OrchestrationManager` sits above the
three specialists. Every specialist invocation routes through
`OrchestrationManager.wrap_system_prompt(agent_id, base_prompt)`, which prepends:

- The list of skills + tools the specialist may use (from `agents/tool_registry.py`).
- Recent lessons recorded against that `agent_id` (or `*` for everyone) so the
  agent does not repeat past mistakes.

Rules for the manager:

- It does NOT edit code in any specialist's directory.
- When a capability gap is identified, it opens a **headed** browser-use session
  (per the System Architect Skill) to search GitHub. Candidates are recorded
  with `status='pending'` in `discovered_tools` and require user approval via
  `OrchestrationManager.approve_tool(id)` before they are promoted into
  `agents/tool_registry.py`.
- Specialists that find themselves about to repeat a rejected approach must
  call `memory.record_lesson(agent_id, lesson)` (or
  `OrchestrationManager.record_lesson`) before continuing.

## 8. Web scraping is trust-gated

The `yfe404/web-scraper` skill at `.claude/skills/web-scraper/` is the only
sanctioned scraping playbook. Specialists do NOT scrape directly with urllib,
requests, or playwright. Every scrape, "learn from this repo", or "copy a
pattern from that URL" routes through `OrchestrationManager.request_scrape(agent_id, target)`.

The gate enforces two layers:

1. **Trust policy** (`web_scraper/trust_policy.py`): the GitHub owner or
   domain must be on the allowlist. Seed list: `anthropics`, `browser-use`,
   `Polymarket`, `yfe404`, plus the official Polymarket / Anthropic docs
   domains. Extending the list requires `OrchestrationManager.add_trusted_owner`
   or `add_trusted_domain` — these are user-approved actions.
2. **Authenticator** (`web_scraper/authenticator.py`): hits the public GitHub
   REST API and confirms the repo is public, not archived/disabled, owner
   login matches, a license is declared, and (best-effort) the latest commit
   on the default branch is GPG-verified per GitHub. For HTTPS URLs we
   require HTTPS, a 200 response, and host match after redirects.

Every request — approved AND rejected — is logged to `scrape_audit` in the
memory store. Rejections also become a lesson on the requesting agent so the
same untrusted target won't be requested twice in a future session.

## 9. Publishing to GitHub is gated

Source code is published to a single PRIVATE GitHub repo via
`github_publisher.GitHubAgent`. The agent is invoked by the Forward Deployment
specialist after every successful test run and enforces:

1. `git` and `gh` installed, `gh` authenticated.
2. Target repo must be PRIVATE (checked via `gh repo view` for existing
   repos; new repos are created with `--private`).
3. Test suite green at the time of the call.
4. Secret-scan clean: no `.env`, no `*private_key*`, no `*.pem`, no `id_rsa*`,
   no `credentials.json`, no `.p12`/`.pfx` in the staged set.
5. First publish to a new repo requires explicit `approved=True`. Subsequent
   pushes after green tests may be automatic. Approval is persisted in memory
   under `agent_id="github_publisher"`.

The GitHubAgent is deterministic (no LLM in the publish path) so it cannot
hallucinate a force-push or bypass any of the gates above. The Forward
Deployment specialist may NOT bypass it — if the agent refuses, file a lesson
and stop.

## 10. Vulnerability scan gates every publish

Before any push, `GitHubAgent.should_publish` runs
`vulnerability_detector.VulnerabilityDetectionAgent.run()` over the entire
working tree. Categories live in `vulnerability_detector/categories.py`
(POLY-001..POLY-011) and are adapted from the cookbook 06 agent for this
codebase's actual threat surface: private-key leakage, live-trading gate
bypass, trust-boundary bypass, scrape-gate bypass, headless wallet flows,
command injection, SSRF, SQL injection, unsafe deserialization, hardcoded
secrets, and disabled HTTPS verification.

Rules:

- Any finding at severity `high` or `critical` blocks the publish. Fix the
  finding (do not lower the severity) before retrying.
- The scan is deterministic — pure AST + regex. It runs without an
  ANTHROPIC_API_KEY. If a key is set, an additional LLM Find pass runs with
  tools restricted to Read/Grep/Glob (no Bash, no edit) per the cookbook
  safety stance.
- The scanner does NOT modify source. Specialists do the fixes; the scanner
  only reports.
- Last scan report is persisted to memory under
  `agent_id='vulnerability_detector'`, key `last_report`.
- Tests may pass `skip_vuln_scan=True` to `should_publish` when they are
  exercising the publisher's other gates and don't need the full scan.

## 11. Memory

Cross-session state lives in SQLite at `memory/state.db` (path overridable via `MEMORY_DB_PATH`). Use `memory.store.MemoryStore` — do not write ad-hoc files. Each agent's records are scoped by `agent_id` in the schema.
