"""Shared test fixtures — chiefly the DB-pinning fixtures.

WHY THIS EXISTS
---------------
The app resolves its mode from the repo-root .env (TRACKER_MODE=personal) so a
normal local run correctly targets the personal book — that is correct and must
not change. But it makes .env the ambient default for the WHOLE test suite too,
and mode is frozen at import: src.config._resolved_mode and src.db.DB_PATH are
both computed once when those modules load, so setting TRACKER_MODE (or the inert
DB_PATH) in os.environ AFTER import does nothing. A test that "wants demo" via
os.environ.setdefault was silently reading the personal book — the invisible
ambient state behind five contradictory baselines.

`use_demo_db` / `use_personal_db` OVERRIDE that frozen state explicitly and
restore it after the test, so a test's DB is a property of the test, not of
whatever the shell exported. TRACKER_MODE is the real knob; DB_PATH the env var
is inert and is deliberately not used here.
"""
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _pin_mode(monkeypatch, mode: str) -> None:
    """Repoint every import-frozen mode/DB binding to `mode`, restored on teardown.

    src.db.DB_PATH is what get_connection() actually opens; src.config._resolved_mode
    / IS_DEMO drive is_demo()/is_write_enabled(). All three are frozen at import, so
    all three are overridden here — setting the env var alone would be a no-op.
    """
    import src.config as cfg
    import src.db as db

    db_path = _ROOT / "data" / ("demo.db" if mode == "demo" else "tracker.db")
    monkeypatch.setattr(cfg, "_resolved_mode", mode)
    monkeypatch.setattr(cfg, "IS_DEMO", mode == "demo")
    monkeypatch.setattr(db, "DB_PATH", db_path)


@pytest.fixture
def use_demo_db(monkeypatch):
    """Pin this test to the public demo book, overriding .env=personal."""
    _pin_mode(monkeypatch, "demo")
    yield


@pytest.fixture
def use_personal_db(monkeypatch):
    """Pin this test to the personal tracker book explicitly."""
    _pin_mode(monkeypatch, "personal")
    yield


@pytest.fixture
def no_ambient_db(tmp_path, monkeypatch):
    """Repoint src.db.DB_PATH at an EMPTY sqlite file for this test.

    OPT-IN, never autouse: request it in a test's signature to assert that the
    test reaches NO database. The ~50 honest DB-backed tests that legitimately
    read data are untouched — pinning this autouse would break every one of them.

    Why it exists: production scopes reads with the inline idiom
    ``f(..., account_id=get_portfolio_account_id())``. The resolver argument
    evaluates BEFORE a mock intercepts the wrapped call, so patching only the data
    function (``get_holdings_on_date`` / ``get_portfolio_value_series`` / …) does
    NOT stop the DB hit — and against the ambient DB it silently resolves to
    account 1 and the test passes, masking the coupling. Under this fixture the
    resolver instead opens an empty DB with no ``accounts`` table and RAISES,
    turning that silent coupling into a hard failure. Keep it on any unit test that
    mocks its data layer and must not touch a database, so the next instance of the
    same shape fails loudly instead of passing by accident. Pairs with patching the
    resolver at the module boundary (e.g. ``src.factors.get_portfolio_account_id``).
    """
    import src.db as db
    empty = tmp_path / "no_ambient_db.db"          # created on first connect; no schema
    monkeypatch.setattr(db, "DB_PATH", empty)
    monkeypatch.setattr(db, "_migrated_paths", set(), raising=False)
    return empty
