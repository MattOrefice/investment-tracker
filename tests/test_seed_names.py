"""The seed is the source for securities names (#707, owner's decision, November 3, 2026).

src/seed/securities_loader.py now updates an existing ticker's `name` from the seed's
description column, as it already did sleeve_category, tax_efficiency and is_in_saa.
Two seeds write names: the SAA seed (src/seed_securities.py) and the household CSV
(data/seed/securities_household.csv), and bootstrap runs them in that order. So:

  * no ticker may get two different names from two seed files, or each bootstrap
    would write one and then the other;
  * the loader updates a name it finds changed, and leaves every other column alone;
  * BILPX's sleeve is an alternative: no page counts it as cash, and its yield is
    refused rather than assumed (no source publishes one).

Everything here is synthetic or read from committed seeds; no book is written.
"""
from __future__ import annotations

import csv
import importlib
import pkgutil
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = ROOT / "data" / "seed"


def _python_seed_names() -> dict[str, dict[str, str]]:
    """{source: {ticker: name}} for every module-level list of dicts in the Python seeds
    that carries both a ticker and a name."""
    import src
    import src.seed
    modules = [importlib.import_module("src.seed_securities")]
    modules += [importlib.import_module(f"src.seed.{m.name}")
                for m in pkgutil.iter_modules(src.seed.__path__)]
    out: dict[str, dict[str, str]] = {}
    for mod in modules:
        for attr, value in vars(mod).items():
            if (isinstance(value, list) and value and all(isinstance(v, dict) for v in value)
                    and all("ticker" in v and "name" in v for v in value)):
                out[f"{mod.__name__}.{attr}"] = {str(v["ticker"]).strip(): str(v["name"]).strip()
                                                 for v in value}
    return out


def _csv_seed_names() -> dict[str, dict[str, str]]:
    """{source: {ticker: name}} for every CSV under data/seed with a ticker column
    (symbol or ticker) and a name column (description or name)."""
    out: dict[str, dict[str, str]] = {}
    for path in sorted(SEED_DIR.glob("*.csv")):
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            cols = reader.fieldnames or []
            tcol = next((c for c in ("symbol", "ticker") if c in cols), None)
            ncol = next((c for c in ("description", "name") if c in cols), None)
            if tcol and ncol:
                out[path.name] = {r[tcol].strip(): r[ncol].strip() for r in reader
                                  if r[tcol].strip()}
    return out


def test_no_ticker_gets_two_different_names_from_two_seeds():
    sources = {**_python_seed_names(), **_csv_seed_names()}
    # The premise: both seeds that write names are read here.
    assert "src.seed_securities.HOLDINGS" in sources, sorted(sources)
    assert "securities_household.csv" in sources, sorted(sources)
    names: dict[str, dict[str, str]] = {}
    for source, table in sources.items():
        for ticker, name in table.items():
            names.setdefault(ticker, {})[source] = name
    clashes = {t: by for t, by in names.items() if len(set(by.values())) > 1}
    assert not clashes, f"tickers named differently by two seeds: {clashes}"
    shared = [t for t, by in names.items() if len(by) > 1]
    assert shared, "no ticker is in two seeds, so this check compares nothing"


def _book(tmp_path) -> Path:
    db = tmp_path / "book.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE asset_classes (asset_class_id INTEGER PRIMARY KEY, name TEXT);
            INSERT INTO asset_classes VALUES (15, 'Other / Non-SAA');
            CREATE TABLE securities (
                ticker TEXT PRIMARY KEY, name TEXT, asset_class_id INTEGER NOT NULL,
                sleeve_category TEXT, tax_efficiency TEXT, is_in_saa INTEGER,
                expense_ratio REAL);
            INSERT INTO securities VALUES ('OLDN', 'An Old Name', 7, 'thematic', 'low', 0, 0.0042);
        """)
    return db


def test_the_loader_updates_a_changed_name_and_nothing_else(tmp_path):
    from src.seed.securities_loader import load_household_securities
    db = _book(tmp_path)
    seed = tmp_path / "seed.csv"
    seed.write_text("symbol,description,sleeve_category,tax_efficiency,is_in_saa,notes\n"
                    "OLDN,The Corrected Name,us_large_core,high,true,x\n"
                    "NEWT,A New Ticker,cash,low,false,y\n", encoding="utf-8")
    load_household_securities(db, seed)
    with sqlite3.connect(db) as conn:
        rows = {r[0]: r[1:] for r in conn.execute(
            "SELECT ticker, name, asset_class_id, sleeve_category, tax_efficiency, "
            "is_in_saa, expense_ratio FROM securities")}
    # Name, sleeve, efficiency and SAA flag from the seed; the class and fee untouched.
    assert rows["OLDN"] == ("The Corrected Name", 7, "us_large_core", "high", 1, 0.0042)
    # A new ticker is inserted under the parking lot.
    assert rows["NEWT"] == ("A New Ticker", 15, "cash", "low", 0, None)


def test_the_loader_run_twice_changes_nothing_the_second_time(tmp_path):
    from src.seed.securities_loader import load_household_securities
    db = _book(tmp_path)
    load_household_securities(db, SEED_DIR / "securities_household.csv")
    with sqlite3.connect(db) as conn:
        first = conn.execute("SELECT * FROM securities ORDER BY ticker").fetchall()
    load_household_securities(db, SEED_DIR / "securities_household.csv")
    with sqlite3.connect(db) as conn:
        second = conn.execute("SELECT * FROM securities ORDER BY ticker").fetchall()
    assert first == second


def test_bilpx_is_an_alternative_never_a_cash_substitute():
    from src.household import get_substitution_note, _OFF_SAA_SUBSTITUTIONS
    from src.location_config import OFF_SAA_SLEEVE_CATEGORY
    assert _OFF_SAA_SUBSTITUTIONS["liquid_alt"] == ("—", "none")
    assert get_substitution_note("liquid_alt") == "—"
    assert OFF_SAA_SLEEVE_CATEGORY["liquid_alt"] == "Other / Non-SAA"
    import src.sell_plan as sp
    assert sp.BROAD_CLASS_OF_SLEEVE["liquid_alt"] == sp.ALTERNATIVES
    # Nothing else names the sleeve as cash.
    cash_like = {k for k, (equiv, _lvl) in _OFF_SAA_SUBSTITUTIONS.items() if equiv == "Cash"}
    assert "liquid_alt" not in cash_like


def test_bilpxs_yield_is_refused_and_its_character_is_cited():
    from src import location_config as lc
    assert "liquid_alt" not in lc.SLEEVE_ASSUMED_YIELD
    assert "liquid_alt" not in lc.SLEEVE_YIELD_AUTHORED
    assert "liquid_alt" in lc.NOT_MODELLED_SLEEVES
    assert lc.SLEEVE_TAX_CHARACTER["liquid_alt"] == "ordinary"
    src = (ROOT / "src" / "location_config.py").read_text(encoding="utf-8")
    assert "2025 Distributions for Open-End Mutual Funds" in src
    assert "fact sheet (as of June 30, 2026)" in src

    from src.household import _assumed_yield_with_source
    assert _assumed_yield_with_source("liquid_alt", "BILPX", None)[1] == "not_modelled"


def test_the_seed_names_bilpx_as_the_fund_it_is():
    with open(SEED_DIR / "securities_household.csv", newline="", encoding="utf-8") as fh:
        row = next(r for r in csv.DictReader(fh) if r["symbol"] == "BILPX")
    assert row["description"] == "BlackRock Event Driven Equity Fund Institutional"
    assert row["sleeve_category"] == "liquid_alt"


def test_the_loaders_entry_point_names_its_book_and_loads_the_seed(tmp_path, capsys):
    from src.seed import securities_loader as sl
    assert sl.main([]) == 2, "no book named: nothing is written"
    assert sl.main([str(tmp_path / "absent.db")]) == 2
    db = _book(tmp_path)
    assert sl.main([str(db)]) == 0
    with sqlite3.connect(db) as conn:
        n = conn.execute("SELECT COUNT(*) FROM securities").fetchone()[0]
    with open(SEED_DIR / "securities_household.csv", newline="", encoding="utf-8") as fh:
        seeded = {r["symbol"] for r in csv.DictReader(fh) if r["symbol"].strip()}
    assert n == len(seeded | {"OLDN"})
    assert "'errors': 0" in capsys.readouterr().out
