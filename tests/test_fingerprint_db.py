"""tools/fingerprint_db.py — the per-table check for the committed-DB drift trap.

The tool exists because `git diff --stat` on a committed sqlite file prints
`Bin N -> N bytes` whether the file is clean or carries hundreds of rows of drift.

These tests exist because an earlier scratch version of the tool was invoked as
`fingerprint HEAD HEAD` and printed "IDENTICAL across all 13 tables" while the working
tree carried 302 rows of drift — it compared two git blobs, both HEAD, and never looked
at the working file. **The tool built to catch a reads-as-confirmation-but-checks-nothing
trap failed in exactly that way.** So the refusal is the load-bearing test here, not the
fingerprinting.

Sides may be literal file paths, which is how these run without a git repository.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tools.fingerprint_db import WORKTREE, compare, fingerprint, main


def _db(path: Path, rows: list[tuple[str, int]], table: str = "prices") -> Path:
    conn = sqlite3.connect(path)
    conn.execute(f"CREATE TABLE {table} (ticker TEXT, n INTEGER)")
    conn.executemany(f"INSERT INTO {table} VALUES (?,?)", rows)
    conn.commit()
    conn.close()
    return path


# ── the refusals: the reason this file exists ────────────────────────────────

def test_identical_revision_specs_are_refused(capsys):
    """`--baseline HEAD --target HEAD` is the exact invocation that reported agreement
    it had not tested. It must exit non-zero and say so."""
    rc = main(["data/demo.db", "--baseline", "HEAD", "--target", "HEAD"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err
    assert "test nothing" in err


def test_two_paths_to_the_same_file_are_refused(tmp_path, capsys):
    """The spec strings can differ while resolving to one file — `./x.db` vs `x.db`.
    Refusing on the resolved path, not just the spec, closes that."""
    db = _db(tmp_path / "a.db", [("VOO", 1)])
    rc = main([str(db), "--baseline", str(db), "--target", str(db.resolve())])
    assert rc == 2
    assert "REFUSED" in capsys.readouterr().err


def test_the_default_target_is_the_working_tree():
    """The default comparison must be the question a reader almost always means:
    what is in my working tree versus what is committed. Pinned so a future edit
    cannot quietly make the default a blob-to-blob comparison again."""
    import inspect

    import tools.fingerprint_db as mod
    src = inspect.getsource(mod.main)
    assert 'default=WORKTREE' in src, "the --target default is not the working tree"
    assert 'default="HEAD"' in src, "the --baseline default is not HEAD"
    assert WORKTREE == "<worktree>"


# ── it reports what it actually compared ────────────────────────────────────

def test_output_names_both_sides(tmp_path, capsys):
    """Every report names both sides, so the output cannot be read as a different
    comparison than the one performed — which is how the original failure survived."""
    a = _db(tmp_path / "a.db", [("VOO", 1)])
    b = _db(tmp_path / "b.db", [("VOO", 1)])
    rc = main([str(a), "--baseline", str(a), "--target", str(b)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "baseline:" in out and "target:" in out
    assert str(a) in out and str(b) in out
    assert "IDENTICAL" in out


def test_drift_is_reported_per_table_with_counts(tmp_path, capsys):
    a = _db(tmp_path / "a.db", [("VOO", 1), ("SPY", 2)])
    b = _db(tmp_path / "b.db", [("VOO", 1), ("SPY", 2), ("QQQ", 3)])
    rc = main([str(a), "--baseline", str(a), "--target", str(b)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "CHANGED" in out and "prices" in out
    assert "2 rows" in out and "3 rows" in out


def test_a_row_edit_that_preserves_the_count_is_still_caught(tmp_path, capsys):
    """The whole point: byte size and row count can both hold while contents change.
    A count-only check would pass here."""
    a = _db(tmp_path / "a.db", [("VOO", 1)])
    b = _db(tmp_path / "b.db", [("VOO", 999)])
    rc = main([str(a), "--baseline", str(a), "--target", str(b)])
    assert rc == 1
    assert "CHANGED" in capsys.readouterr().out


# ── the primitives ──────────────────────────────────────────────────────────

def test_fingerprint_returns_count_and_hash_per_table(tmp_path):
    db = _db(tmp_path / "a.db", [("VOO", 1), ("SPY", 2)])
    fp = fingerprint(db)
    assert set(fp) == {"prices"}
    count, digest = fp["prices"]
    assert count == 2
    assert len(digest) == 16


def test_fingerprint_is_stable_across_calls(tmp_path):
    db = _db(tmp_path / "a.db", [("VOO", 1)])
    assert fingerprint(db) == fingerprint(db)


@pytest.mark.parametrize("baseline,target,kind", [
    ({"t": (1, "aa")}, {},                 "DROPPED"),
    ({},                {"t": (1, "aa")},  "ADDED"),
    ({"t": (1, "aa")}, {"t": (1, "bb")},   "CHANGED"),
])
def test_compare_classifies_each_kind(baseline, target, kind):
    drift = compare(baseline, target)
    assert len(drift) == 1
    assert drift[0][1] == kind


def test_compare_is_empty_for_identical_inputs():
    fp = {"a": (2, "xx"), "b": (0, "yy")}
    assert compare(fp, dict(fp)) == []
