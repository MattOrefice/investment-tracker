"""Typed figures, derived (the 2026-09-25 audit's resumed run, item D).

  * #406 item 5: the SAA sleeve rationales quoted weights from an earlier target set.
    They carry tokens now (src/prose_figures.py), filled from the targets.
  * #406 item 6: Asset Evaluation's 2022 paragraph typed four figures that disagreed
    with each other. Each is now the year's decline in the book's own series.
  * #406 item 7: SPAXX's rationale gave cash a "3% weight"; cash has no target. It now
    states the operational share, derived, and that cash is untargeted.
  * #406 item 10: Stage 1's caption typed "60/40" whichever naive benchmark was
    selected. The labels derive from the naive benchmark's weights.

The pages that render these are checked in tests/render/test_typed_figures_render.py.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src import benchmarks
from src.asset_evaluation import decline_in_year
from src.prose_figures import render

ROOT = Path(__file__).resolve().parent.parent
BOOKS = ["data/demo.db", "tests/fixtures/frozen_book.db"]
INTL = ["International Core", "International Quality", "International Large Value",
        "International Small Value"]


def _book(rel):
    con = sqlite3.connect(f"file:{(ROOT / rel).as_posix()}?mode=ro", uri=True)
    try:
        targets = {n: w for n, w in con.execute(
            "SELECT name, target_weight FROM asset_classes ORDER BY parent_id IS NOT NULL")}
        rationales = {n: r for n, r in con.execute(
            "SELECT name, rationale FROM asset_classes WHERE parent_id IS NOT NULL")}
    finally:
        con.close()
    return targets, rationales


def _p(x: float) -> str:
    return f"{x * 100:.1f}%"


# ── item 5 ───────────────────────────────────────────────────────────────────

# Each typed weight as it read before, in the text around it.
TYPED = ["The 16% weight", "Quality at 14%", "14% expresses", "8% out of the 38%",
         "is 21% of US", "7% is enough", "Core is 34.7%", "a 20% region", "is 20% of the",
         "The 8% weight", "a 9.18% region", "3.2%, 2.8%", "6% in a 78%", "4% is 40%",
         "10% is large"]


@pytest.mark.parametrize("where", ["src/seed_saa.py"] + BOOKS)
def test_no_sleeve_rationale_types_a_weight(where):
    if where.endswith(".db"):
        text = " ".join(_book(where)[1].values())
    else:
        text = " ".join((ROOT / where).read_text(encoding="utf-8").split())
    assert [t for t in TYPED if t in text] == []


@pytest.mark.parametrize("book", BOOKS)
def test_the_rendered_rationales_state_the_targets(book):
    """Every figure, recomputed here from the targets the book holds."""
    t, r = _book(book)
    got = {n: render(text, targets=t) for n, text in r.items() if text}
    large = t["US Large Core"] + t["US Large Quality"] + t["US Large Value"]
    intl = sum(t[s] for s in INTL)
    assert (f"At {_p(t['US Large Core'])}, Core is sized for that role, because most US "
            "large-cap exposure") in got["US Large Core"]
    assert f"{_p(t['US Large Quality'])} expresses high" in got["US Large Quality"]
    assert (f"{_p(t['US Large Value'])} out of the {_p(large)} total in US large caps is "
            f"{_p(t['US Large Value'] / large)} of US large-cap exposure") in got["US Large Value"]
    assert f"{_p(t['US Small Cap'])} is enough to matter" in got["US Small Cap"]
    assert f"Core is {_p(t['International Core'] / intl)} of international" in got["International Core"]
    assert f"to a {_p(intl)} region" in got["International Core"]
    assert f"international is {_p(intl)} of the portfolio" in got["International Small Value"]
    em = t["Emerging Markets"]
    mirrored = ", ".join(_p(em * t[s] / intl) for s in INTL[:3]) + ", and " + _p(em * t[INTL[3]] / intl)
    assert f"The {_p(em)} weight" in got["Emerging Markets"]
    assert f"into a {_p(em)} region yields sleeves of roughly {mirrored}." in got["Emerging Markets"]
    assert (f"{_p(t['Core Fixed Income'])} in a {_p(t['Equity'])} growth portfolio"
            in got["Core Fixed Income"])
    assert f"{_p(t['TIPS'])} is {_p(t['TIPS'] / t['Income'])} of the fixed-income" in got["TIPS"]
    assert f"{_p(t['Real Assets'])} is large enough" in got["Real Assets"]
    assert not [n for n, text in got.items() if "{{" in text]


def test_the_demo_books_figures_as_read_off_its_targets():
    """The derived values on the demo book, read off the data (17.3 not 16, and so on)."""
    t, r = _book("data/demo.db")
    value = render(r["US Large Value"], targets=t)
    assert "9.2% out of the 41.8% total in US large caps is 22.0%" in value
    assert "At 17.3%, Core is sized" in render(r["US Large Core"], targets=t)
    assert "6.1% in a 79.6% growth portfolio" in render(r["Core Fixed Income"], targets=t)


def test_a_target_that_moves_moves_the_prose():
    """The derivation's positive control: the same text, other targets."""
    t, r = _book("data/demo.db")
    moved = dict(t, **{"US Large Value": 0.10})
    assert "10.0% out of the" in render(r["US Large Value"], targets=moved)


def test_a_token_naming_no_sleeve_of_the_book_raises():
    with pytest.raises(KeyError):
        render("{{w:International Developed}}", targets={"International Core": 0.07})


def test_text_without_tokens_renders_unchanged():
    assert render("The personal book's typed 16% stays as written.", targets={}) == (
        "The personal book's typed 16% stays as written.")


# ── item 7 ───────────────────────────────────────────────────────────────────

def _page_spaxx_rationale() -> str:
    """The Research page's SPAXX_RATIONALE, read with ast: its comment quotes the old
    text, and a search of the source would find the comment."""
    import ast
    tree = ast.parse((ROOT / "pages" / "8_Research.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and getattr(n.targets[0], "id", None) == "SPAXX_RATIONALE")
    return ast.literal_eval(node.value)


def test_spaxx_says_cash_is_untargeted_and_types_no_weight():
    from src.seed_position_theses import SPAXX_VEHICLE_RATIONALE
    page = _page_spaxx_rationale()
    assert page == SPAXX_VEHICLE_RATIONALE
    assert "The operational cash balance, {{cash}}, is untargeted liquidity" in page
    assert "3% cash weight" not in page
    for book in BOOKS:
        con = sqlite3.connect(f"file:{(ROOT / book).as_posix()}?mode=ro", uri=True)
        try:
            (cell,) = con.execute("SELECT vehicle_rationale FROM theses WHERE title = "
                                  "'Cash / SPAXX — SPAXX'").fetchone()
        finally:
            con.close()
        assert cell == SPAXX_VEHICLE_RATIONALE, book


# ── item 6 ───────────────────────────────────────────────────────────────────

def test_decline_in_year_is_that_years_drawdown():
    idx = pd.to_datetime(["2021-12-31", "2022-01-03", "2022-06-01", "2022-12-30", "2023-01-03"])
    r = pd.Series([0.50, 0.10, -0.50, 0.20, -0.90], index=idx)
    assert decline_in_year(r) == pytest.approx(0.50)   # 1.10 -> 0.55; 2021 and 2023 ignored
    assert np.isnan(decline_in_year(r.loc[:"2021"]))


def test_the_2022_paragraph_types_no_figure():
    src = (ROOT / "pages" / "5_Asset_Evaluation.py").read_text(encoding="utf-8")
    for typed in ("approximately 65%", "roughly 20%", "bonds fell 15%", "over 60%"):
        assert typed not in src, typed


# ── item 10 ──────────────────────────────────────────────────────────────────

def test_the_naive_labels_derive_from_the_weights(monkeypatch):
    assert benchmarks.naive_60_40_label() == "60/40"
    assert benchmarks.naive_60_40_legs() == "60% SPY / 40% AGG"
    monkeypatch.setattr(benchmarks, "NAIVE_60_40", (("SPY", 0.7), ("AGG", 0.3)))
    assert benchmarks.naive_60_40_label() == "70/30"
    assert benchmarks.naive_60_40_legs(", ") == "70% SPY, 30% AGG"


def test_the_naive_series_is_built_from_the_same_weights(monkeypatch, tmp_path):
    """Other weights in the table, and the basket holds exactly them: its last value is
    each leg's growth at that weight. A leg left at a typed 0.6 beside the table's other
    leg would still move the series (the legs renormalize), so the value is what counts."""
    from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers
    start, end = "2025-07-01", "2025-09-30"
    pin_today(monkeypatch)
    try:
        point_at_frozen_book(monkeypatch, tmp_path)
        monkeypatch.setattr(benchmarks, "NAIVE_60_40", (("SPY", 0.2), ("AGG", 0.8)))
        got = float(benchmarks._naive_basket_series(start, end).dropna().iloc[-1])
        growth = {}
        for t in ("SPY", "AGG"):
            p = benchmarks._get_price_series(t, start, end, col="adj_close").ffill()
            growth[t] = float(p.iloc[-1]) / float(p.bfill().iloc[0])
    finally:
        unpin_leftovers()
    assert got == pytest.approx(0.2 * growth["SPY"] + 0.8 * growth["AGG"], rel=1e-9)


def test_stage_1s_caption_is_not_typed():
    src = (ROOT / "pages" / "2_Performance.py").read_text(encoding="utf-8")
    assert '"SAA blend vs. 60/40"' not in src and '"60/40 (60% SPY / 40% AGG)"' not in src
    assert 'caption(f"SAA blend vs. {_naive_short}")' in src
