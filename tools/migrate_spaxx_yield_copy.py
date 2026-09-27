"""Replace SPAXX's typed yield, "~4-5% currently", in data/demo.db's seeded prose
(2026-09-25 audit, item 15b; #370).

The figure dated itself ("currently") and follows short rates, so it went stale
whenever rates moved, with nothing saying when it was true. SPAXX earns roughly the
Treasury bill rate less its fee, and the Macro page shows the live rate, so the prose
says that instead.

Seeded prose is insert-only: src/seed_*.py changes a fresh build only. This carries the
same text into the committed demo book, in the seven cells #370 counted. Each cell must
hold exactly one of the old phrases, or already hold its new one (a second run changes
nothing). Each UPDATE is keyed on the row and its current text and must change exactly
one row, or the run rolls back. Targets data/demo.db explicitly (never get_db_path) and
runs from main() only, so bootstrap never runs it. The personal book is not touched.
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"

# (old, new): the three forms the figure takes in the seeded prose.
REPLACEMENTS = [
    ("SPAXX yields ~4-5% currently, so the drag is muted.",
     "SPAXX earns roughly the Treasury bill rate less its fee, so the drag is muted; the "
     "Macro page shows the live rate."),
    ("the natural cash vehicle — no transaction cost, immediate liquidity, currently ~4-5% "
     "yield.",
     "the natural cash vehicle — no transaction cost and immediate liquidity. It earns "
     "roughly the Treasury bill rate less its fee; the Macro page shows the live rate."),
    ("Money market yield (~4-5% currently) with zero principal risk; drag offset by "
     "rebalancing optionality value and avoidance of forced selling.",
     "Money market yield, roughly the Treasury bill rate less the fund's fee, with zero "
     "principal risk; drag offset by rebalancing optionality value and avoidance of forced "
     "selling. The Macro page shows the live rate."),
]

# One literal statement per (table, column), keyed on the row's primary key and its
# current text, so #288's guard (tests/test_asset_classes_update_predicates.py) sees
# the asset_classes one: a table name in a placeholder would hide it from the guard.
UPDATES = {
    ("asset_classes", "rationale"):
        "UPDATE asset_classes SET rationale = ? WHERE asset_class_id = ? AND rationale = ?",
    ("theses", "macro_view"):
        "UPDATE theses SET macro_view = ? WHERE thesis_id = ? AND macro_view = ?",
    ("theses", "view_summary"):
        "UPDATE theses SET view_summary = ? WHERE thesis_id = ? AND view_summary = ?",
    ("theses", "expected_return_scenario"):
        "UPDATE theses SET expected_return_scenario = ? WHERE thesis_id = ? AND "
        "expected_return_scenario = ?",
    ("theses", "vehicle_rationale"):
        "UPDATE theses SET vehicle_rationale = ? WHERE thesis_id = ? AND vehicle_rationale = ?",
}

# (table, column, key column, key): the seven cells, from #370's count on 2026-09-24.
CELLS = [
    ("asset_classes", "rationale", "asset_class_id", 14),
    ("theses", "macro_view", "thesis_id", 12),
    ("theses", "view_summary", "thesis_id", 12),
    ("theses", "expected_return_scenario", "thesis_id", 12),
    ("theses", "macro_view", "thesis_id", 23),
    ("theses", "view_summary", "thesis_id", 23),
    ("theses", "vehicle_rationale", "thesis_id", 23),
]


def rewrite(text: str) -> str:
    """The cell's text with its one old phrase replaced; unchanged if already done."""
    old_hits = [(o, n) for o, n in REPLACEMENTS if o in text]
    new_hits = [n for _o, n in REPLACEMENTS if n in text]
    if not old_hits:
        if len(new_hits) != 1:
            raise SystemExit(f"ABORT: a cell holds none of the old phrases and {len(new_hits)} "
                             "of the new ones")
        return text
    if len(old_hits) != 1 or text.count(old_hits[0][0]) != 1:
        raise SystemExit("ABORT: a cell holds more than one old phrase")
    o, n = old_hits[0]
    return text.replace(o, n)


def main() -> int:
    conn = sqlite3.connect(DEMO_DB)
    changed = 0
    try:
        with conn:
            for table, col, key_col, key in CELLS:
                row = conn.execute(f"SELECT {col} FROM {table} WHERE {key_col} = ?",
                                   (key,)).fetchone()
                if row is None:
                    raise SystemExit(f"ABORT: {table} {key_col}={key} is missing")
                current = row[0]
                new = rewrite(current)
                if new == current:
                    continue
                n = conn.execute(UPDATES[(table, col)], (new, key, current)).rowcount
                if n != 1:
                    raise SystemExit(f"ABORT: {table}.{col} {key_col}={key} update changed "
                                     f"{n} rows, not 1")
                changed += 1
            left = [(t, c) for t, c, _k, _v in CELLS
                    for (v,) in conn.execute(f"SELECT {c} FROM {t}") if v and "4-5%" in v]
            if left:
                raise SystemExit(f"ABORT: '4-5%' still in {sorted(set(left))}")
    finally:
        conn.close()
    print(f"{DEMO_DB.name}: {changed} cell(s) changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
