"""#212: a sleeve's category has exactly one source, and "equity" is derived from it.

The hand-kept EQUITY_SLEEVES set this replaced let `.isin()` drop the three intl tilt
sleeves from the thematic card's equity denominator without a word, and the error grew
with the position. Now (household.sleeve_categories / equity_sleeves):

  * an SAA sleeve's category is the root of the taxonomy class its is_in_saa=1
    security is filed under;
  * an off-SAA sleeve's is declared in location_config.OFF_SAA_SLEEVE_CATEGORY;
  * a HELD sleeve with neither RAISES, and a sleeve with both raises.

The positive runs the REAL phase-46 migration on a fixture shaped like the personal
book BEFORE the split: carriers flagged is_in_saa=1 and parked under 'Other / Non-SAA',
exactly as the household securities loader leaves them. The migration's own test
fixture files them under the Equity root instead, which is not what tracker.db holds,
so this one is built to match the book rather than reused.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sqlite3

import pandas as pd
import pytest

import src.location_config as lc
from src.household import equity_sleeves, sleeve_categories
from src.location_actions import _fmt_dollars, _thematic_equity_figures

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_TILTS = {"intl_quality", "intl_large_value", "intl_small_value"}
_CARRIER_OF = {"intl_quality": "IDHQ", "intl_large_value": "AVIV", "intl_small_value": "AVDV"}

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
    ticker            TEXT PRIMARY KEY,
    name              TEXT,
    asset_class_id    INTEGER,
    security_type     TEXT,
    expense_ratio     REAL,
    holding_rationale TEXT,
    sleeve_category   TEXT,
    is_in_saa         INTEGER
);
CREATE TABLE accounts (account_id INTEGER PRIMARY KEY, name TEXT, pseudonym TEXT);
CREATE TABLE trades (trade_id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER, ticker TEXT);
"""

_DEV = 0.20 / 0.98   # International Developed, which the split divides four ways

# (ticker, class name, sleeve, is_in_saa, held dollars). The personal book's shape,
# pre-split: SAA tickers filed under SAA classes; everything else, the three carriers
# and SPAXX included, parked under 'Other / Non-SAA'.
_BOOK = [
    ("VOO",   "US Large Core",          "us_large_core",    1, 10_000.0),
    ("VEA",   "International Developed", "intl_developed",  1,  4_000.0),
    ("VGIT",  "Core Fixed Income",      "core_fi_treasury", 1,  3_000.0),
    # A listed equity the taxonomy files under Real Assets. The retired set left it
    # out of equity, and the taxonomy must too: moving it would be a classification
    # decision, not a refactor.
    ("VNQ",   "Real Assets",            "real_assets_reit", 1,  2_000.0),
    ("QQQ",   "Other / Non-SAA",        "us_large_growth",  0,  1_500.0),   # declared Equity
    ("HLIPX", "Other / Non-SAA",        "core_fi_credit",   0,  1_200.0),   # declared Income
    ("SPAXX", "Other / Non-SAA",        "cash",             1,    300.0),   # flagged, parked, declared
    ("RFUTX", "Other / Non-SAA",        "target_date",      0,  1_000.0),   # a blend: looked through
    ("IDHQ",  "Other / Non-SAA",        "intl_quality",     1,    227.0),
    ("AVIV",  "Other / Non-SAA",        "intl_large_value", 1,    227.0),
    ("AVDV",  "Other / Non-SAA",        "intl_small_value", 1,    227.0),
]
_RFUTX_MIX = [("us_large_core", 0.60), ("intl_developed", 0.10), ("core_fi_treasury", 0.30)]


def _build(db, *, logged=()):
    """The pre-split book, with `logged` carrier buys in acct_01's trade ledger."""
    conn = sqlite3.connect(str(db))
    conn.executescript(_SCHEMA)
    for i, root in [(1, "Equity"), (2, "Income"), (3, "Real Assets"), (4, "Cash"),
                    (15, "Other / Non-SAA")]:
        conn.execute("INSERT INTO asset_classes (asset_class_id, name, parent_id, target_weight) "
                     "VALUES (?, ?, NULL, 0)", (i, root))
    # Sub-classes summing to 1.0 so migrate_saa_phase39._patch_db's read-back passes.
    for i, name, parent, w in [(5, "US Large Core", 1, 0.50), (9, "International Developed", 1, _DEV),
                               (11, "Core Fixed Income", 2, 0.20),
                               (13, "Real Assets", 3, 1.0 - 0.50 - _DEV - 0.20),
                               (14, "Cash / SPAXX", 4, 0.0)]:
        conn.execute("INSERT INTO asset_classes (asset_class_id, name, parent_id, target_weight, "
                     "tolerance_band, sort_order) VALUES (?, ?, ?, ?, 0.03, ?)",
                     (i, name, parent, w, i * 10))
    ids = {n: i for i, n in conn.execute("SELECT asset_class_id, name FROM asset_classes "
                                         "WHERE name != 'Real Assets' OR parent_id IS NOT NULL")}
    for t, cls, sleeve, saa, _v in _BOOK:
        conn.execute("INSERT INTO securities (ticker, name, asset_class_id, sleeve_category, "
                     "is_in_saa) VALUES (?, ?, ?, ?, ?)", (t, t, ids[cls], sleeve, saa))
    conn.execute("INSERT INTO accounts VALUES (1, 'Personal Fidelity', 'acct_01')")
    for t in logged:
        conn.execute("INSERT INTO trades (account_id, ticker) VALUES (1, ?)", (t,))
    conn.commit()
    conn.close()


def _frames(db):
    conn = sqlite3.connect(str(db))
    sec = pd.read_sql_query("SELECT * FROM securities", conn)
    ac = pd.read_sql_query("SELECT asset_class_id, name, parent_id FROM asset_classes", conn)
    conn.close()
    return sec, ac


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, str(_ROOT / rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _m46():
    return _load("m46", "tools/migrate_saa_phase46_personal_intl_split.py")


def _positions(symbols):
    return pd.DataFrame([{"pseudonym": "acct_01", "symbol": t, "current_value": v}
                         for t, _c, _s, _saa, v in _BOOK if t in symbols])


_COMPS = pd.DataFrame([{"fund_symbol": "RFUTX", "underlying_sleeve": s, "weight": w}
                       for s, w in _RFUTX_MIX])
_WITHOUT_CARRIERS = {t for t, *_ in _BOOK} - set(_CARRIER_OF.values())


def _held(sec, symbols):
    """sleeve -> one symbol holding it, as-held."""
    s = sec[sec["ticker"].isin(symbols)]
    return dict(zip(s["sleeve_category"], s["ticker"]))


# ── the vocabulary ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("book", ["data/demo.db", "tests/fixtures/frozen_book.db"])
def test_every_declared_category_is_a_root_of_each_committed_taxonomy(book):
    """'Category' means one thing whichever source supplies it: a declared value must
    be a word the taxonomy itself uses at its top level. Read against each committed
    book, since the resolver validates against whichever taxonomy it is handed."""
    conn = sqlite3.connect(f"file:{_ROOT / book}?mode=ro", uri=True)
    roots = {r[0] for r in conn.execute(
        "SELECT name FROM asset_classes WHERE parent_id IS NULL")}
    conn.close()
    assert set(lc.OFF_SAA_SLEEVE_CATEGORY.values()) <= roots, (
        set(lc.OFF_SAA_SLEEVE_CATEGORY.values()) - roots)
    assert lc.EQUITY_CATEGORY in roots


def test_no_declaration_names_a_pending_sleeve():
    """A declared tilt would clear the window's error and then collide with the
    taxonomy the moment the split lands."""
    assert not set(lc.OFF_SAA_SLEEVE_CATEGORY) & set(lc.PENDING_TAXONOMY_SLEEVES)


def test_pending_sleeves_are_exactly_what_the_split_files():
    """The window's error names the phase-46 migration for these sleeves, so the set
    must be the migration's own: the sleeves migrate_saa_phase39 writes onto the
    carriers, which phase 46 reuses verbatim. And the step it describes must match
    the migration's gate: the carriers it counts, and the path it lives at."""
    m39 = _load("m39", "tools/migrate_saa_phase39.py")
    assert set(lc.PENDING_TAXONOMY_SLEEVES) == set(m39.NEW_HOLDING_CATEGORIES.values())
    carriers = _m46()._CARRIERS
    for sleeve, step in lc.PENDING_TAXONOMY_SLEEVES.items():
        assert (_ROOT / "tools" / "migrate_saa_phase46_personal_intl_split.py").exists()
        assert "tools/migrate_saa_phase46_personal_intl_split.py" in step
        for t in carriers:
            assert t in step, f"{sleeve}'s step does not name carrier {t}"
        assert "restart the app" in step, "the migration runs once per process"


# ── inert: the pre-split book keeps the retired membership ────────────────────

def test_pre_split_categories_come_from_one_source_each(tmp_path):
    db = tmp_path / "pre.db"
    _build(db)
    sec, ac = _frames(db)
    cats = sleeve_categories(sec, ac)
    assert cats["us_large_core"] == "Equity"           # taxonomy
    assert cats["intl_developed"] == "Equity"          # taxonomy
    assert cats["core_fi_treasury"] == "Income"        # taxonomy
    assert cats["real_assets_reit"] == "Real Assets"   # taxonomy: NOT equity, as before
    assert cats["us_large_growth"] == "Equity"         # declared
    assert cats["core_fi_credit"] == "Income"          # declared
    assert cats["cash"] == "Cash"                      # declared: SPAXX is parked
    assert cats["target_date"] == "Other / Non-SAA"    # declared
    assert not _TILTS & set(cats), "pre-split, the tilts have no source at all"


def test_unheld_pending_sleeves_do_not_raise(tmp_path):
    """Today's book: the tilts exist in securities but nobody holds them."""
    db = tmp_path / "pre.db"
    _build(db)
    sec, ac = _frames(db)
    got = equity_sleeves(_held(sec, _WITHOUT_CARRIERS), sec, ac)
    assert got == {"us_large_core", "intl_developed", "us_large_growth"}


# ── the window: carriers held, split not yet run ──────────────────────────────

@pytest.mark.parametrize("logged, migrate", [
    (("IDHQ",), True),                      # partial: the migration HOLDS
    (("IDHQ", "AVIV"), True),               # partial: still HOLDS
    (("IDHQ", "AVIV", "AVDV"), False),      # all logged, app not restarted yet
])
def test_window_raises_naming_the_migration_never_a_declaration(tmp_path, logged, migrate):
    db = tmp_path / "window.db"
    _build(db, logged=logged)
    if migrate:
        assert _m46().migrate_db(db) == 0, "fixture: the migration should hold"
    sec, ac = _frames(db)
    pos = _positions(_WITHOUT_CARRIERS | set(logged))

    # The page's path: page 14 -> resolve_placeholders -> this.
    with pytest.raises(ValueError) as exc:
        _thematic_equity_figures(pos, sec, _COMPS, ac)
    msg = str(exc.value)
    for t in logged:
        assert t in msg
    assert "tools/migrate_saa_phase46_personal_intl_split.py" in msg
    assert "restart the app" in msg
    assert "Do NOT declare" in msg
    # The non-pending branch's instruction must not leak into this one.
    assert "Declare it there" not in msg


# ── positive: after the real migration, the tilts are equity ──────────────────

def _expected_equity(symbols, equity):
    comp = dict(_RFUTX_MIX)
    total = 0.0
    for t, _c, sleeve, _saa, v in _BOOK:
        if t not in symbols:
            continue
        if t == "RFUTX":
            total += v * sum(w for s, w in comp.items() if s in equity)
        elif sleeve in equity:
            total += v
    return total


def test_after_the_split_the_tilts_count_as_equity(tmp_path):
    """No edit to any list: the migration files the carriers under International
    Quality / Large Value / Small Value, and their category follows from the taxonomy.
    The hand-kept list this replaced fails here: it has none of the three."""
    db = tmp_path / "split.db"
    _build(db, logged=("IDHQ", "AVIV", "AVDV"))
    assert _m46().migrate_db(db) == 1, "fixture: the split should apply"
    sec, ac = _frames(db)
    everything = {t for t, *_ in _BOOK}

    eq = equity_sleeves(_held(sec, everything), sec, ac)
    assert _TILTS <= eq
    cats = sleeve_categories(sec, ac)
    for s in _TILTS:
        assert s not in lc.OFF_SAA_SLEEVE_CATEGORY and cats[s] == "Equity"

    # The card's denominator carries the carriers' dollars, looked through as the page does.
    got = _thematic_equity_figures(_positions(everything), sec, _COMPS, ac)
    equity = {"us_large_core", "intl_developed", "us_large_growth"} | _TILTS
    want = _expected_equity(everything, equity)
    without_tilts = _expected_equity(everything, equity - _TILTS)
    assert want - without_tilts == pytest.approx(3 * 227.0)
    assert got["lookthrough_equity_value"] == _fmt_dollars(want)
    assert got["lookthrough_equity_value"] != _fmt_dollars(without_tilts)


# ── one source each: the raises ───────────────────────────────────────────────

def test_a_declared_sleeve_that_enters_the_taxonomy_raises(tmp_path, monkeypatch):
    """The issue's own scenario. Declared while off-SAA, then the split files it."""
    monkeypatch.setitem(lc.OFF_SAA_SLEEVE_CATEGORY, "intl_quality", "Equity")
    db = tmp_path / "both.db"
    _build(db, logged=("IDHQ", "AVIV", "AVDV"))
    sec, ac = _frames(db)
    assert sleeve_categories(sec, ac)["intl_quality"] == "Equity"   # one source: fine
    assert _m46().migrate_db(db) == 1
    sec, ac = _frames(db)
    with pytest.raises(ValueError, match="two sources") as exc:
        sleeve_categories(sec, ac)
    assert "intl_quality" in str(exc.value) and "IDHQ" in str(exc.value)
    assert "delete its line" in str(exc.value)


def test_a_held_undeclared_off_saa_sleeve_raises_naming_where_it_goes(tmp_path, monkeypatch):
    monkeypatch.delitem(lc.OFF_SAA_SLEEVE_CATEGORY, "us_large_growth")
    db = tmp_path / "gap.db"
    _build(db)
    sec, ac = _frames(db)
    with pytest.raises(ValueError) as exc:
        equity_sleeves(_held(sec, {"VOO", "QQQ"}), sec, ac)
    msg = str(exc.value)
    assert "'us_large_growth'" in msg and "QQQ" in msg
    assert "OFF_SAA_SLEEVE_CATEGORY" in msg and "src/location_config.py" in msg
    assert "Declare it there" in msg
    # Unheld, the same gap is not an error: only a held sleeve can leave a denominator.
    assert equity_sleeves(_held(sec, {"VOO"}), sec, ac) == {"us_large_core"}


def test_a_flagged_but_parked_sleeve_says_so(tmp_path, monkeypatch):
    """SPAXX's shape: is_in_saa=1 under the parking lot. Not pending, so the message
    offers both honest fixes rather than naming a migration that files nothing."""
    monkeypatch.delitem(lc.OFF_SAA_SLEEVE_CATEGORY, "cash")
    db = tmp_path / "cash.db"
    _build(db)
    sec, ac = _frames(db)
    with pytest.raises(ValueError) as exc:
        equity_sleeves(_held(sec, {"SPAXX"}), sec, ac)
    msg = str(exc.value)
    assert "SPAXX is flagged is_in_saa=1 but still filed under 'Other / Non-SAA'" in msg
    assert "Declare it there" in msg and "phase-46" not in msg


def test_a_declared_category_outside_the_taxonomy_raises(tmp_path, monkeypatch):
    monkeypatch.setitem(lc.OFF_SAA_SLEEVE_CATEGORY, "us_large_growth", "Equities")
    db = tmp_path / "vocab.db"
    _build(db)
    sec, ac = _frames(db)
    with pytest.raises(ValueError, match="roots are") as exc:
        sleeve_categories(sec, ac)
    assert "Equities" in str(exc.value)


def test_an_saa_sleeve_filed_under_two_roots_raises(tmp_path):
    db = tmp_path / "split_root.db"
    _build(db)
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO securities (ticker, name, asset_class_id, sleeve_category, is_in_saa) "
                 "VALUES ('IEF', 'IEF', 11, 'us_large_core', 1)")   # us_large_core under Income
    conn.commit()
    conn.close()
    sec, ac = _frames(db)
    with pytest.raises(ValueError, match="more than one root"):
        sleeve_categories(sec, ac)


def test_no_equity_root_raises_rather_than_returning_nothing(tmp_path):
    db = tmp_path / "noequity.db"
    _build(db)
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE asset_classes SET name = 'Stocks' WHERE asset_class_id = 1")
    conn.commit()
    conn.close()
    sec, ac = _frames(db)
    with pytest.raises(ValueError, match="no 'Equity' root"):
        equity_sleeves(_held(sec, {"VOO"}), sec, ac)


def test_without_a_taxonomy_the_equity_figures_are_none(tmp_path):
    """Same contract as a missing compositions frame: no basis, no figure. (render_prose
    raising on a None figure is pinned in test_location_actions.)"""
    db = tmp_path / "pre.db"
    _build(db)
    sec, _ac = _frames(db)
    got = _thematic_equity_figures(_positions(_WITHOUT_CARRIERS), sec, _COMPS, None)
    assert set(got.values()) == {None}


# ── the owner's book (skips without it) ───────────────────────────────────────

# The equity set as it was hand-kept before #212, frozen here as the INERTNESS pin.
_RETIRED_EQUITY_SLEEVES = frozenset({
    "us_large_core", "us_large_quality", "us_large_value", "us_large_growth",
    "us_small_core", "us_small_value", "us_mid_cap",
    "us_sector_tech", "us_sector_healthcare",
    "intl_developed", "intl_all_exus", "emerging_markets",
    "hedged_equity", "single_stock", "thematic",
})


def test_live_book_is_the_retired_set_plus_at_most_the_split():
    """On tracker.db the derived equity set is the retired hand-kept set, plus the
    three tilts once the split lands, and nothing else. Pre-split, and post-split.

    RED HERE IS A CLASSIFICATION CHANGE, NOT A TEST TO UPDATE. It means an existing
    sleeve changed category: a security refiled in the taxonomy (VNQ under Equity
    would move real_assets_reit) or a declaration edited. Make that decision
    explicitly, then update this pin."""
    db = _ROOT / "data" / "tracker.db"
    if not db.exists() or db.stat().st_size == 0:
        pytest.skip("personal-mode tracker.db absent")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    sec = pd.read_sql_query("SELECT * FROM securities", conn)
    ac = pd.read_sql_query("SELECT asset_class_id, name, parent_id FROM asset_classes", conn)
    conn.close()
    cats = sleeve_categories(sec, ac)
    derived = {s for s, c in cats.items() if c == lc.EQUITY_CATEGORY}
    assert derived - _TILTS == _RETIRED_EQUITY_SLEEVES
    assert derived & _TILTS in (set(), _TILTS), "the split lands all three at once"
    # And every sleeve the book knows has a source, pending tilts aside.
    assert set(sec["sleeve_category"].dropna()) - set(cats) <= _TILTS
