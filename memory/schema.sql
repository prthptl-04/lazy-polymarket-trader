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
