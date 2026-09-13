from pathlib import Path

from memory.store import MemoryStore


def test_put_and_get_roundtrip(tmp_path: Path):
    store = MemoryStore(db_path=str(tmp_path / "test.db"))
    store.put("architect", "last_phase", {"phase": 0, "status": "ok"})
    got = store.get("architect", "last_phase")
    assert got == {"phase": 0, "status": "ok"}


def test_get_returns_default_for_missing(tmp_path: Path):
    store = MemoryStore(db_path=str(tmp_path / "test.db"))
    assert store.get("product", "missing", default="fallback") == "fallback"


def test_trade_log_records_grade(tmp_path: Path):
    store = MemoryStore(db_path=str(tmp_path / "test.db"))
    log_id = store.log_trade(
        agent_id="architect",
        market_id="m1",
        side="YES",
        size=10.0,
        price=0.5,
        paper=True,
        grade_pass=True,
        grade_reason="verified outcome",
    )
    assert log_id is not None
    recent = store.recent_trades(limit=5)
    assert len(recent) == 1
    assert recent[0]["grade_pass"] == 1
    assert recent[0]["paper"] == 1


# ---------- additive migration ----------

def test_migration_adds_columns_to_an_existing_database(tmp_path):
    """CREATE TABLE IF NOT EXISTS is a NO-OP on a database that already has the
    table. A column added to schema.sql alone never appears on a live file, and
    the first write fails with "no such column" inside a caller that catches
    Exception — losing the row silently. The migrator is what closes that."""
    import sqlite3
    from memory.store import MemoryStore

    db = tmp_path / "old.db"
    # A database created from the PRE-migration shape of the table.
    conn = sqlite3.connect(db)
    conn.executescript(
        """CREATE TABLE closed_trades (
               id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
               asset_class TEXT, thesis_id TEXT, venue TEXT, mode TEXT, reason TEXT,
               entry_price REAL NOT NULL, exit_price REAL NOT NULL, quantity REAL NOT NULL,
               stop REAL, target REAL, atr REAL, realized_return REAL NOT NULL,
               realized_usd REAL NOT NULL, held_seconds REAL, opened_at REAL,
               closed_at REAL NOT NULL);
           CREATE TABLE trade_log (
               id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT NOT NULL,
               market_id TEXT NOT NULL, side TEXT NOT NULL, size REAL NOT NULL,
               price REAL NOT NULL, paper INTEGER NOT NULL, grade_pass INTEGER NOT NULL,
               grade_reason TEXT, created REAL NOT NULL);"""
    )
    conn.commit()
    conn.close()

    store = MemoryStore(db_path=str(db))
    closed_cols = {r[1] for r in store._conn.execute("PRAGMA table_info(closed_trades)")}
    assert {"planned_entry", "planned_exit", "entry_fill_source",
            "exit_fill_source", "adv_usd", "spread_bps_at_entry"} <= closed_cols
    log_cols = {r[1] for r in store._conn.execute("PRAGMA table_info(trade_log)")}
    assert {"filled", "session", "venue"} <= log_cols

    # ...and a write using the new columns round-trips, which is the thing that
    # was silently failing before.
    store.record_closed_trade({
        "symbol": "AAPL", "asset_class": "equity", "entry_price": 100.0,
        "exit_price": 110.0, "quantity": 1.0, "realized_return": 0.1,
        "realized_usd": 10.0, "closed_at": 1.0, "planned_entry": 99.9,
        "entry_fill_source": "venue", "spread_bps_at_entry": 4,
    })
    row = store.closed_trades()[0]
    assert row["planned_entry"] == 99.9 and row["entry_fill_source"] == "venue"

    store.log_trade("fund", "AAPL", "buy", 10.0, 100.0, True, True, "ok",
                    filled=True, session="regular", venue="paper")
    assert store.recent_trades(limit=1)[0]["filled"] == 1


def test_migration_is_idempotent(tmp_path):
    """It runs on every boot; running twice must not raise."""
    from memory.store import MemoryStore
    db = str(tmp_path / "twice.db")
    MemoryStore(db_path=db)
    store = MemoryStore(db_path=db)
    store._migrate()
    assert store.closed_trades() == []
