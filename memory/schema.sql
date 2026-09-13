CREATE TABLE IF NOT EXISTS agent_state (
    agent_id TEXT NOT NULL,
    key      TEXT NOT NULL,
    value    TEXT NOT NULL,
    updated  REAL NOT NULL,
    PRIMARY KEY (agent_id, key)
);

CREATE TABLE IF NOT EXISTS trade_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL,
    market_id   TEXT NOT NULL,
    side        TEXT NOT NULL,
    size        REAL NOT NULL,
    price       REAL NOT NULL,
    paper       INTEGER NOT NULL,
    grade_pass  INTEGER NOT NULL,
    grade_reason TEXT,
    created     REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trade_log_market ON trade_log (market_id);
CREATE INDEX IF NOT EXISTS idx_trade_log_created ON trade_log (created);

-- Lessons learned: surfaced to every specialist by the OrchestrationManager
-- BEFORE it runs, so agents don't repeat past mistakes. See agents/orchestration_manager.py.
CREATE TABLE IF NOT EXISTS agent_lessons (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id  TEXT NOT NULL,             -- "product" | "architect" | "forward_deployment" | "*"
    lesson    TEXT NOT NULL,             -- short imperative line: "Don't X because Y"
    context   TEXT,                      -- optional JSON: triggering event / source ticket
    created   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lessons_agent ON agent_lessons (agent_id);
CREATE INDEX IF NOT EXISTS idx_lessons_created ON agent_lessons (created);

-- Tool discovery results: when the manager opens a headed browser to look for
-- a trusted GitHub repo, candidates land here pending user approval before
-- they're promoted into tool_registry.
-- Audit trail for every scrape request gated by OrchestrationManager.request_scrape.
-- Records BOTH allowed and rejected requests so we can answer "what did we ever
-- look at, and what did the auth check say about it?"
CREATE TABLE IF NOT EXISTS scrape_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id        TEXT NOT NULL,
    target_raw      TEXT NOT NULL,
    target_kind     TEXT NOT NULL,        -- github_repo | https_url | unknown
    policy_allowed  INTEGER NOT NULL,
    policy_reason   TEXT NOT NULL,
    auth_verified   INTEGER,              -- NULL when policy blocked before auth ran
    auth_reason     TEXT,
    auth_details    TEXT,                 -- JSON
    created         REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scrape_audit_agent ON scrape_audit (agent_id);
CREATE INDEX IF NOT EXISTS idx_scrape_audit_created ON scrape_audit (created);

-- Chief-of-Staff audit log: every consequential action (plan persisted,
-- specialist invoked, gate decision recorded) lands here so we have a single
-- chronological view of what the system did and why.
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    actor     TEXT NOT NULL,        -- "manager" | agent_id | "user"
    action    TEXT NOT NULL,        -- short verb: "plan_persisted", "scrape_request", "executive_summary"
    target    TEXT,                 -- subject of the action (plan id, target string, specialist id)
    details   TEXT,                 -- optional JSON
    created   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log (created);
CREATE INDEX IF NOT EXISTS idx_audit_log_actor ON audit_log (actor);

-- Chief-of-Staff plans: short-form strategic notes the manager keeps.
-- Unlike memory lessons (rules of thumb), plans are concrete "we will do X by Y".
CREATE TABLE IF NOT EXISTS strategic_plans (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id   TEXT UNIQUE NOT NULL,
    title     TEXT NOT NULL,
    body      TEXT NOT NULL,
    created   REAL NOT NULL,
    updated   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS discovered_tools (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    query      TEXT NOT NULL,
    name       TEXT NOT NULL,
    url        TEXT NOT NULL,
    stars      INTEGER,
    note       TEXT,
    status     TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | rejected
    created    REAL NOT NULL
);

-- Round-table deliberations. `status='in_progress'` is what makes the kill
-- switch resumable: a STOP mid-debate leaves the row, and GO picks it up
-- rather than restarting the thesis from nothing.
CREATE TABLE IF NOT EXISTS deliberations (
    thesis_id   TEXT PRIMARY KEY,
    symbol      TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    status      TEXT NOT NULL,        -- in_progress | complete | abandoned
    signal      TEXT,                 -- bullish | bearish | neutral
    confidence  REAL,
    payload     TEXT NOT NULL,        -- JSON: opinions, consensus, transcript
    created     REAL NOT NULL,
    updated     REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_delib_status ON deliberations (status);
CREATE INDEX IF NOT EXISTS idx_delib_symbol ON deliberations (symbol);
CREATE INDEX IF NOT EXISTS idx_delib_created ON deliberations (created);

-- What actually happened to a thesis. Without this the round table can never
-- be scored, and the confidence-to-probability shrink in trading/pipeline.py
-- stays a guess forever.
CREATE TABLE IF NOT EXISTS thesis_outcomes (
    thesis_id       TEXT PRIMARY KEY,
    symbol          TEXT NOT NULL,
    signal          TEXT,             -- what the committee concluded
    confidence      REAL,             -- how sure it said it was
    realized_return REAL,             -- what the position actually did
    correct         INTEGER,          -- direction matched (1/0)
    resolved_at     REAL NOT NULL,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_outcomes_symbol ON thesis_outcomes (symbol);
CREATE INDEX IF NOT EXISTS idx_outcomes_resolved ON thesis_outcomes (resolved_at);

-- Model spend, tagged with the trading mode that caused it. Without the tag the
-- fund cannot answer the only question that matters about its own token bill:
-- is the paper engine burning credits it has not yet earned the right to spend?
CREATE TABLE IF NOT EXISTS llm_costs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    provider      TEXT NOT NULL,        -- anthropic | gemini
    model         TEXT,
    mode          TEXT NOT NULL,        -- paper | live
    thesis_id     TEXT,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read    INTEGER NOT NULL DEFAULT 0,
    cache_write   INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0,
    created       REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_costs_mode ON llm_costs (mode);
CREATE INDEX IF NOT EXISTS idx_llm_costs_created ON llm_costs (created);
