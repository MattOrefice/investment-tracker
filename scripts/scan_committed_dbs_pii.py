#!/usr/bin/env python3
"""Scan committed databases for account-number-shaped PII in their accounts table.

The text-grep guards skip binary blobs (``*.db``, ``*.csv``). A committed SQLite
file is exactly how a raw account label once reached a rendered page (Household
View): the value lives inside the binary, invisible to a text scan, yet the page
reads straight from it. A binary the text-grep can't read is precisely the hole
that let an account-derived string surface on a rendered page once.

This check reads the file the page reads. It opens every committed ``*.db`` and,
for each row of the accounts table, matches every text-column value against the
same account-number shape set the text guard uses. The DB is queried properly —
never grep the raw pages: SQLite page boundaries can split a string (false
negatives) and storage/schema bytes can look account-shaped (false positives),
so a byte scan is unreliable. Reading through SQLite sees each value exactly as
the page does.

No allowlist: if a second committed ``.db`` grows an accounts table, it is
scanned too. Kept in lockstep with the text shapes in
``.github/workflows/ci.yml`` and the ``_LITERAL_RE`` consumed by
``tests/test_pii_guards.py`` (which imports the regex below).
"""
from __future__ import annotations

import pathlib
import re
import sqlite3
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Account-number shapes. Kept deliberately narrow to avoid false positives on
# prices/dates:
#   * a bare nine-digit run,
#   * a single uppercase letter followed by six or more digits (the Fidelity
#     "Z-account" family — generalized from the old fixed-width form),
#   * a canonical UUID,
#   * a broker "@" domain suffix (the Fidelity form; bracketed first letter so
#     this pattern string is not itself a matching literal in the file scan).
LITERAL_RE = re.compile(
    r"\b\d{9}\b"
    r"|\b[A-Z]\d{6,}\b"
    r"|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|@[Ff]idelity"
)


def _committed_db_paths(root: pathlib.Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "*.db"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return [rel for rel in out if rel.strip()]


def scan_committed_dbs(root: pathlib.Path = ROOT) -> list[str]:
    """Return location-only offenders (``path :: accounts.<col> rowid=<id>``).

    Never returns the offending value itself — only where it lives.
    """
    offenders: list[str] = []
    for rel in _committed_db_paths(root):
        path = root / rel
        if not path.is_file():
            continue
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            has_accounts = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='accounts'"
            ).fetchone()
            if not has_accounts:
                continue
            cols = [r[1] for r in conn.execute("PRAGMA table_info(accounts)")]
            for row in conn.execute("SELECT rowid, * FROM accounts"):
                rowid, values = row[0], row[1:]
                for col, val in zip(cols, values):
                    if isinstance(val, str) and LITERAL_RE.search(val):
                        offenders.append(f"{rel} :: accounts.{col} rowid={rowid}")
        finally:
            conn.close()
    return offenders


def main() -> int:
    offenders = scan_committed_dbs(ROOT)
    if offenders:
        print("Account-number-shaped values found in committed DB accounts tables "
              "(location only — value withheld):")
        for o in offenders:
            print("  " + o)
        return 1
    print("OK — no account-number-shaped values in committed database accounts tables.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
