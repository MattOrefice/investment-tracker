"""Database schema and connection helpers."""
import hashlib
import os
import sqlite3
import tempfile
import threading
from pathlib import Path

from src.config import _ROOT, get_db_path

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
        conn.commit()

    # The seed's ON CONFLICT(pseudonym) upsert depends on this uniqueness; ensure
    # it exists whenever the column is present. Minimal/legacy accounts tables
    # (bare test DBs) have no pseudonym column — skip the index there rather than
    # error, keeping the migration a safe no-op on incomplete schemas.
    #
    # Pre-gated on sqlite_master rather than left to IF NOT EXISTS. The statement
    # is write-free on a book that already has the index only because SQLite
    # elides CREATE ... IF NOT EXISTS for an existing object — an implementation
    # detail, and one no test can observe (it is elided below the authorizer as
    # well as below the write lock, measured 2026-08-12). The gate makes "issues
    # no write when there is nothing to do" a property of this code instead.
    if "pseudonym" in cols and not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' "
        "AND name='ux_accounts_pseudonym'"
    ).fetchone():
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


# Rows the Real-Assets label migration heals. Shared verbatim between the gate
# that decides whether to run it and the UPDATE that does the work — if the two
# were written out separately they could drift, and a gate that stopped matching
# what it guards would silently disable the migration rather than fail.
_REAL_ASSETS_LEGACY_WHERE = (
    "WHERE name = 'Real Assets' AND parent_id IS NOT NULL "
    "AND benchmark_ticker IN ('VNQ+DBC', 'VNQ+DJP', 'VNQ (50%) + DBC (50%)')"
)


def _auto_migrate(conn: sqlite3.Connection) -> None:
    """Idempotent schema migrations. Safe to call on every process startup.

    Every write here sits behind a condition check that is itself a pure read, so
    connecting to a database that needs no migration issues no write statement at
    all. That is what makes ``get_connection()`` safe to call against a read-only
    DB — a guarantee ``tests/test_db_no_write_on_touch.py`` enforces, and one the
    unconditional statements this function used to issue quietly broke: they
    demanded write access at statement start even when they matched zero rows.
    """
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
    #
    # The heal is kept, not retired: of the three legacy labels, 'VNQ+DBC' and
    # 'VNQ+DJP' fail loud downstream (parse_benchmark_spec rejects weights summing
    # to 2.0), but 'VNQ (50%) + DBC (50%)' PARSES — an unhealed book would feed a
    # silent 50/50 Real-Assets benchmark into attribution and the PDF with no
    # error anywhere. Nothing else in the stack catches that, so the migration
    # earns its place; only its unconditional execution had to go.
    has_ac = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='asset_classes'"
    ).fetchone()
    if has_ac and conn.execute(
        f"SELECT COUNT(*) FROM asset_classes {_REAL_ASSETS_LEGACY_WHERE}"
    ).fetchone()[0]:
        conn.execute(
            "UPDATE asset_classes SET benchmark_ticker = 'VNQ (60%) + DBC (40%)' "
            f"{_REAL_ASSETS_LEGACY_WHERE}"
        )
        conn.commit()

    # Migration: drop the raw account_number PII column (keyed on pseudonym now).
    _drop_account_number(conn)

    # Migration: add included_in_household (see function docstring).
    _add_included_in_household(conn)


class _ClosingConnection(sqlite3.Connection):
    """A connection whose ``with`` block commits or rolls back AND THEN CLOSES.

    sqlite3's own context manager only commits or rolls back; it never closes. Every
    ``with get_connection() as conn:`` site (97 of them) therefore held its connection
    open until garbage collection, which a reference cycle can delay indefinitely. On
    Windows an open handle also blocks deleting the file, which is how it surfaced
    (#307). A census of all 97 sites found no use of a connection after its block,
    so closing on exit breaks none of them.

    Only ``__exit__`` changes. A caller that uses the connection without ``with`` and
    calls ``close()`` itself is unaffected.
    """

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)   # commit, or roll back
        finally:
            self.close()


# ── The demo app's runtime cache (#368 item 2) ──────────────────────────────────
# The four tables the demo app writes while it runs: fetched closes and dividends,
# FRED series, and quarter locks. Each maps to (key columns, {column: type}).
#
# With the cache in use (use_runtime_cache), demo.db is opened READ-ONLY and a
# separate cache file is attached. The cache holds a COPY of these four tables,
# seeded from demo.db once per demo.db (keyed on its sha256, so a new deploy
# re-seeds), plus everything the run writes. On each connection the four names are
# TEMP views of the cache's tables, and a write to one lands in the cache through an
# INSTEAD OF trigger (INSERT OR REPLACE, so the newest write wins a shared key).
# Nothing else is redirected: any other write fails on the read-only file rather
# than changing it. Before this, demo mode wrote all four into demo.db itself, and
# 127 FRED rows from past runs were committed that way (#368).
#
# A COPY, NOT A UNION. A view of cache rows over committed rows was measured first:
# per-row dedup and the UNION kept SQLite from using an index for MAX(price_date) and
# for dividend_adjusted's correlated lookups, and six get_prices reads went from
# 0.03 s to 8 s. A view of one table is flattened into the query and keeps its index.
#
# The cache tables carry a `runtime_` prefix because SQLite forbids a qualified
# target (cache.prices) in trigger DML: the unqualified name must resolve to the
# cache's table and nothing else.
_RUNTIME_TABLES = {
    "prices":            (("ticker", "price_date"),
                          {"ticker": "TEXT", "price_date": "TEXT", "close": "REAL", "adj_close": "REAL"}),
    "dividends":         (("ticker", "ex_date"),
                          {"ticker": "TEXT", "ex_date": "TEXT", "amount": "REAL"}),
    "macro_cache":       (("series_id", "fetch_date"),
                          {"series_id": "TEXT", "fetch_date": "TEXT", "data": "TEXT"}),
    "quarter_snapshots": (("quarter_id",),
                          {"quarter_id": "TEXT", "snapshot_date": "TEXT", "captured_at": "TEXT",
                           "snapshot_data": "BLOB"}),
}

# (committed demo.db, cache file) once use_runtime_cache() has run; None otherwise.
_RUNTIME_CACHE: "tuple[Path, Path] | None" = None
_SEEDED: "set[tuple[str, str]]" = set()
_SEED_LOCK = threading.Lock()


def runtime_cache_path() -> Path:
    """Where the demo app keeps what it fetches: $DEMO_RUNTIME_CACHE, else a file in
    the OS temp directory. Never under data/: every data/*.db is tracked or
    treated as tracked (tests/conftest.py), and this file must never be either."""
    env = os.environ.get("DEMO_RUNTIME_CACHE")
    if env:
        return Path(env)
    return Path(tempfile.gettempdir()) / "investment-tracker" / "demo_runtime_cache.db"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _seed_runtime_cache(committed: Path, cache: Path) -> None:
    """Copy the four runtime tables from demo.db into the cache, unless the cache was
    already seeded from this exact demo.db. demo.db is read through a read-only URI."""
    ident = _sha256(committed)
    conn = sqlite3.connect(str(cache), uri=True)   # uri: so ATTACH can open demo.db read-only
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS runtime_meta (key TEXT PRIMARY KEY, value TEXT)")
        row = conn.execute("SELECT value FROM runtime_meta WHERE key = 'seeded_from'").fetchone()
        if row and row[0] == ident:
            return
        conn.execute("ATTACH DATABASE ? AS committed",
                     (f"file:{committed.as_posix()}?mode=ro",))
        present = {r[0] for r in conn.execute(
            "SELECT name FROM committed.sqlite_master WHERE type = 'table'")}
        for name, (key, cols) in _RUNTIME_TABLES.items():
            conn.execute(f"DROP TABLE IF EXISTS runtime_{name}")
            conn.execute(f"CREATE TABLE runtime_{name} ("
                         + ", ".join(f"{c} {t}" for c, t in cols.items())
                         + f", PRIMARY KEY ({', '.join(key)}))")
            if name in present:
                names = ", ".join(cols)
                conn.execute(f"INSERT INTO runtime_{name} ({names}) "
                             f"SELECT {names} FROM committed.{name}")
        conn.execute("INSERT OR REPLACE INTO runtime_meta VALUES ('seeded_from', ?)", (ident,))
        conn.commit()
    finally:
        conn.close()


def use_runtime_cache(cache_path=None, *, committed=None) -> Path:
    """Send the demo app's runtime writes to a separate cache, never to demo.db.

    Called by app.py in demo mode, before anything reads or writes, on every rerun:
    the seed runs once per process and demo.db. Applies only to connections whose
    DB_PATH is ``committed`` (the committed demo.db by default), so a tool that
    repoints DB_PATH at a book it means to write is unaffected. Returns the cache
    path."""
    global _RUNTIME_CACHE
    demo = Path(committed or (_ROOT / "data" / "demo.db")).resolve()
    cache = Path(cache_path or runtime_cache_path())
    with _SEED_LOCK:
        if (str(demo), str(cache)) not in _SEEDED:
            cache.parent.mkdir(parents=True, exist_ok=True)
            _seed_runtime_cache(demo, cache)
            _SEEDED.add((str(demo), str(cache)))
        _RUNTIME_CACHE = (demo, cache)
    return cache


_OVERLAY_SQL = "\n".join(
    f"CREATE TEMP VIEW {name} AS SELECT {', '.join(cols)} FROM cache.runtime_{name};\n"
    f"CREATE TEMP TRIGGER {name}_insert INSTEAD OF INSERT ON {name} BEGIN "
    f"INSERT OR REPLACE INTO runtime_{name} ({', '.join(cols)}) "
    f"VALUES ({', '.join('NEW.' + c for c in cols)}); END;\n"
    f"CREATE TEMP TRIGGER {name}_delete INSTEAD OF DELETE ON {name} BEGIN "
    f"DELETE FROM runtime_{name} WHERE {' AND '.join(f'{k} = OLD.{k}' for k in key)}; END;"
    for name, (key, cols) in _RUNTIME_TABLES.items()
)


def _runtime_overlay_connection(committed: Path, cache: Path):
    conn = sqlite3.connect(f"file:{committed.as_posix()}?mode=ro", uri=True,
                           factory=_ClosingConnection)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    db_key = str(DB_PATH)
    if db_key not in _migrated_paths:
        _auto_migrate(conn)          # write-free on a book that needs nothing (#175)
        _migrated_paths.add(db_key)
    conn.execute("ATTACH DATABASE ? AS cache", (str(cache),))
    conn.executescript(_OVERLAY_SQL)
    return conn


def get_connection():
    """Return a SQLite connection with foreign keys enabled. Used as a context
    manager it commits (or rolls back) and then closes; see _ClosingConnection.

    To the committed demo.db while the runtime cache is in use, the connection is
    the read-only overlay described above _RUNTIME_TABLES."""
    if _RUNTIME_CACHE is not None and Path(DB_PATH).resolve() == _RUNTIME_CACHE[0]:
        return _runtime_overlay_connection(*_RUNTIME_CACHE)
    conn = sqlite3.connect(DB_PATH, factory=_ClosingConnection)
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
