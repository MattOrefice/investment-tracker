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
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    tax_treatment TEXT DEFAULT 'other',
    pseudonym     TEXT,
    display_name  TEXT,
    managed_by    TEXT DEFAULT 'external',
    included_in_household INTEGER DEFAULT 0
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
    lot_source    TEXT DEFAULT 'initial',
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
CREATE UNIQUE INDEX IF NOT EXISTS ux_accounts_pseudonym ON accounts (pseudonym);
"""

_migrated_paths: set[str] = set()


def _drop_account_number(conn: sqlite3.Connection) -> None:
    """PII migration — remove the raw ``account_number`` column from ``accounts``.

    Account metadata is keyed on ``pseudonym`` instead; ingestion resolves raw
    numbers to pseudonyms before anything reaches the DB. Idempotent: safe to run
    twice, and a no-op once the column is gone. Requires SQLite >= 3.35 for
    ALTER TABLE DROP COLUMN, and the account_number unique index must be dropped
    first (SQLite forbids dropping an indexed column).
    """
    has_accounts = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='accounts'"
    ).fetchone()
    if not has_accounts:
        return

    cols = [row[1] for row in conn.execute("PRAGMA table_info(accounts)")]
    if "account_number" in cols:
        version = tuple(int(p) for p in sqlite3.sqlite_version.split("."))
        if version < (3, 35, 0):
            raise RuntimeError(
                f"SQLite {sqlite3.sqlite_version} is too old for ALTER TABLE DROP "
                "COLUMN (needs >= 3.35). Cannot complete the account_number PII "
                "migration; upgrade SQLite/Python before running."
            )
        conn.execute("DROP INDEX IF EXISTS ux_accounts_account_number")
        conn.execute("ALTER TABLE accounts DROP COLUMN account_number")

    # The seed's ON CONFLICT(pseudonym) upsert depends on this uniqueness; ensure
    # it exists whenever the column is present. Minimal/legacy accounts tables
    # (bare test DBs) have no pseudonym column — skip the index there rather than
    # error, keeping the migration a safe no-op on incomplete schemas.
    if "pseudonym" in cols:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_accounts_pseudonym ON accounts (pseudonym)"
        )
    conn.commit()


def _add_included_in_household(conn: sqlite3.Connection) -> None:
    """Add ``included_in_household`` to ``accounts`` (default 1) if missing.

    Some accounts hold money that is not a household asset — e.g. unvested,
    forfeitable employer contributions — and must be excluded from every
    total, allocation, and liquidity calc at this one source rather than
    re-filtered per page.

    This is the RETROFIT path for a legacy DB that predates the column: every
    row already present is a genuine, pre-flag account, so it back-fills them to
    1 (included). The GO-FORWARD default for new inserts is fail-conservative
    (0), set by the CREATE TABLE schema and, on already-built DBs, flipped from
    the old permissive 1 by tools/migrate_accounts_phase42_flags.py. Keeping this
    back-fill at 1 is deliberate — an old genuine account must not vanish. Idempotent.
    """
    has_accounts = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='accounts'"
    ).fetchone()
    if not has_accounts:
        return
    cols = [row[1] for row in conn.execute("PRAGMA table_info(accounts)")]
    if "included_in_household" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN included_in_household INTEGER DEFAULT 1")
        conn.execute("UPDATE accounts SET included_in_household = 1 WHERE included_in_household IS NULL")
    conn.commit()


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

    # Migration: add lot_source column to trades for per-lot tax tracking.
    trade_cols = [
        row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()
    ]
    if "lot_source" not in trade_cols:
        conn.execute("ALTER TABLE trades ADD COLUMN lot_source TEXT DEFAULT 'initial'")
        conn.execute(
            "UPDATE trades SET lot_source = 'initial' WHERE lot_source IS NULL"
        )
        conn.commit()

    # Migration: surface VNQ/DBC split in Real Assets benchmark label (60/40 policy).
    has_ac = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='asset_classes'"
    ).fetchone()
    if has_ac:
        conn.execute(
            "UPDATE asset_classes SET benchmark_ticker = 'VNQ (60%) + DBC (40%)' "
            "WHERE name = 'Real Assets' AND parent_id IS NOT NULL "
            "AND benchmark_ticker IN ('VNQ+DBC', 'VNQ+DJP', 'VNQ (50%) + DBC (50%)')"
        )
        conn.commit()

    # Migration: drop the raw account_number PII column (keyed on pseudonym now).
    _drop_account_number(conn)

    # Migration: add included_in_household (see function docstring).
    _add_included_in_household(conn)


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
    """Create the schema, run migrations, then seed the personal account.

    Schema creation must precede migrations. A fresh or 0-byte DB (a clean clone's
    first personal-mode launch) has no tables for ``_auto_migrate`` to ALTER, so
    running migrations first raised ``OperationalError: no such table: trades``.
    SCHEMA is ``CREATE ... IF NOT EXISTS`` throughout, so applying it up front is a
    no-op on an existing DB; the ``get_connection()`` below then runs
    ``_auto_migrate`` against a DB that already has the tables — a no-op on a fresh
    DB, the usual migration on an existing one.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    schema_conn = sqlite3.connect(DB_PATH)
    try:
        schema_conn.executescript(SCHEMA)
        schema_conn.commit()
    finally:
        schema_conn.close()
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT account_id FROM accounts WHERE name = ?",
            ("Personal Fidelity",),
        ).fetchone()
        if existing is None:
            # The base self-directed taxable account: it carries the trade ledger,
            # is the row get_portfolio_account() resolves via
            # tax_treatment='taxable' AND managed_by='self', and is a genuine
            # household asset. Under the fail-conservative schema defaults
            # (managed_by 'external', included_in_household 0) all three flags MUST
            # be set explicitly here — otherwise this account would be excluded
            # from the household AND the portfolio resolver would raise (no
            # self-directed taxable account). Do not fall back to the defaults.
            conn.execute(
                "INSERT INTO accounts (name, type, custodian, tax_treatment,"
                " managed_by, included_in_household) VALUES (?, ?, ?, ?, ?, ?)",
                ("Personal Fidelity", "taxable", "Fidelity", "taxable", "self", 1),
            )

if __name__ == "__main__":
    initialize_db()
    print(f"DB initialized at {DB_PATH}")
