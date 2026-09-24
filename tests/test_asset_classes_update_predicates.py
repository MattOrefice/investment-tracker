"""#288 — every UPDATE asset_classes predicate matches the row it means, and no more.

A migration whose WHERE matches more rows than intended SUCCEEDS SILENTLY: sqlite raises
nothing, the script prints its happy path, and the suite asserts only the rows it meant
to change. asset_classes has NO unique index on name, and "Real Assets" is already two
rows in both books (id 3, the parent; id 13, the sleeve under it). So a name-keyed
predicate that drops its `parent_id IS [NOT] NULL` clause flattens both.

#288's design, applied: assert the PREDICATE once, across the code, instead of editing
shipped migrations that write a tracked binary. Every `UPDATE asset_classes` string in
the repo (tools/, src/ and the rest; tests excluded) is discovered by parsing, and each
must be covered by a rule here. So a new one fails this test until someone states which
row it means to touch.

Rules, by predicate shape:
  name-keyed   exactly ONE row per name the script lists, on each book. The one allowed
               zero is a name a later migration RETIRED by renaming it in that book,
               derived from that migration's own constants, not hardcoded.
  text-keyed   (rationale LIKE, a conditional heal) AT MOST one row. Zero is correct
               once the patch or heal has been applied; these are idempotent.
  primary key  `asset_class_id = ?` is unique by the schema, so the schema is checked.
"""
import ast
import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BOOKS = [ROOT / "data" / "demo.db", ROOT / "data" / "tracker.db"]


def _discover() -> "dict[tuple[str, str], int]":
    """{(relative path, normalised SQL): line} for every string literal containing
    `UPDATE asset_classes`, in every .py outside tests/ (enumerated from the ROOT,
    not from where such statements are expected to live)."""
    found = {}
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(("tests/", ".venv/", "venv/")) or "__pycache__" in rel:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and "update asset_classes" in node.value.lower():
                found[(rel, " ".join(node.value.split()))] = node.lineno
            elif isinstance(node, ast.JoinedStr):   # f-strings: keep the literal parts
                text = "".join(v.value for v in node.values
                               if isinstance(v, ast.Constant) and isinstance(v.value, str))
                if "update asset_classes" in text.lower():
                    found[(rel, " ".join(text.split()))] = node.lineno
    return found


def _mod(name):
    sys.path.insert(0, str(ROOT))
    return importlib.import_module(name)


def _where(sql: str) -> str:
    i = sql.upper().index("WHERE")
    return sql[i:]


# ── the rules: (path, SQL) -> list of (params, expectation) for one book ───────

def _retired_in(conn) -> set:
    """Names a later migration retired by renaming them IN THIS BOOK: phase39 renamed
    International Developed to International Core. Retired only where the new name
    exists and the old one does not, so a book the rename never reached (tracker.db,
    whose intl restructure is held) still owes the old name exactly one row."""
    p39 = _mod("tools.migrate_saa_phase39")
    has = lambda n: conn.execute("SELECT 1 FROM asset_classes WHERE name = ?", (n,)).fetchone()
    return {p39.OLD_NAME} if (has(p39.NEW_CORE) and not has(p39.OLD_NAME)) else set()


def _name_keyed(names):
    return lambda conn: [((n,), "zero-if-retired" if n in _retired_in(conn) else "one")
                         for n in names]


def _rules():
    p25 = _mod("tools.migrate_saa_phase25")
    p38 = _mod("tools.migrate_saa_phase38a")
    parent = "UPDATE asset_classes SET target_weight = ? WHERE name = ? AND parent_id IS NULL"
    sub = "UPDATE asset_classes SET target_weight = ? WHERE name = ? AND parent_id IS NOT NULL"
    like = "UPDATE asset_classes SET rationale = REPLACE(rationale, ?, ?) WHERE rationale LIKE ?"
    em = "UPDATE asset_classes SET rationale = ? WHERE name = ? AND parent_id IS NOT NULL"
    return {
        ("tools/migrate_saa_phase25.py", parent): _name_keyed(p25.PARENT_UPDATES),
        ("tools/migrate_saa_phase25.py", sub): _name_keyed(p25.SUBCLASS_UPDATES),
        ("tools/migrate_saa_phase25.py", like): lambda conn: [
            ((f"%{old[:40]}%",), "at-most-one") for old, _new in p25.RATIONALE_PATCHES],
        ("tools/migrate_saa_phase38a.py", parent): _name_keyed(p38.PARENT_UPDATES),
        ("tools/migrate_saa_phase38a.py", sub): _name_keyed(p38.SUBCLASS_UPDATES),
        ("tools/migrate_saa_phase39.py", em): _name_keyed(["Emerging Markets"]),
        ("tools/migrate_saa_phase39.py",
         "UPDATE asset_classes SET name = ? WHERE asset_class_id = ?"): "primary-key",
        ("tools/migrate_saa_phase39.py",
         "UPDATE asset_classes SET parent_id = ?, target_weight = ?, tolerance_band = ?, "
         "sort_order = ?, rationale = ?, benchmark_ticker = ? WHERE asset_class_id = ?"):
            "primary-key",
        # src/db.py's _auto_migrate heal: the f-string's literal part (the WHERE is the
        # module constant _REAL_ASSETS_LEGACY_WHERE, checked below with its real text).
        ("src/db.py", "UPDATE asset_classes SET benchmark_ticker = 'VNQ (60%) + DBC (40%)'"):
            "db-heal",
    }


def test_every_update_asset_classes_in_the_repo_has_a_rule():
    """Completeness, the half that catches the NEXT migration: a site with no rule
    fails here until its predicate is stated."""
    found, rules = _discover(), _rules()
    unruled = {k: line for k, line in found.items() if k not in rules}
    assert not unruled, (
        "UPDATE asset_classes with no predicate rule; state which row it means to touch "
        f"in {Path(__file__).name}: {unruled}")
    stale = set(rules) - set(found)
    assert not stale, f"rules for statements that no longer exist: {stale}"


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_every_predicate_matches_the_row_it_means_and_no_more(book):
    if not book.exists():
        pytest.skip(f"{book.name} absent (tracker.db is personal and never in CI)")
    conn = sqlite3.connect(f"file:{book.as_posix()}?mode=ro", uri=True)
    db = _mod("src.db")
    problems, checked = [], 0
    for (path, sql), rule in _rules().items():
        if rule == "primary-key":
            pk = [r[1] for r in conn.execute("PRAGMA table_info(asset_classes)") if r[5]]
            assert pk == ["asset_class_id"], f"asset_class_id is no longer the PK: {pk}"
            checked += 1
            continue
        if rule == "db-heal":
            n = conn.execute(
                f"SELECT COUNT(*) FROM asset_classes {db._REAL_ASSETS_LEGACY_WHERE}").fetchone()[0]
            checked += 1
            if n > 1:
                problems.append(f"{path}: the legacy heal matches {n} rows")
            continue
        for params, expect in rule(conn):
            n = conn.execute(f"SELECT COUNT(*) FROM asset_classes {_where(sql)}",
                             params).fetchone()[0]
            checked += 1
            if n > 1 or (expect == "one" and n != 1) or (expect == "zero-if-retired" and n != 0):
                problems.append(f"{path}: {_where(sql)} {params} matches {n} row(s), "
                                f"expected {expect}")
    assert checked >= 30, f"only {checked} predicates checked; the rules stopped resolving"
    assert not problems, "\n".join(problems)
