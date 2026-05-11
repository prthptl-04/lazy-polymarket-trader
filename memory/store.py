import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any


SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class MemoryStore:
    """SQLite-backed cross-session memory, scoped by agent_id."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or os.environ.get("MEMORY_DB_PATH", "./memory/state.db")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(SCHEMA_PATH.read_text())
        self._conn.commit()

    def put(self, agent_id: str, key: str, value: Any) -> None:
        payload = json.dumps(value)
        self._conn.execute(
            "INSERT OR REPLACE INTO agent_state (agent_id, key, value, updated) VALUES (?, ?, ?, ?)",
            (agent_id, key, payload, time.time()),
        )
        self._conn.commit()

    def get(self, agent_id: str, key: str, default: Any = None) -> Any:
        row = self._conn.execute(
            "SELECT value FROM agent_state WHERE agent_id = ? AND key = ?",
            (agent_id, key),
        ).fetchone()
        return json.loads(row[0]) if row else default

    def log_trade(
        self,
        agent_id: str,
        market_id: str,
        side: str,
        size: float,
        price: float,
        paper: bool,
        grade_pass: bool,
        grade_reason: str | None,
    ) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO trade_log (agent_id, market_id, side, size, price, paper, grade_pass, grade_reason, created)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (agent_id, market_id, side, size, price, int(paper), int(grade_pass), grade_reason, time.time()),
        )
        self._conn.commit()
        return cur.lastrowid

    def recent_trades(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, agent_id, market_id, side, size, price, paper, grade_pass, grade_reason, created "
            "FROM trade_log ORDER BY created DESC LIMIT ?",
            (limit,),
        ).fetchall()
        cols = ["id", "agent_id", "market_id", "side", "size", "price", "paper", "grade_pass", "grade_reason", "created"]
        return [dict(zip(cols, r)) for r in rows]

    # ----- Lessons (consulted by OrchestrationManager before every specialist run) -----

    def record_lesson(self, agent_id: str, lesson: str, context: dict | None = None) -> int:
        cur = self._conn.execute(
            "INSERT INTO agent_lessons (agent_id, lesson, context, created) VALUES (?, ?, ?, ?)",
            (agent_id, lesson, json.dumps(context) if context else None, time.time()),
        )
        self._conn.commit()
        return cur.lastrowid

    def recent_lessons(self, agent_id: str, limit: int = 10) -> list[dict]:
        """Return lessons targeted at this agent OR at '*' (everyone), newest first."""
        rows = self._conn.execute(
            "SELECT id, agent_id, lesson, context, created FROM agent_lessons "
            "WHERE agent_id = ? OR agent_id = '*' "
            "ORDER BY created DESC LIMIT ?",
            (agent_id, limit),
        ).fetchall()
        cols = ["id", "agent_id", "lesson", "context", "created"]
        out = []
        for r in rows:
            row = dict(zip(cols, r))
            if row["context"]:
                row["context"] = json.loads(row["context"])
            out.append(row)
        return out

    # ----- Discovered tools (headed-browser GitHub search results, pending approval) -----

    def record_discovered_tool(
        self, query: str, name: str, url: str, stars: int | None = None, note: str | None = None
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO discovered_tools (query, name, url, stars, note, status, created) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (query, name, url, stars, note, time.time()),
        )
        self._conn.commit()
        return cur.lastrowid

    def pending_discovered_tools(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, query, name, url, stars, note, status, created FROM discovered_tools "
            "WHERE status = 'pending' ORDER BY created DESC LIMIT ?",
            (limit,),
        ).fetchall()
        cols = ["id", "query", "name", "url", "stars", "note", "status", "created"]
        return [dict(zip(cols, r)) for r in rows]

    # ----- Scrape audit (every web-scraper request the OrchestrationManager gates) -----

    def record_scrape_audit(
        self,
        agent_id: str,
        target_raw: str,
        target_kind: str,
        policy_allowed: bool,
        policy_reason: str,
        auth_verified: bool | None,
        auth_reason: str | None,
        auth_details: dict | None,
    ) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO scrape_audit
                (agent_id, target_raw, target_kind, policy_allowed, policy_reason,
                 auth_verified, auth_reason, auth_details, created)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                agent_id,
                target_raw,
                target_kind,
                int(policy_allowed),
                policy_reason,
                None if auth_verified is None else int(auth_verified),
                auth_reason,
                json.dumps(auth_details) if auth_details else None,
                time.time(),
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    def recent_scrape_audits(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, agent_id, target_raw, target_kind, policy_allowed, policy_reason, "
            "auth_verified, auth_reason, auth_details, created "
            "FROM scrape_audit ORDER BY created DESC LIMIT ?",
            (limit,),
        ).fetchall()
        cols = [
            "id", "agent_id", "target_raw", "target_kind", "policy_allowed", "policy_reason",
            "auth_verified", "auth_reason", "auth_details", "created",
        ]
        out = []
        for r in rows:
            row = dict(zip(cols, r))
            if row["auth_details"]:
                row["auth_details"] = json.loads(row["auth_details"])
            out.append(row)
        return out

    # ----- Chief-of-Staff audit log -----

    def record_audit_event(
        self, actor: str, action: str, target: str | None, details: dict | None = None
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO audit_log (actor, action, target, details, created) VALUES (?, ?, ?, ?, ?)",
            (actor, action, target, json.dumps(details) if details else None, time.time()),
        )
        self._conn.commit()
        return cur.lastrowid

    def recent_audit_events(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, actor, action, target, details, created FROM audit_log "
            "ORDER BY created DESC LIMIT ?",
            (limit,),
        ).fetchall()
        cols = ["id", "actor", "action", "target", "details", "created"]
        out = []
        for r in rows:
            row = dict(zip(cols, r))
            if row["details"]:
                row["details"] = json.loads(row["details"])
            out.append(row)
        return out

    # ----- Chief-of-Staff strategic plans -----

    def upsert_plan(self, plan_id: str, title: str, body: str) -> int:
        now = time.time()
        existing = self._conn.execute(
            "SELECT id, created FROM strategic_plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()
        if existing:
            self._conn.execute(
                "UPDATE strategic_plans SET title = ?, body = ?, updated = ? WHERE plan_id = ?",
                (title, body, now, plan_id),
            )
            self._conn.commit()
            return existing[0]
        cur = self._conn.execute(
            "INSERT INTO strategic_plans (plan_id, title, body, created, updated) VALUES (?, ?, ?, ?, ?)",
            (plan_id, title, body, now, now),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_plan(self, plan_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT plan_id, title, body, created, updated FROM strategic_plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if not row:
            return None
        cols = ["plan_id", "title", "body", "created", "updated"]
        return dict(zip(cols, row))

    def list_plans(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT plan_id, title, body, created, updated FROM strategic_plans ORDER BY updated DESC"
        ).fetchall()
        cols = ["plan_id", "title", "body", "created", "updated"]
        return [dict(zip(cols, r)) for r in rows]

    def set_discovered_tool_status(self, tool_id: int, status: str) -> None:
        if status not in ("pending", "approved", "rejected"):
            raise ValueError(f"invalid status {status!r}")
        self._conn.execute("UPDATE discovered_tools SET status = ? WHERE id = ?", (status, tool_id))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
