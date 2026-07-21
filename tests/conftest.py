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
