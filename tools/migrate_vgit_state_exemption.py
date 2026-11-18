"""VGIT's prose names no state: Treasury interest is exempt from state and local income
tax under federal law (#710).

The sentence this replaces said Treasury interest was "a real, if modest, after-tax
advantage at" one named state's "flat rate". The rule set on #706 and #710 is that no
committed code, comment, doc or test names a home state; a book's seeded prose is held
to it too. The replacement says what the source says, and only that, and cites it:

    31 U.S.C. § 3124(a): "Stocks and obligations of the United States Government are
    exempt from taxation by a State or political subdivision of a State. The exemption
    applies to each form of taxation that would require the obligation, the interest on
    the obligation, or both, to be considered in computing a tax", with two exceptions
    (a nondiscriminatory franchise tax or another nonproperty tax instead of one,
    imposed on a corporation; and an estate or inheritance tax).
    Read on November 17, 2026, in the U.S. Code's 2024 edition on govinfo.gov (USCODE-
    2024-title31, sec. 3124). The section was last amended on October 22, 1986
    (Pub. L. 99-514).

The old sentence's comparison with corporate bond funds is not in that source, so the
replacement does not carry it.

TWO SPELLINGS OF THE OLD SENTENCE. The committed books join its two clauses with a
colon; a book seeded before the colon replaced the dash joins them with " — ". The
pattern takes either, and nothing else.

THE CELLS. VGIT's securities.holding_rationale, which every book holds and which must
carry one sentence or the other, and thesis 19's macro_view, vehicle_rationale and
view_summary, which hold the same sentence in a book that has them. A thesis cell with
neither sentence is left as it is and reported: its prose is that book's own.

THE #283 SHAPE (tools/migrate_demo_vgit_jurisdiction.py). A targeted substring swap,
so the rest of each cell is preserved byte for byte. Every UPDATE must match exactly one
row, or the whole run rolls back and nothing is written. Idempotent: a cell already
carrying the new sentence is left alone, and a second run writes nothing.

The old sentence is matched by a pattern, not typed out, so this file names no state.
The book is named on the command line and never defaulted, because the same tool writes
data/demo.db and, in a separate step, the personal book:

    python tools/migrate_vgit_state_exemption.py data/demo.db
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

OLD_SENTENCE = re.compile(
    r"Critically, Treasury interest income is exempt from state and local taxes(?::| —) "
    r"a real, if modest, after-tax advantage at [A-Z][A-Za-z .]*'s flat rate over "
    r"investment-grade corporate bond funds with similar yield\.")
NEW_SENTENCE = (
    "Treasury interest is exempt from state and local income tax under federal law "
    "(31 U.S.C. § 3124(a); U.S. Code, 2024 edition, read November 17, 2026).")

# (table, column, key column, key, required)
CELLS: tuple[tuple[str, str, str, object, bool], ...] = (
    ("securities", "holding_rationale", "ticker", "VGIT", True),
    ("theses", "macro_view", "thesis_id", 19, False),
    ("theses", "vehicle_rationale", "thesis_id", 19, False),
    ("theses", "view_summary", "thesis_id", 19, False),
)


class MigrationRefused(Exception):
    """A cell this tool cannot reason about; nothing was written."""


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,)).fetchone() is not None


def migrate(db_path: Path) -> dict[str, str]:
    """Rewrite the cells in ``db_path``. Returns {"table.column": outcome}, where the
    outcome is "updated", "already", or "left as it is". Raises MigrationRefused, with
    nothing written, on a required cell that holds neither sentence, a cell holding the
    old sentence more than once, or an UPDATE that does not match exactly one row."""
    db_path = Path(db_path)
    if not db_path.is_file():
        raise MigrationRefused(f"{db_path} is not a file")
    conn = sqlite3.connect(db_path)
    try:
        outcomes: dict[str, str] = {}
        for table, column, key_col, key, required in CELLS:
            name = f"{table}.{column}"
            row = (conn.execute(f'SELECT "{column}" FROM "{table}" WHERE "{key_col}" = ?',
                                (key,)).fetchone()
                   if _has_table(conn, table) else None)
            text = row[0] if row else None
            if text is None:
                if required:
                    raise MigrationRefused(f"{name} for {key} is missing")
                outcomes[name] = "left as it is"
                continue
            found = OLD_SENTENCE.findall(text)
            if NEW_SENTENCE in text and not found:
                outcomes[name] = "already"
                continue
            if len(found) != 1:
                if required or found:
                    raise MigrationRefused(
                        f"{name} for {key} holds the old sentence {len(found)} times and "
                        f"the new one {text.count(NEW_SENTENCE)} times; inspect it first")
                outcomes[name] = "left as it is"
                continue
            updated = OLD_SENTENCE.sub(NEW_SENTENCE, text, count=1)
            cur = conn.execute(
                f'UPDATE "{table}" SET "{column}" = ? WHERE "{key_col}" = ?', (updated, key))
            if cur.rowcount != 1:
                conn.rollback()
                raise MigrationRefused(
                    f"UPDATE of {name} matched {cur.rowcount} rows, expected exactly 1; "
                    "rolled back")
            outcomes[name] = "updated"
        conn.commit()
        return outcomes
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def main(argv: "list[str] | None" = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python tools/migrate_vgit_state_exemption.py <db>")
        return 2
    try:
        outcomes = migrate(Path(args[0]))
    except MigrationRefused as e:
        print(f"ABORT, nothing written: {e}")
        return 1
    for name, outcome in outcomes.items():
        print(f"{name}: {outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
