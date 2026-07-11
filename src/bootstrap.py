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
from pathlib import Path

import src.db as _db

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
        return {"migrations": [], "db_path": str(path), "skipped": True}

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
    seed_household_accounts(path)                      # 7 household accounts (UPSERT)

    _bootstrapped.add(str(path))
    logger.info("Personal-mode bootstrap complete (migrations: %s)", migrations)
    return {"migrations": migrations, "db_path": str(path)}
