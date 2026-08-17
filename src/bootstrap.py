"""Personal-mode database bootstrap — startup wiring only.

A fresh (or under-migrated) ``tracker.db`` needs more than the base ``SCHEMA``
that ``initialize_db()`` creates: the Phase 25.3 ``securities`` sleeve columns +
parking-lot asset class, the Phase 25.4 ``fund_compositions`` table, and the
household seeds (SAA taxonomy, securities, compositions, accounts). Those
migrations and seeds already exist and the desktop ran them long ago, but a
fresh personal-mode boot never did — so pages 13/14 crashed on the missing
tables/columns.

This module *wires the existing pieces* into one idempotent bootstrap. It
authors no schema, no seed data, and no classifications.

IT ALSO READS THE NEWEST HOLDINGS CSV — a deliberate widening of what this module
is, recorded here because the sentence above ("wires the existing pieces") no
longer describes the whole function. After the seeds run, ``unmapped_holdings``
reconciles the held symbols against ``securities`` and returns any that the
location register would silently drop. Bootstrap is the only place both inputs
exist at the moment they matter: it runs at every personal-mode start, it already
loads the seed, and the check cannot run in CI at all (holdings are gitignored,
and a committed fixture would have frozen holdings and pass forever while the real
book drifted — see #217). It REPORTS, never raises: ``app.py`` calls this
unwrapped at import, so a raise here takes down every page, and the fix is a CSV
edit that is not in the app. Same reasoning the phase-46 migration already
settled for its own guard (see its module docstring).

Generic migration discovery: every ``tools/migrate_*.py`` that exposes a
``migrate_db(db_path)`` is run, in filename order, against the current DB — so a
future migration following that convention is covered automatically, with no
migration named here. The two SAA migrations expose only ``main()`` and patch
BOTH ``tracker.db`` and ``demo.db`` by hardcoded path; they are deliberately NOT
run here (a personal bootstrap must not touch ``demo.db``). A fresh DB gets the
current SAA taxonomy from ``seed_saa`` instead.

Demo mode is never bootstrapped here — ``demo.db`` is committed and prebuilt.
"""
from __future__ import annotations

import importlib.util
import logging
import sqlite3
from pathlib import Path

import src.db as _db

# The self-directed taxable book's household-facing label. acct_01's `name` stays
# "Personal Fidelity" (db.py's idempotency key); only the rendered display_name
# changes, and only in personal (this module is never run for demo).
_SELF_DIRECTED_DISPLAY_NAME = "Individual Taxable (Self-Directed)"

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_TOOLS = _ROOT / "tools"
_HOUSEHOLD_CSV = _ROOT / "data" / "seed" / "securities_household.csv"

# Paths already bootstrapped in THIS process — skips the (idempotent but non-trivial)
# migration+seed pass on every Streamlit rerun. Cleared by tests exercising DB-level
# idempotency. Not a correctness guard: the bootstrap is safe to run repeatedly.
_bootstrapped: set[str] = set()


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"_bootstrap_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_pending_migrations(db_path: Path) -> list[str]:
    """Run every ``tools/migrate_*.py`` exposing ``migrate_db(db_path)``, in
    filename order, against ``db_path``. Each such migration is idempotent
    (guards on IF NOT EXISTS / existing columns). Migrations exposing only
    ``main()`` (the SAA ones, which hardcode both tracker.db + demo.db) are
    skipped — no demo side effect, and the SAA state comes from ``seed_saa``.
    """
    ran: list[str] = []
    for py in sorted(_TOOLS.glob("migrate_*.py")):
        mod = _load_module(py)
        migrate_db = getattr(mod, "migrate_db", None)
        if callable(migrate_db):
            migrate_db(db_path)
            ran.append(py.stem)
    return ran


_SEED_CSV_NAME = "data/seed/securities_household.csv"
_REGISTER_COLUMNS = ("sleeve_category", "tax_efficiency")


def unmapped_holdings(
    db_path: "str | Path | None" = None,
    uploads_dir: "str | Path | None" = None,
    account_map_path: "str | Path | None" = None,
) -> dict[str, list[str]]:
    """Held symbols the location register would silently drop, and why.

    Returns ``{symbol: [reasons]}`` — either ``["no securities row at all"]`` or the
    names of the NULL columns. Empty dict means nothing to report.

    build_location_register drops a row when EITHER column is empty
    (``household.py:850``), so both are checked together; which one is missing is
    returned because a reader fixing the wrong column learns nothing.

    NEVER RAISES. Every failure mode returns ``{}``:

    * no dated positions CSV — a first run has nothing to reconcile, and reporting
      every seeded symbol as unmapped would fire the notice on a state that is not
      wrong;
    * no ``securities`` table — a pre-migration DB. Bootstrap calls this after the
      seeds so it should not happen, but a check that crashes the app it exists to
      protect is the failure mode this whole item avoids;
    * an unreadable CSV, or one naming an account number absent from
      ``private/account_map.json`` — the ingest raises there by design and the page
      reports it directly, so this check has nothing to add and must not crash trying.

    ``account_map_path`` exists only so the check is testable without the gitignored
    private map; production passes nothing.

    Deliberately silent on ``tax_treatment``, the register's third condition: that
    comes from the ACCOUNTS merge, is a different table with a different cause, and
    is not fixed by editing the securities seed.
    """
    from src.household_data import find_latest_positions_csv

    db = Path(db_path) if db_path is not None else Path(_db.DB_PATH)
    csv_path = find_latest_positions_csv(uploads_dir)
    if csv_path is None or not Path(csv_path).exists():
        return {}

    try:
        from src.ingestion.fidelity import parse_fidelity_csv
        parsed = parse_fidelity_csv(str(csv_path), account_map_path=account_map_path)
        held = sorted({s for s in parsed["symbol"].dropna()})
    except Exception:                                   # unreadable / unmapped account
        logger.warning("unmapped_holdings: could not parse %s; skipping the check",
                       csv_path, exc_info=True)
        return {}
    if not held:
        return {}

    try:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT ticker, sleeve_category, tax_efficiency FROM securities"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:                               # no table, no file, locked
        logger.warning("unmapped_holdings: securities unreadable in %s; skipping", db)
        return {}

    by_ticker = {r["ticker"]: r for r in rows}
    out: dict[str, list[str]] = {}
    for sym in held:
        row = by_ticker.get(sym)
        if row is None:
            out[sym] = ["no securities row at all"]
            continue
        missing = [c for c in _REGISTER_COLUMNS if row[c] is None]
        if missing:
            out[sym] = missing
    return out


def unmapped_holdings_notice(findings: dict[str, list[str]]) -> "str | None":
    """The reader-facing sentence for :func:`unmapped_holdings`, or None if clean.

    None when there is nothing to say — a notice that always renders teaches the
    reader to skip it.

    States that the pages FAIL rather than that data is missing, because they do:
    Asset Location and Household View both raise on this state (#217), so
    "incomplete" would understate it. Names the file to edit, because the fix is not
    in the app.
    """
    if not findings:
        return None
    n = len(findings)
    detail = "; ".join(f"**{sym}** ({', '.join(reasons)})"
                       for sym, reasons in sorted(findings.items()))
    # "not FULLY mapped", not "not mapped to a sleeve": a symbol missing only
    # tax_efficiency does have a sleeve, and the narrower phrasing would be wrong for
    # that half of the findings.
    return (
        f"**{n} held symbol{'' if n == 1 else 's'} "
        f"{'is' if n == 1 else 'are'} not fully mapped:** {detail}. "
        "The **Asset Location** and **Household View** pages will fail until this is "
        f"fixed — add the symbol{'' if n == 1 else 's'} to `{_SEED_CSV_NAME}` and "
        "reload; the next start picks it up."
    )


def bootstrap_personal_db() -> dict:
    """Idempotently bring the current personal-mode DB (``src.db.DB_PATH``) to a
    render-ready state: base schema -> all pending migrations -> household seeds.

    Safe on a fresh 0-byte DB, a partially-migrated DB, and a fully-populated one
    (no duplicate rows, no re-migration errors). Operates on ``src.db.DB_PATH``
    (read live so tests can monkeypatch it); the seed helpers that route through
    ``get_connection()`` target the same path.
    """
    path = Path(_db.DB_PATH)
    if str(path) in _bootstrapped:
        return {"migrations": [], "db_path": str(path), "skipped": True,
                "unmapped_holdings": unmapped_holdings(path)}

    from src import seed_saa, seed_securities
    from src.seed.securities_loader import load_household_securities
    from src.seed.fund_compositions import seed_fund_compositions
    from src.seed.household_accounts import seed_household_accounts

    # 1. Base schema + the 'Personal Fidelity' account (idempotent).
    _db.initialize_db()

    # 2. SAA taxonomy, BEFORE the migrations. seed_saa skips if asset_classes is
    #    non-empty, and the Phase 25.3 migration inserts a parking-lot asset class
    #    that would trip that guard and silently drop the whole SAA taxonomy (which
    #    seed_securities then needs to map its ETFs to an asset_class_id). The order
    #    is dictated by the seeds' own guards, not preference.
    seed_saa.seed()                                    # asset_classes (INSERT OR IGNORE)

    # 3. All pending migrations (generic discovery; each idempotent) — adds the
    #    parking-lot class + securities sleeve columns + fund_compositions table.
    migrations = run_pending_migrations(path)

    # 4. Remaining household seeds, in dependency order (each idempotent or
    #    per-ticker guarded).
    seed_securities.seed()                             # SAA ETFs -> real asset_class_id
    load_household_securities(path, _HOUSEHOLD_CSV)    # enrich + household rows (UPSERT)
    seed_fund_compositions(path)                       # compositions (UPSERT)
    seed_household_accounts(path)                      # 6 household accounts (UPSERT)

    # 5. Label acct_01 (the db.py base account = the self-directed taxable book,
    #    carrying the trade ledger) with its household-facing display_name. Done
    #    HERE, in the personal bootstrap, rather than in a migration: demo never
    #    runs this path (so demo's acct_01 keeps "Personal Fidelity"), and it
    #    re-applies on every personal bootstrap including a fresh reseed — which a
    #    one-time migration gated on the now-removed acct_taxable_01 row would miss.
    #    Only display_name changes; `name` stays "Personal Fidelity" (db.py's key).
    _conn = sqlite3.connect(str(path))
    try:
        _conn.execute(
            "UPDATE accounts SET display_name = ? "
            "WHERE pseudonym = 'acct_01' AND display_name IS NOT ?",
            (_SELF_DIRECTED_DISPLAY_NAME, _SELF_DIRECTED_DISPLAY_NAME),
        )
        _conn.commit()
    finally:
        _conn.close()

    _bootstrapped.add(str(path))
    logger.info("Personal-mode bootstrap complete (migrations: %s)", migrations)
    # AFTER the seeds: load_household_securities has just UPSERTed, so this
    # reconciles against the freshest mapping rather than the pre-seed state.
    return {"migrations": migrations, "db_path": str(path),
            "unmapped_holdings": unmapped_holdings(path)}
