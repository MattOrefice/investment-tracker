"""Database schema and connection helpers."""
import sqlite3
from pathlib import Path

from src.config import get_db_path

DB_PATH = get_db_path()

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    account_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    type          TEXT NOT NULL,
    custodian     TEXT,
    is_active     INTEGER DEFAULT 1,
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS asset_classes (
    asset_class_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    parent_id        INTEGER,
    target_weight    REAL NOT NULL,
    tolerance_band   REAL NOT NULL DEFAULT 0.05,
    sort_order       INTEGER,
    rationale        TEXT,
    benchmark_ticker TEXT,
    FOREIGN KEY (parent_id) REFERENCES asset_classes(asset_class_id)
);

CREATE TABLE IF NOT EXISTS securities (
    ticker            TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    asset_class_id    INTEGER NOT NULL,
    security_type     TEXT,
    expense_ratio     REAL,
    notes             TEXT,
    holding_rationale TEXT,
    FOREIGN KEY (asset_class_id) REFERENCES asset_classes(asset_class_id)
);

CREATE TABLE IF NOT EXISTS themes (
    theme_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS theses (
    thesis_id                INTEGER PRIMARY KEY AUTOINCREMENT,
    title                    TEXT NOT NULL,
    macro_view               TEXT NOT NULL,
    view_summary             TEXT,
    conviction               INTEGER NOT NULL,
    level                    TEXT NOT NULL DEFAULT 'investment',
    parent_thesis_id         INTEGER,
    target_sleeves           TEXT,
    horizon_months           INTEGER,
    exit_conditions          TEXT,
    invalidation_conditions  TEXT,
    expected_return_scenario TEXT,
    vehicle_rationale        TEXT,
    target_weight            REAL,
    status                   TEXT NOT NULL DEFAULT 'active',
    created_at               TEXT DEFAULT CURRENT_TIMESTAMP,
    closed_at                TEXT,
    post_mortem              TEXT,
    outcome                  TEXT,
    realized_pnl_pct         REAL,
    what_i_got_right         TEXT,
    what_i_got_wrong         TEXT,
    would_repeat             TEXT,
    FOREIGN KEY (parent_thesis_id) REFERENCES theses(thesis_id)
);

CREATE TABLE IF NOT EXISTS thesis_themes (
    thesis_id INTEGER NOT NULL,
    theme_id  INTEGER NOT NULL,
    PRIMARY KEY (thesis_id, theme_id),
    FOREIGN KEY (thesis_id) REFERENCES theses(thesis_id),
    FOREIGN KEY (theme_id)  REFERENCES themes(theme_id)
);

CREATE TABLE IF NOT EXISTS trades (
    trade_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id    INTEGER NOT NULL,
    ticker        TEXT NOT NULL,
    thesis_id     INTEGER,
    trade_date    TEXT NOT NULL,
    action        TEXT NOT NULL,
    shares        REAL NOT NULL,
    price         REAL NOT NULL,
    fees          REAL DEFAULT 0,
    notes         TEXT,
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (account_id) REFERENCES accounts(account_id),
    FOREIGN KEY (ticker)     REFERENCES securities(ticker),
    FOREIGN KEY (thesis_id)  REFERENCES theses(thesis_id)
);

CREATE TABLE IF NOT EXISTS prices (
    ticker        TEXT NOT NULL,
    price_date    TEXT NOT NULL,
    close         REAL NOT NULL,
    adj_close     REAL,
    PRIMARY KEY (ticker, price_date)
);

CREATE TABLE IF NOT EXISTS dividends (
    ticker   TEXT NOT NULL,
    ex_date  TEXT NOT NULL,
    amount   REAL NOT NULL,
    PRIMARY KEY (ticker, ex_date)
);

CREATE INDEX IF NOT EXISTS idx_trades_account_date  ON trades(account_id, trade_date);
CREATE INDEX IF NOT EXISTS idx_prices_ticker_date   ON prices(ticker, price_date);
CREATE INDEX IF NOT EXISTS idx_dividends_ticker_date ON dividends(ticker, ex_date);
"""

_migrated_paths: set[str] = set()


def _auto_migrate(conn: sqlite3.Connection) -> None:
    """Idempotent schema migrations. Safe to call on every process startup."""
    # Migration: drop FK on prices.ticker so benchmark-only tickers (e.g. AGG)
    # can be cached without needing a securities table entry.
    info = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='prices'"
    ).fetchone()
    if info and "FOREIGN KEY (ticker) REFERENCES securities" in (info[0] or ""):
        conn.executescript(
            "PRAGMA foreign_keys = OFF;"
            "CREATE TABLE IF NOT EXISTS prices_new ("
            "    ticker TEXT NOT NULL, price_date TEXT NOT NULL,"
            "    close REAL NOT NULL, adj_close REAL,"
            "    PRIMARY KEY (ticker, price_date)"
            ");"
            "INSERT OR IGNORE INTO prices_new"
            "    SELECT ticker, price_date, close, adj_close FROM prices;"
            "DROP TABLE prices;"
            "ALTER TABLE prices_new RENAME TO prices;"
            "CREATE INDEX IF NOT EXISTS idx_prices_ticker_date"
            "    ON prices(ticker, price_date);"
            "PRAGMA foreign_keys = ON;"
        )


def get_connection():
    """Return a SQLite connection with foreign keys enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    db_key = str(DB_PATH)
    if db_key not in _migrated_paths:
        _auto_migrate(conn)
        _migrated_paths.add(db_key)
    return conn

def initialize_db():
    """Create tables if they don't exist, then seed the personal account."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        existing = conn.execute(
            "SELECT account_id FROM accounts WHERE name = ?",
            ("Personal Fidelity",),
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO accounts (name, type, custodian) VALUES (?, ?, ?)",
                ("Personal Fidelity", "taxable", "Fidelity"),
            )

if __name__ == "__main__":
    initialize_db()
    print(f"DB initialized at {DB_PATH}")
