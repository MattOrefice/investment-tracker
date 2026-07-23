"""Phase 46: the personal International Developed -> 4-sleeve split, gated on carriers.

Guards the SELF-CONDITIONING wrapper (migrate_saa_phase46_personal_intl_split):
  * pending + no carriers in acct_01's ledger -> HOLD (no-op, refuses)
  * pending + all three carriers held         -> apply the split
  * already split                             -> no-op
  * no "International Developed" to split      -> no-op
  * idempotent

The split body is migrate_saa_phase39._patch_db (reused verbatim); the fixture is a
compact-but-complete SAA (weights sum to 1.0, International Developed at 0.20/0.98) so
_patch_db's own read-back asserts pass.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

_DEV = 0.20 / 0.98           # International Developed target (== the four intl sleeves' sum)
_FILLER = 1.0 - _DEV

_SCHEMA = """
CREATE TABLE asset_classes (
    asset_class_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    parent_id        INTEGER,
    target_weight    REAL NOT NULL,
    tolerance_band   REAL NOT NULL DEFAULT 0.05,
    sort_order       INTEGER,
    rationale        TEXT,
    benchmark_ticker TEXT
);
CREATE TABLE securities (
    ticker         TEXT PRIMARY KEY,
    name           TEXT,
    asset_class_id INTEGER,
    security_type  TEXT,
    expense_ratio  REAL,
    holding_rationale TEXT,
    sleeve_category TEXT,
    is_in_saa      INTEGER
);
CREATE TABLE accounts (
    account_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT, pseudonym TEXT
);
CREATE TABLE trades (
    trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER, ticker TEXT
);
"""


def _m46():
    spec = importlib.util.spec_from_file_location(
        "m46", str(_ROOT / "tools" / "migrate_saa_phase46_personal_intl_split.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed(db_path, *, carriers=(), already_split=False, no_developed=False):
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    # Parents + a filler equity sleeve so the sub-class weights sum to 1.0.
    conn.execute("INSERT INTO asset_classes (asset_class_id, name, parent_id, target_weight) VALUES (1,'Equity',NULL,0)")
    conn.execute("INSERT INTO asset_classes (name, parent_id, target_weight, tolerance_band, sort_order) "
                 "VALUES ('US Large Core', 1, ?, 0.03, 10)", (_FILLER,))
    if not no_developed and not already_split:
        conn.execute("INSERT INTO asset_classes (asset_class_id, name, parent_id, target_weight, tolerance_band, sort_order) "
                     "VALUES (9,'International Developed', 1, ?, 0.03, 50)", (_DEV,))
        conn.execute("INSERT INTO securities (ticker,name,asset_class_id,sleeve_category,is_in_saa) "
                     "VALUES ('VEA','Vanguard Dev',9,'intl_developed',1)")
        conn.execute("INSERT INTO securities (ticker,name,asset_class_id,sleeve_category,is_in_saa) "
                     "VALUES ('IEFA','iShares Dev',9,'intl_developed',0)")
        for t in ("IDHQ", "AVIV", "AVDV"):
            conn.execute("INSERT INTO securities (ticker,name,asset_class_id,sleeve_category,is_in_saa) "
                         "VALUES (?,?,1,?,1)", (t, t, "intl_" + t.lower()))
    if already_split:
        # The post-split shape: Core (id 9) + the three tilts already present.
        conn.execute("INSERT INTO asset_classes (asset_class_id, name, parent_id, target_weight, tolerance_band, sort_order) "
                     "VALUES (9,'International Core', 1, ?, 0.02, 50)", (0.20 * 17 / 49 / 0.98,))
        for nm, num, so in [("International Quality", 15, 52), ("International Large Value", 9, 54),
                            ("International Small Value", 8, 56)]:
            conn.execute("INSERT INTO asset_classes (name,parent_id,target_weight,tolerance_band,sort_order) "
                         "VALUES (?,1,?,0.02,?)", (nm, 0.20 * num / 49 / 0.98, so))
    conn.execute("INSERT INTO accounts (account_id, name, pseudonym) VALUES (1,'Personal Fidelity','acct_01')")
    for t in carriers:
        conn.execute("INSERT INTO trades (account_id, ticker) VALUES (1, ?)", (t,))
    conn.commit()
    conn.close()


def _sleeve_names(db_path):
    conn = sqlite3.connect(str(db_path))
    out = {r[0] for r in conn.execute("SELECT name FROM asset_classes WHERE parent_id IS NOT NULL")}
    conn.close()
    return out


def test_holds_without_carriers_and_refuses(tmp_path, capsys):
    db = tmp_path / "hold.db"
    _seed(db, carriers=())                       # no IDHQ/AVIV/AVDV in the ledger
    assert _m46().migrate_db(db) == 0
    names = _sleeve_names(db)
    assert "International Developed" in names and "International Quality" not in names
    msg = capsys.readouterr().out
    assert "HELD" in msg and "0/3" in msg and "invariant" in msg


def test_holds_with_only_partial_carriers(tmp_path):
    db = tmp_path / "partial.db"
    _seed(db, carriers=("IDHQ", "AVIV"))         # 2/3 — a sleeve would still be unfunded
    assert _m46().migrate_db(db) == 0
    assert "International Quality" not in _sleeve_names(db)


def test_applies_with_all_three_carriers(tmp_path):
    db = tmp_path / "apply.db"
    _seed(db, carriers=("IDHQ", "AVIV", "AVDV"))
    assert _m46().migrate_db(db) == 1
    names = _sleeve_names(db)
    assert {"International Core", "International Quality", "International Large Value",
            "International Small Value"} <= names
    assert "International Developed" not in names

    conn = sqlite3.connect(str(db)); conn.row_factory = sqlite3.Row
    def ac(t):
        return conn.execute("SELECT a.name FROM securities s JOIN asset_classes a "
                            "ON s.asset_class_id=a.asset_class_id WHERE s.ticker=?", (t,)).fetchone()[0]
    assert ac("IDHQ") == "International Quality"
    assert ac("AVIV") == "International Large Value"
    assert ac("AVDV") == "International Small Value"
    assert ac("VEA") == "International Core"          # id 9 preserved through the rename
    total = conn.execute("SELECT SUM(target_weight) FROM asset_classes WHERE parent_id IS NOT NULL").fetchone()[0]
    conn.close()
    assert abs(total - 1.0) < 1e-9


def test_idempotent_after_apply(tmp_path):
    db = tmp_path / "idem.db"
    _seed(db, carriers=("IDHQ", "AVIV", "AVDV"))
    assert _m46().migrate_db(db) == 1
    assert _m46().migrate_db(db) == 0


def test_noop_when_already_split(tmp_path):
    db = tmp_path / "split.db"
    _seed(db, already_split=True, carriers=())     # Intl Quality exists -> no-op regardless
    assert _m46().migrate_db(db) == 0


def test_noop_when_no_international_developed(tmp_path):
    db = tmp_path / "none.db"
    _seed(db, no_developed=True)
    assert _m46().migrate_db(db) == 0


def test_exposes_migrate_db_for_discovery():
    assert callable(getattr(_m46(), "migrate_db", None))
