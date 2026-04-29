"""Database schema and connection helpers."""
import sqlite3
from pathlib import Path

try:
    from src.config import get_db_path
except ImportError:
    from config import get_db_path  # when run directly as python src/db.py

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
    name             TEXT NOT NULL UNIQUE,
    parent_id        INTEGER,
    target_weight    REAL NOT NULL,
    tolerance_band   REAL NOT NULL DEFAULT 0.05,
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
    FOREIGN KEY (asset_class_id) REFERENCES asset_classes(asset_class_id)
);

CREATE TABLE IF NOT EXISTS theses (
    thesis_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    title            TEXT NOT NULL,
    macro_view       TEXT NOT NULL,
    conviction       INTEGER NOT NULL,
    horizon_months   INTEGER,
    exit_conditions  TEXT,
    status           TEXT NOT NULL DEFAULT 'active',
    created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
    closed_at        TEXT,
    post_mortem      TEXT
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
    PRIMARY KEY (ticker, price_date),
    FOREIGN KEY (ticker) REFERENCES securities(ticker)
);

CREATE INDEX IF NOT EXISTS idx_trades_account_date ON trades(account_id, trade_date);
CREATE INDEX IF NOT EXISTS idx_prices_ticker_date  ON prices(ticker, price_date);
"""

def get_connection():
    """Return a SQLite connection with foreign keys enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
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
