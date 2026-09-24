"""#307 — `with get_connection() as conn:` commits or rolls back AND THEN CLOSES.

sqlite3's own context manager never closes. Every one of the 97 `with
get_connection()` sites held its connection open until garbage collection, and on
Windows an open handle blocks deleting the file. That is how it surfaced: copies of
the personal book left in TEMP by the suite's redirect cleanup, which swallowed the
error. These tests pin both halves: the connection closes, and a cleanup that cannot
remove something says so.
"""
import sqlite3
import sys

import pytest

import src.db as db


@pytest.fixture
def scratch_db(tmp_path, monkeypatch):
    path = tmp_path / "scratch.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db, "_migrated_paths", {str(path)})   # skip _auto_migrate
    return path


def _rows(path):
    c = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return c.execute("SELECT a FROM t ORDER BY a").fetchall()
    finally:
        c.close()


def test_the_connection_is_closed_after_its_block(scratch_db):
    with db.get_connection() as conn:
        conn.execute("CREATE TABLE t (a INTEGER)")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")


def test_a_clean_block_commits_before_closing(scratch_db):
    """Closing must not lose the write: the commit happens first."""
    with db.get_connection() as conn:
        conn.execute("CREATE TABLE t (a INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
    assert _rows(scratch_db) == [(1,)]


def test_a_failing_block_rolls_back_and_still_closes(scratch_db):
    with db.get_connection() as conn:
        conn.execute("CREATE TABLE t (a INTEGER)")
    with pytest.raises(RuntimeError, match="boom"):
        with db.get_connection() as conn:
            conn.execute("INSERT INTO t VALUES (2)")
            raise RuntimeError("boom")
    assert _rows(scratch_db) == [], "the failed block's insert was committed"
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")


def test_explicit_close_callers_are_unaffected(scratch_db):
    """One caller (tests/test_bootstrap.py) uses the connection without `with` and
    closes it itself. Only __exit__ changed, so that still works."""
    conn = db.get_connection()
    conn.execute("CREATE TABLE t (a INTEGER)")
    conn.execute("INSERT INTO t VALUES (3)")
    conn.commit()
    conn.close()
    assert _rows(scratch_db) == [(3,)]


def test_row_factory_and_foreign_keys_survive_the_subclass(scratch_db):
    with db.get_connection() as conn:
        assert conn.row_factory is sqlite3.Row
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


# ── the redirect cleanup reports what it could not remove ─────────────────────

def _conftest_plugin(config):
    """The LOADED conftest, from the plugin manager. `import conftest` can build a
    second copy of the module, re-running its module-level sqlite3.connect patch."""
    return next(p for p in config.pluginmanager.get_plugins()
                if getattr(p, "remove_tree_reporting", None) is not None)


def test_cleanup_returns_nothing_when_everything_is_removed(tmp_path, pytestconfig):
    remove = _conftest_plugin(pytestconfig).remove_tree_reporting
    d = tmp_path / "copies"
    d.mkdir()
    (d / "tracker.db").write_bytes(b"x")
    assert remove(d) == []
    assert not d.exists()


def test_cleanup_reports_a_failure_instead_of_swallowing_it(tmp_path, pytestconfig, monkeypatch):
    """Platform-independent: force the removal of one file to fail and require the
    failure to come back, naming the path. `ignore_errors=True` returned nothing."""
    import shutil as _shutil
    conftest = _conftest_plugin(pytestconfig)
    d = tmp_path / "copies"
    d.mkdir()
    victim = d / "tracker.db"
    victim.write_bytes(b"x")

    real_rmtree = _shutil.rmtree

    def rmtree_that_fails(path, onerror=None, **kw):
        onerror(__import__("os").unlink, str(victim),
                (PermissionError, PermissionError("in use by another process"), None))

    monkeypatch.setattr(conftest.shutil, "rmtree", rmtree_that_fails)
    failures = conftest.remove_tree_reporting(d)
    monkeypatch.setattr(conftest.shutil, "rmtree", real_rmtree)
    assert len(failures) == 1
    assert "tracker.db" in failures[0] and "PermissionError" in failures[0]


@pytest.mark.skipif(sys.platform != "win32",
                    reason="only Windows refuses to delete a file with an open handle")
def test_an_open_connection_is_reported_not_hidden(tmp_path, pytestconfig):
    """The real failure, on the platform where it happens: a connection left open
    makes the copy undeletable, and the cleanup must say so."""
    remove = _conftest_plugin(pytestconfig).remove_tree_reporting
    d = tmp_path / "copies"
    d.mkdir()
    held = sqlite3.connect(str(d / "tracker.db"))
    held.execute("CREATE TABLE t (a)")
    held.commit()
    try:
        failures = remove(d)
        assert failures and "tracker.db" in failures[0], failures
    finally:
        held.close()
    assert remove(d) == []


# ── a leaked connection is caught DETERMINISTICALLY (#340) ─────────────────────
# Python 3.11's sqlite3 emits no ResourceWarning for an unclosed connection, so the
# redirect holds every connection it opens by a strong reference and checks it after
# the opening test's teardown. Before this, a leak was caught only when garbage
# collection had not yet closed it: test_seed leaked in every run, and only short
# runs said so.

def _demo_db():
    from pathlib import Path
    return Path(__file__).resolve().parent.parent / "data" / "demo.db"


def test_garbage_collection_cannot_close_a_leak_before_it_is_checked(pytestconfig, request):
    """The determinism the old check lacked: drop every reference the test holds and
    collect, and the redirected connection is STILL open in the registry."""
    import gc
    conftest = _conftest_plugin(pytestconfig)
    sqlite3.connect(str(_demo_db())).execute("SELECT 1")   # leaked on purpose
    gc.collect()   # here to PROVE the point; the harness itself never collects
    mine = [c for c, phase, test in conftest._open_redirected
            if test == request.node.nodeid and conftest._is_open(c)]
    assert len(mine) == 1 and mine[0].execute("SELECT 1").fetchone() == (1,)
    mine[0].close()   # this test's own leak, closed so its teardown check passes


def test_the_check_reports_and_closes_an_open_connection_and_drops_closed_ones(pytestconfig):
    conftest = _conftest_plugin(pytestconfig)
    leaked, closed = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
    closed.close()
    conftest._open_redirected.extend([(leaked, "call", "T::leaks"), (closed, "call", "T::clean")])
    assert conftest._take_leaks(lambda phase, test: test.startswith("T::")) == ["T::leaks (call)"]
    assert not conftest._is_open(leaked), "the check must close what it reports"
    assert all(t not in ("T::leaks", "T::clean") for _c, _p, t in conftest._open_redirected)


def test_a_setup_phase_connection_is_left_for_the_session_end_check(pytestconfig):
    """A fixture broader than one test may hold a connection across tests, so a
    per-test check that flagged setup-phase opens would misfire."""
    conftest = _conftest_plugin(pytestconfig)
    held = sqlite3.connect(":memory:")
    conftest._open_redirected.append((held, "setup", "T::fixture"))
    try:
        per_test = lambda phase, test: phase == "call" and test == "T::fixture"
        assert conftest._take_leaks(per_test) == []
        assert any(c is held for c, _p, _t in conftest._open_redirected)
    finally:
        conftest._take_leaks(lambda phase, test: test == "T::fixture")
