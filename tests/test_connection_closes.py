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
