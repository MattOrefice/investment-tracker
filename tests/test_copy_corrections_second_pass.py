"""Copy corrections the earlier passes missed (the 2026-09-25 audit's resumed run).

  * #436: the Trade Log's IEMG and PDBC position theses (18, 21) repeated claims #408
    corrected on the Research page. They now carry their fund's rationale, as SCHP's
    thesis (20) does since item C.
  * #437: US Large Core's rationale said the sleeve is "not the largest US sleeve", and
    at 17.3% it is. The comparison is dropped, in the rationale and in the investment
    thesis (3) that copies it; the weight stays derived.
  * TIP's expense ratio is 0.18% on iShares' fund page ("as stated in the prospectus",
    read 2026-09-27), in the benchmark row and in SCHP's rationale, where it read 0.19%.
  * The unrendered Cash / SPAXX rationale claimed a 2% allocation against a 0% target,
    and thesis 17's target_weight was still the undivided International Developed
    sleeve's 0.2.
  * Asset Evaluation labels its 2022 figures as peak-to-trough declines (the render
    test in tests/render/test_typed_figures_render.py).

Every book that carries a cell is checked: the seed, data/demo.db and the frozen book.
"""
from __future__ import annotations

import sqlite3
import shutil
from pathlib import Path

import pytest

from src.seed_saa import SUB_CLASSES
from src.seed_securities import BENCHMARKS, HOLDINGS

ROOT = Path(__file__).resolve().parent.parent
BOOKS = [ROOT / "data" / "demo.db", ROOT / "tests" / "fixtures" / "frozen_book.db"]
COLUMNS = ("macro_view", "view_summary", "vehicle_rationale")
REVISIT = "\n\n**Would revisit if** "


def _q(book, sql, *args):
    con = sqlite3.connect(f"file:{Path(book).as_posix()}?mode=ro", uri=True)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _thesis(book, thesis_id):
    return dict(zip(COLUMNS, _q(book, f"SELECT {', '.join(COLUMNS)} FROM theses "
                                      "WHERE thesis_id = ?", thesis_id)[0]))


def _fund(ticker):
    return next(h["holding_rationale"] for h in HOLDINGS if h["ticker"] == ticker)


def _sleeve(name):
    return next(s for s in SUB_CLASSES if s["name"] == name)


# ── #436: theses 18 and 21 keep the corrections their fund rationales carry ─────
# #449 copied the fund rationales in. #421's pass corrected the theses item by item
# from there, reading their fees from the data and dropping the two figures derived
# from them ("seven times", "61 bps"), so they no longer equal the fund text: what
# stays pinned is that #449's corrections hold, rendered.

@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_the_iemg_and_pdbc_theses_keep_449s_corrections(book, tmp_path, monkeypatch):
    want = {18: "The two are not the same exposure: IEMG tracks MSCI Emerging Markets "
                "IMI, which adds small caps to the large and mid caps of EEM's MSCI "
                "Emerging Markets index.",
            21: "PDBC is a regulated investment company that holds its futures through a "
                "wholly-owned Cayman Islands subsidiary, so it reports on Form 1099 "
                "instead."}
    for tid, text in want.items():
        cells = _thesis(book, tid)
        for col, cell in zip(cells, _render_in(book, cells.values(), tmp_path, monkeypatch)):
            assert text in cell, (book.name, tid, col)
            for claim in ("identical exposure", "the same MSCI Emerging Markets index",
                          "only broad commodity ETF worth owning", "C-corporation"):
                assert claim not in cell, (book.name, tid, col, claim)


def _render_in(book, texts, tmp_path, monkeypatch):
    """``texts`` rendered against ``book`` (a copy): the theses read their fees from the
    book's securities table since #421."""
    import os
    import src.db as db
    from src.prose_figures import render
    src_path = Path(book) if Path(book).is_absolute() else ROOT / book
    copy = tmp_path / f"render_{src_path.name}"
    if not copy.exists():
        shutil.copyfile(src_path, copy)
        os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    return [render(t) for t in texts]


# ── #437: no "not the largest US sleeve" ──────────────────────────────────────────

def test_the_core_rationale_drops_the_comparison_and_keeps_the_derived_weight():
    text = _sleeve("US Large Core")["rationale"]
    assert "not the largest" not in text
    assert "At {{w:US Large Core}}, Core is sized for that role" in text


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_no_book_says_core_is_not_the_largest_us_sleeve(book):
    (rationale,), = _q(book, "SELECT rationale FROM asset_classes WHERE name = 'US Large Core' "
                             "AND parent_id IS NOT NULL")
    assert rationale == _sleeve("US Large Core")["rationale"]
    for col, cell in _thesis(book, 3).items():
        if cell:
            assert "not the largest" not in cell, (book.name, col)
    # The premise the claim contradicted: Core IS the largest US sleeve.
    us = dict(_q(book, "SELECT name, target_weight FROM asset_classes WHERE name IN "
                       "('US Large Core', 'US Large Quality', 'US Large Value', 'US Small Cap')"))
    assert max(us, key=us.get) == "US Large Core", us


# ── TIP's fee, from iShares' fund page ─────────────────────────────────────────

def test_the_seed_holds_tips_fee_from_the_fund_page():
    tip = next(b for b in BENCHMARKS if b["ticker"] == "TIP")
    assert tip["expense_ratio"] == 0.0018 and tip["er_as_of"] == "2026-09-27"
    # The rationale reads the fee from the securities table since #456; the book's
    # rendered thesis 20 is checked for 0.18% below.
    assert "versus TIP's {{er:TIP}}." in _fund("SCHP")


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_every_book_carries_tips_fee(book, tmp_path, monkeypatch):
    assert _q(book, "SELECT expense_ratio FROM securities WHERE ticker = 'TIP'") == [(0.0018,)]
    (schp,), = _q(book, "SELECT holding_rationale FROM securities WHERE ticker = 'SCHP'")
    assert schp == _fund("SCHP")
    cells = _thesis(book, 20)
    for col, cell in zip(cells, _render_in(book, cells.values(), tmp_path, monkeypatch)):
        assert "versus TIP's 0.18%." in cell and "0.19%" not in cell, (book.name, col)


# ── Cash / SPAXX and thesis 17 ────────────────────────────────────────────────

@pytest.mark.parametrize("book", BOOKS + ["seed"], ids=lambda p: getattr(p, "name", p))
def test_the_cash_rationale_claims_no_allocation(book):
    if book == "seed":
        text, target = _sleeve("Cash / SPAXX")["rationale"], _sleeve("Cash / SPAXX")["target_weight"]
    else:
        (text, target), = _q(book, "SELECT rationale, target_weight FROM asset_classes "
                                   "WHERE name = 'Cash / SPAXX'")
    assert target == 0.0, "premise: the Cash / SPAXX sleeve has no target"
    assert "2% handles" not in text
    assert "The operational cash balance is untargeted" in text


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_thesis_17_holds_international_cores_target(book):
    (weight,), = _q(book, "SELECT target_weight FROM theses WHERE thesis_id = 17")
    (target,), = _q(book, "SELECT target_weight FROM asset_classes WHERE name = "
                          "'International Core' AND parent_id IS NOT NULL")
    funds = _q(book, "SELECT COUNT(*) FROM securities s JOIN asset_classes ac ON "
                     "s.asset_class_id = ac.asset_class_id WHERE ac.name = 'International Core' "
                     "AND COALESCE(s.security_type, '') != 'benchmark'")[0][0]
    assert funds == 1
    assert weight == target, (book.name, weight, target)
