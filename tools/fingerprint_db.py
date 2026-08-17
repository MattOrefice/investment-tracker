#!/usr/bin/env python
"""Per-table fingerprint of a committed SQLite file — the check for the demo.db trap.

WHY THIS EXISTS. ``git diff --stat`` on a committed sqlite file prints
``Bin N -> N bytes`` whether the file is clean or carries hundreds of rows of drift:
sqlite reuses free pages, so the size does not move and ``--stat`` has nothing to
report on a binary. Any verification that consults size, ``--stat``, or a plain
``git diff`` on a committed DB is reading a constant. PR #205 shipped 303 rows of
unintended drift past a review that had exactly that output in front of it.

WHY IT REFUSES SOME INVOCATIONS. An earlier scratch version of this tool was run as
``fingerprint HEAD HEAD`` and printed "IDENTICAL across all 13 tables" while the
working tree carried 302 rows of drift — it had compared two git blobs, both HEAD, and
inspected the working file not at all. The tool built to catch a
reads-as-confirmation-but-checks-nothing trap failed in precisely that way. So:

* the DEFAULT compares the WORKING TREE against ``HEAD`` — the question you almost
  always mean;
* comparing two committed revisions is available but must be asked for explicitly;
* an invocation whose two sides resolve to the SAME source is refused, non-zero,
  rather than reporting agreement it did not test;
* every report names both sides it compared, so the output cannot be mistaken for a
  different comparison.

Usage
-----
    python tools/fingerprint_db.py data/demo.db
        working tree vs HEAD (the default)

    python tools/fingerprint_db.py data/demo.db --baseline 21bdfdd
        working tree vs an explicit revision

    python tools/fingerprint_db.py data/demo.db --baseline 21bdfdd --target 08b8698
        two committed revisions (explicit; neither side is the working tree)

A side may also be a literal filesystem path, which is how the tests exercise this
without needing a git repository.
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

WORKTREE = "<worktree>"


class NoOpComparison(ValueError):
    """Both sides resolve to the same source, so the comparison tests nothing."""


def fingerprint(db_path: str | Path) -> dict[str, tuple[int, str]]:
    """{table: (row_count, sha256-prefix over its rows)} for every table."""
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        out: dict[str, tuple[int, str]] = {}
        for t in names:
            rows = conn.execute(f'SELECT * FROM "{t}"').fetchall()
            h = hashlib.sha256()
            for r in rows:
                h.update(repr(tuple(r)).encode())
            out[t] = (len(rows), h.hexdigest()[:16])
        return out
    finally:
        conn.close()


def resolve_side(spec: str, db_path: Path, workdir: Path) -> tuple[Path, str]:
    """Materialise one side. Returns (path_to_read, human_label)."""
    if spec == WORKTREE:
        return db_path, f"working tree ({db_path})"
    candidate = Path(spec)
    if candidate.exists() and candidate.is_file():
        return candidate, f"file {candidate}"
    dst = workdir / f"{spec.replace('/', '_')}.db"
    with open(dst, "wb") as fh:
        subprocess.run(["git", "show", f"{spec}:{db_path.as_posix()}"],
                       stdout=fh, check=True)
    return dst, f"revision {spec}"


def compare(baseline: dict, target: dict) -> list[tuple[str, str, str, str]]:
    """[(table, kind, baseline_desc, target_desc)] for every table that differs."""
    drift = []
    for t in sorted(set(baseline) | set(target)):
        a, b = baseline.get(t), target.get(t)
        if a == b:
            continue
        if a is None:
            drift.append((t, "ADDED", "", f"{b[0]} rows"))
        elif b is None:
            drift.append((t, "DROPPED", f"{a[0]} rows", ""))
        else:
            drift.append((t, "CHANGED", f"{a[0]} rows {a[1]}", f"{b[0]} rows {b[1]}"))
    return drift


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("db_path", help="repo-relative path to the committed .db")
    p.add_argument("--baseline", default="HEAD",
                   help="revision or file to compare FROM (default: HEAD)")
    p.add_argument("--target", default=WORKTREE,
                   help="revision or file to compare TO (default: the working tree)")
    args = p.parse_args(argv)

    if args.baseline == args.target:
        # The trap this tool exists to catch, turned on the tool itself.
        print(f"REFUSED: both sides resolve to {args.baseline!r}, so this comparison "
              "would test nothing and report agreement anyway.\n"
              "         Compare the working tree against a revision (the default), or "
              "name two DIFFERENT revisions.", file=sys.stderr)
        return 2

    db_path = Path(args.db_path)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        a_path, a_label = resolve_side(args.baseline, db_path, work)
        b_path, b_label = resolve_side(args.target, db_path, work)
        if Path(a_path).resolve() == Path(b_path).resolve():
            print(f"REFUSED: {a_label} and {b_label} are the same file.",
                  file=sys.stderr)
            return 2

        a, b = fingerprint(a_path), fingerprint(b_path)
        # Both sides are named on every path, so the output cannot be read as a
        # different comparison than the one performed.
        print(f"baseline: {a_label}\ntarget:   {b_label}")
        print(f"tables:   baseline {len(a)}, target {len(b)}\n")
        drift = compare(a, b)
        if not drift:
            print(f"IDENTICAL across all {len(a)} tables (row counts and content "
                  f"hashes) — {a_label} vs {b_label}.")
            return 0
        for t, kind, x, y in drift:
            print(f"  {kind:<8} {t:<26} {x}  ->  {y}")
        print(f"\n{len(drift)} table(s) drifted.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
