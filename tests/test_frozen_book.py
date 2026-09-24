"""The FROZEN TEST BOOK itself (#302, test half): tests/fixtures/frozen_book.db.

It is a committed SQLite file, which this repo's .gitignore refuses by default (every
*.db is ignored; demo.db and this file are the two named exceptions). So what makes the
exception safe is asserted here, not stated in a PR: public origin, #304's format,
nothing date-keyed, a fixed frontier, and a clock pin that agrees with it.
"""
import datetime
import sqlite3
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FROZEN = ROOT / "tests" / "fixtures" / "frozen_book.db"
DEMO = ROOT / "data" / "demo.db"


def _ro(p: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)


def test_public_origin_every_row_that_names_the_book_is_demo_s():
    """Accounts and trades are exactly rows of the PUBLIC demo.db. A book seeded
    from anywhere else (the personal tracker.db above all) fails here, in CI."""
    f, d = _ro(FROZEN), _ro(DEMO)
    for table in ("accounts", "trades", "securities", "theses"):
        frozen_rows = set(f.execute(f"SELECT * FROM {table}"))
        assert frozen_rows, f"{table} is empty in the frozen book"
        assert frozen_rows <= set(d.execute(f"SELECT * FROM {table}")), (
            f"{table} has rows that are not demo.db's: the frozen book must be built "
            "from the public demo.db only (tools/build_frozen_book.py)")
    assert f.execute("SELECT COUNT(*) FROM accounts").fetchone() == (1,)


def test_stored_in_304_format_with_nothing_date_keyed():
    f = _ro(FROZEN)
    n, adj = f.execute("SELECT COUNT(*), COUNT(adj_close) FROM prices").fetchone()
    assert n > 0 and adj == 0, f"{adj} of {n} rows carry a stored adjustment"
    assert f.execute("SELECT COUNT(*) FROM dividends").fetchone()[0] > 0
    assert f.execute("SELECT COUNT(*) FROM macro_cache").fetchone() == (0,), (
        "macro_cache is keyed on the fetch date; a frozen book must not carry it")


def test_the_book_is_frozen_and_the_pinned_today_is_its_next_day(pytestconfig):
    conftest = next(p for p in pytestconfig.pluginmanager.get_plugins()
                    if getattr(p, "FROZEN_TODAY", None) is not None)
    f = _ro(FROZEN)
    frontier = f.execute("SELECT MAX(price_date) FROM prices").fetchone()[0]
    inception = f.execute("SELECT MIN(trade_date) FROM trades").fetchone()[0]
    assert (frontier, inception) == ("2026-07-20", "2025-05-01")
    assert conftest.FROZEN_TODAY == datetime.date.fromisoformat(frontier) + datetime.timedelta(days=1)


def test_the_pin_holds_while_active_and_is_gone_after(pytestconfig):
    """Under the pin, today is the book's next day, a real date (as pandas or sqlite
    hand back) still counts as a date, and no loaded app module escapes the sweep.
    Pinned and unpinned HERE rather than via the module-scoped fixture, so no other
    test in this file runs under a pin."""
    import sys
    import src.holdings  # noqa: F401  (a module that binds `date` at import)
    conftest = next(p for p in pytestconfig.pluginmanager.get_plugins()
                    if getattr(p, "FROZEN_TODAY", None) is not None)
    real = type(datetime.datetime(2026, 1, 2).date())
    with pytest.MonkeyPatch.context() as mp:
        conftest.pin_today(mp)
        assert datetime.date.today() == conftest.FROZEN_TODAY
        assert src.holdings.date.today() == conftest.FROZEN_TODAY
        assert isinstance(datetime.datetime(2026, 1, 2).date(), datetime.date)
        unpinned = sorted(n for n, m in list(sys.modules.items())
                          if n.startswith(("src", "pages")) and getattr(m, "date", None) is real)
        assert not unpinned, f"modules that escaped the clock pin: {unpinned}"
    conftest.unpin_leftovers()
    assert datetime.date is real and src.holdings.date is real
    assert datetime.date.today() != conftest.FROZEN_TODAY


def test_the_ignore_rule_admits_this_file_and_still_refuses_any_other_db():
    """The .gitignore exception is ONE named file. Any other SQLite file, here or
    anywhere, must still be ignored: fail-closed is the rule the exception rides on."""
    def ignored(path: str) -> bool:
        return subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT).returncode == 0
    assert not ignored("tests/fixtures/frozen_book.db")
    assert ignored("tests/fixtures/another_book.db")
    assert ignored("tests/fixtures/tracker.db")
    assert ignored("data/tracker.db")


def test_a_module_imported_during_a_pin_does_not_keep_it(pytestconfig, tmp_path, monkeypatch):
    """The pin must not outlive its module scope through an import made under it."""
    import importlib
    import sys
    conftest = next(p for p in pytestconfig.pluginmanager.get_plugins()
                    if getattr(p, "FROZEN_TODAY", None) is not None)
    (tmp_path / "late_import_probe.py").write_text("from datetime import date\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.MonkeyPatch.context() as mp:
        conftest.pin_today(mp)
        mod = importlib.import_module("late_import_probe")
        assert mod.date.today() == conftest.FROZEN_TODAY      # the pin reached it
    assert mod.date.today() == conftest.FROZEN_TODAY, "premise: the import kept the pin"
    conftest.unpin_leftovers()
    assert mod.date.today() == datetime.date.today() and mod.date is datetime.date
    sys.modules.pop("late_import_probe", None)
