"""Touching a database must not WRITE to it.

``get_connection()`` runs ``_auto_migrate`` on the first connection per DB path
per process. On a DB that needs no migration every migration is a 0-row no-op —
but two statements are issued unconditionally anyway and demand write access at
statement start regardless of matching nothing:

  * the Real-Assets benchmark-label ``UPDATE`` (src/db.py), issued whenever an
    ``asset_classes`` table exists;
  * ``CREATE UNIQUE INDEX IF NOT EXISTS ux_accounts_pseudonym``, issued whenever
    the ``pseudonym`` column exists.

The first is why a read-only ``demo.db``/``tracker.db`` turns ordinary reads into
``attempt to write a readonly database`` — nine test failures in a guarded run,
and the reason a read-only diagnostic cannot be run cleanly. The second happens
to succeed today only because SQLite short-circuits ``IF NOT EXISTS`` before
taking a write lock: write-free by coincidence of an implementation detail, not
by construction. Both are pre-gated behind pure reads; these tests are what makes
that a guarantee rather than a property of today's data.

WHY ``mode=ro`` AND NOT ``chmod``
---------------------------------
Read-only is enforced by SQLite itself via the ``file:…?mode=ro`` URI, so the trap
is deterministic in CI: it does not depend on filesystem permissions, on the
platform's chmod semantics, or on whether the runner happens to be root — under
which ``chmod 0o444`` would not block a write and the test would pass vacuously.

WHY ``copyfile`` AND NOT ``shutil.copy``
----------------------------------------
``shutil.copy`` copies the source's permission bits, so copying a committed DB
that is read-only on disk yields a read-only temp file and the failure lands
somewhere unrelated (see the fixtures in tests/test_attribution.py). ``copyfile``
plus an explicit chmod keeps the copy writable no matter how the source sits.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sqlite3
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import src.db as db
from src.db import SCHEMA, _auto_migrate, initialize_db

_ROOT = pathlib.Path(__file__).resolve().parent.parent

# The three legacy labels the Real-Assets migration heals, and the policy value
# it heals them to. Spelled out literally rather than imported from src.db so the
# test states an independent expectation instead of restating the implementation.
_LEGACY_LABELS = ["VNQ+DBC", "VNQ+DJP", "VNQ (50%) + DBC (50%)"]
_POLICY_LABEL = "VNQ (60%) + DBC (40%)"


def _fresh_schema_db(path: pathlib.Path) -> pathlib.Path:
    """A DB carrying the current schema and nothing else — a fresh personal book."""
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    return path


def _demo_copy(path: pathlib.Path) -> pathlib.Path:
    """A writable copy of the committed demo book — the real-world shape."""
    demo = _ROOT / "data" / "demo.db"
    if not demo.exists():
        pytest.skip("data/demo.db unavailable")
    shutil.copyfile(demo, path)          # NOT shutil.copy — see module docstring
    os.chmod(path, 0o666)
    return path


def _readonly_conn(path: pathlib.Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


@pytest.mark.parametrize("build", [_fresh_schema_db, _demo_copy],
                         ids=["fresh-schema-db", "committed-demo-copy"])
def test_auto_migrate_demands_no_write_on_a_current_schema_db(build, tmp_path):
    """_auto_migrate against a DB needing no migration must complete write-free.

    Fails with 'attempt to write a readonly database' for every statement issued
    unconditionally rather than behind a read-only condition check.
    """
    path = build(tmp_path / "probe.db")
    conn = _readonly_conn(path)
    try:
        _auto_migrate(conn)          # must not raise
    finally:
        conn.close()


def test_initialize_db_demands_no_write_on_a_complete_db(tmp_path, monkeypatch):
    """The whole app entry path (app.py calls this on every launch) must be
    write-free on an already-complete DB, not just _auto_migrate in isolation.

    Widens the guarantee past the two statements under repair: any future
    ungated write added to the schema/seed path fails here too.
    """
    path = _demo_copy(tmp_path / "probe.db")

    real_connect = sqlite3.connect

    def ro_connect(target, *args, **kwargs):
        if str(target) == str(path):
            kwargs["uri"] = True
            return real_connect(f"file:{path.as_posix()}?mode=ro", *args, **kwargs)
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(db.sqlite3, "connect", ro_connect)
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db, "_migrated_paths", set(), raising=False)

    initialize_db()                  # must not raise


def test_auto_migrate_issues_no_write_statement_at_all(tmp_path):
    """No write statement is even ISSUED against a DB needing no migration.

    The mode=ro tests above stop at the first statement that demands a write, so
    they cannot tell a gated statement from one that is merely write-free by
    accident: `CREATE UNIQUE INDEX IF NOT EXISTS` on an existing index succeeds
    read-only because SQLite short-circuits it, so it would slip past them even
    though it is issued unconditionally.

    A SQLite authorizer records every write-class operation and allows it
    through — recording, not denying, so one run enumerates ALL ungated writes
    instead of stopping at the first, unlike the mode=ro tests above.

    SCOPE LIMIT, stated so this test is not read as proving more than it does:
    it does NOT see `CREATE ... IF NOT EXISTS` on an object that already exists.
    SQLite elides such a statement below the authorizer entirely (measured: with
    the index present the authorizer sees nothing; with it dropped it sees
    CREATE_INDEX). So the pseudonym-index statement being gated is a structural
    property no runtime assertion on a current-schema DB can observe. What IS
    observable for that migration is the other direction — that gating it did not
    disable it — which
    `test_auto_migrate_still_creates_a_missing_pseudonym_index` covers.
    """
    path = _demo_copy(tmp_path / "authz.db")
    conn = sqlite3.connect(path)

    write_actions = {
        sqlite3.SQLITE_INSERT: "INSERT", sqlite3.SQLITE_UPDATE: "UPDATE",
        sqlite3.SQLITE_DELETE: "DELETE",
        sqlite3.SQLITE_CREATE_INDEX: "CREATE_INDEX",
        sqlite3.SQLITE_CREATE_TABLE: "CREATE_TABLE",
        sqlite3.SQLITE_DROP_INDEX: "DROP_INDEX",
        sqlite3.SQLITE_DROP_TABLE: "DROP_TABLE",
        sqlite3.SQLITE_ALTER_TABLE: "ALTER_TABLE",
    }
    issued: list[str] = []

    def authorizer(action, arg1, arg2, db_name, trigger):
        if action in write_actions:
            issued.append(f"{write_actions[action]} {arg1 or ''}{'.' + arg2 if arg2 else ''}")
        return sqlite3.SQLITE_OK

    conn.set_authorizer(authorizer)
    try:
        _auto_migrate(conn)
    finally:
        conn.set_authorizer(None)
        conn.close()

    assert not issued, (
        "_auto_migrate issued write statements against a DB that needs no "
        f"migration — each must sit behind a read-only condition check: {issued}")


@pytest.mark.parametrize("legacy", _LEGACY_LABELS)
def test_auto_migrate_still_heals_the_legacy_real_assets_label(legacy, tmp_path):
    """The pre-gate must not silently disable the migration it gates.

    A condition check that is wrong in the other direction — never firing — would
    be invisible: every no-write test above still passes, and the heal quietly
    stops happening. 'VNQ (50%) + DBC (50%)' is the case that makes this matter:
    unlike the other two labels it PARSES (as a 50/50 blend), so an unhealed row
    feeds a wrong Real-Assets benchmark into attribution and the PDF with no error
    anywhere.
    """
    path = _fresh_schema_db(tmp_path / "heal.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO asset_classes (asset_class_id, name, parent_id, target_weight,"
        " benchmark_ticker) VALUES (3, 'Real Assets', NULL, 0.10, ?)", (legacy,))
    conn.execute(
        "INSERT INTO asset_classes (asset_class_id, name, parent_id, target_weight,"
        " benchmark_ticker) VALUES (13, 'Real Assets', 3, 0.10, ?)", (legacy,))
    conn.execute(
        "INSERT INTO asset_classes (asset_class_id, name, parent_id, target_weight,"
        " benchmark_ticker) VALUES (14, 'US Equity', 3, 0.40, 'VOO')")
    conn.commit()

    _auto_migrate(conn)

    rows = dict(conn.execute(
        "SELECT asset_class_id, benchmark_ticker FROM asset_classes").fetchall())
    conn.close()

    assert rows[13] == _POLICY_LABEL, (
        f"sub-class Real Assets row was not healed from {legacy!r} — the gate "
        f"disabled the migration it was meant to gate")
    assert rows[3] == legacy, (
        "parent row (parent_id IS NULL) must be left alone — no benchmark reader "
        "looks at parent rows, and the migration's condition excludes them")
    assert rows[14] == "VOO", "unrelated sleeve row must not be touched"


def test_auto_migrate_still_creates_a_missing_pseudonym_index(tmp_path):
    """Gating the pseudonym index must not disable it either.

    The seed's ON CONFLICT(pseudonym) upsert depends on this uniqueness, so a
    gate that never fires would break account seeding on any book that genuinely
    lacks the index — the same silent-disable failure mode as the Real-Assets
    gate, in the one direction a test can actually see (see the scope limit in
    test_auto_migrate_issues_no_write_statement_at_all).
    """
    path = _fresh_schema_db(tmp_path / "idx.db")
    conn = sqlite3.connect(path)
    conn.execute("DROP INDEX IF EXISTS ux_accounts_pseudonym")
    conn.commit()
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name='ux_accounts_pseudonym'").fetchone() is None, "setup failed"

    _auto_migrate(conn)

    got = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name='ux_accounts_pseudonym'").fetchone()
    conn.close()
    assert got is not None, (
        "the unique pseudonym index was not recreated — the gate disabled the "
        "migration it was meant to gate")


def test_auto_migrate_leaves_an_already_correct_label_alone(tmp_path):
    """A book already on the policy label is untouched — the heal is not a
    blanket overwrite of every Real-Assets row."""
    path = _fresh_schema_db(tmp_path / "noop.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO asset_classes (asset_class_id, name, parent_id, target_weight,"
        " benchmark_ticker) VALUES (13, 'Real Assets', 3, 0.10, ?)", (_POLICY_LABEL,))
    conn.commit()

    _auto_migrate(conn)

    got = conn.execute(
        "SELECT benchmark_ticker FROM asset_classes WHERE asset_class_id = 13"
    ).fetchone()[0]
    conn.close()
    assert got == _POLICY_LABEL
