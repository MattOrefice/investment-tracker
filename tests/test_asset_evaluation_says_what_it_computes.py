"""Asset Evaluation says what it computes (#478 I09 to I13).

Four statements in the section did not describe the code beside them:

  * the risk-free rate never named its series, on the page or in the PDF (I09, I10);
  * the conclusion's first trigger was a rolling 5-year correlation "below 0.20 (the
    pre-2020 baseline ...)", where the section computes a 60-day one whose pre-2020
    average is about zero, and its verdict said "currently" with no date (I11);
  * a full-sample correlation was labelled "post-2020" (I12);
  * the univariate table's Real Assets row was 50% VNQ + 50% PDBC, while the only
    definition given is the sleeve's benchmark, 60% VNQ + 40% DBC (I13).

On copies of the frozen book, offline, today pinned to its next day.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date

import numpy as np
import pandas as pd
import pytest

from src import asset_evaluation as ae
from src import risk_free
from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers


@pytest.fixture
def book(tmp_path, monkeypatch):
    pin_today(monkeypatch)
    path = point_at_frozen_book(monkeypatch, tmp_path)
    yield path
    unpin_leftovers()


# ── the risk-free rate names its series and its window ────────────────────────

def _rate(start, through):
    return risk_free.RiskFreeRate(0.0272, risk_free.FRED, start, through, through)


def test_the_rate_can_name_the_series_it_is_read_from():
    averaged = _rate(date(2018, 1, 2), date(2026, 7, 16))
    assert risk_free.describe(averaged, name_series=True) == (
        "2.72%, the average of FRED's DGS3MO, the 3-month Treasury bill rate, from "
        "January 2, 2018 to July 16, 2026")
    one_day = _rate(date(2026, 7, 16), date(2026, 7, 16))
    assert risk_free.describe(one_day, name_series=True) == (
        "2.72%, FRED's DGS3MO, the 3-month Treasury bill rate, on July 16, 2026")


def test_the_stored_series_clause_follows_the_named_form_too():
    stored = risk_free.RiskFreeRate(0.027, risk_free.STORED, date(2018, 1, 2),
                                    date(2026, 5, 26), date(2026, 5, 26))
    assert risk_free.describe(stored, name_series=True) == (
        "2.70%, the average of FRED's DGS3MO, the 3-month Treasury bill rate, from "
        "January 2, 2018 to May 26, 2026. FRED was unavailable, so the rate comes from the "
        "stored series, which ends May 26, 2026")


def test_the_window_is_named_beside_its_dates_and_before_the_stored_clause():
    """The stored series has a date of its own. "...which ends May 26, 2026, the sample's
    own window" put the window on the wrong date."""
    stored = risk_free.RiskFreeRate(0.027, risk_free.STORED, date(2018, 1, 2),
                                    date(2026, 5, 26), date(2026, 5, 26))
    assert risk_free.describe(stored, name_series=True, window="the sample's own window") == (
        "2.70%, the average of FRED's DGS3MO, the 3-month Treasury bill rate, from "
        "January 2, 2018 to May 26, 2026, the sample's own window. FRED was unavailable, so "
        "the rate comes from the stored series, which ends May 26, 2026")
    live = _rate(date(2018, 1, 2), date(2026, 7, 16))
    assert risk_free.describe(live, name_series=True, window="the sample's own window").endswith(
        "to July 16, 2026, the sample's own window")


def test_every_other_caption_keeps_the_wording_it_had():
    """The Performance page's risk-free caption calls describe() plainly. Its text does
    not move with this change."""
    averaged = _rate(date(2018, 1, 2), date(2026, 7, 16))
    assert risk_free.describe(averaged) == (
        "2.72%, the average 3-month Treasury bill rate from January 2, 2018 to "
        "July 16, 2026")


# ── the conclusion reads the section's own figures, with their date ───────────

def _rolling() -> pd.Series:
    return pd.Series([0.02, -0.04, 0.35, 0.43],
                     index=pd.to_datetime(["2019-03-01", "2019-12-31", "2020-01-02",
                                           "2026-07-16"]))


def test_the_trigger_gives_the_two_averages_the_section_prints_and_their_date():
    """MIGRATED at #425: the trigger names the figure that would move, the average from
    2020 on falling to the pre-2020 one, as the owner decided on October 15, 2026."""
    pre, post, through = ae.pre_post_2020(_rolling())
    assert (round(pre, 2), round(post, 2), through) == (-0.01, 0.39, pd.Timestamp("2026-07-16"))
    text = ae.conclusion(_rolling())
    assert ("- The rolling 60-day correlation with SPY, on average from 2020 on, falls "
            "to its pre-2020 average: it averaged -0.01 before 2020 and 0.39 from 2020 "
            "through July 16, 2026. That would indicate a return to diversifying "
            "behavior.\n") in text


def test_the_verdict_is_dated_and_no_longer_says_currently():
    text = ae.conclusion(_rolling())
    assert "**As of July 16, 2026, Bitcoin does not belong in this portfolio's SAA.**" in text
    assert "currently" not in text


def test_the_typed_threshold_and_its_baseline_are_gone():
    """0.20 was called the pre-2020 baseline of a rolling correlation whose pre-2020
    average the section prints as about zero, and no 5-year correlation is computed."""
    for text in (ae.conclusion(_rolling()), ae.conclusion()):
        for gone in ("0.20", "5-year", "pre-2020 baseline", "currently"):
            assert gone not in text, gone


def test_without_the_rolling_series_it_gives_no_figure_and_no_date():
    text = ae.conclusion()
    assert "**Bitcoin does not belong in this portfolio's SAA.**" in text
    assert ("- The rolling 60-day correlation with SPY, on average from 2020 on, falls "
            "to its pre-2020 average. That would indicate a return to diversifying "
            "behavior.\n") in text
    assert "As of" not in text and "averaged" not in text
    assert ae.conclusion(pd.Series(dtype=float)) == text


def test_the_other_two_triggers_and_the_opening_are_as_they_were():
    text = ae.conclusion(_rolling())
    assert text.startswith("Bitcoin's sample-period Sharpe improvement is real but almost "
                           "entirely attributable to its exceptional 2020–2021 bull market")
    assert text.count("\n- ") == 3
    assert "- A 5–10% allocation produces a positive Sharpe contribution after" in text
    assert text.rstrip().endswith("analysis is the relevant counter-example).")


def _section_on(monkeypatch):
    from src import reports
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    section = reports._build_asset_eval_section()
    assert section["disposition"] != "failed", section.get("failure_reason")
    rolling = ae.compute_rolling_correlation(
        ae.get_candidate_returns("BTC-USD", ae.SAMPLE_START),
        ae.get_candidate_returns("SPY", ae.SAMPLE_START), window=60)
    return section, rolling


def test_on_the_frozen_book_the_conclusion_cites_only_the_average_the_section_prints(
        book, monkeypatch):
    """The frozen book's bitcoin history starts in 2020, so its section prints a
    post-2020 average and no pre-2020 one. The conclusion says so and cites that figure,
    with the date the rolling series ends on."""
    from src.asof import format_long_date
    section, rolling = _section_on(monkeypatch)
    printed = re.search(r"Post-2020 average: (-?\d\.\d\d)\.", section["rolling_prose"])
    assert printed and "Pre-2020 average" not in section["rolling_prose"]
    through = format_long_date(rolling.index.max().date())
    assert (f"its pre-2020 average: this sample holds none before 2020, and it has averaged "
            f"{printed.group(1)} from 2020 through {through}. That would indicate"
            ) in section["conclusion"]
    assert f"**As of {through}, Bitcoin does not belong" in section["conclusion"]
    assert section["conclusion"] == ae.conclusion(rolling)


def test_on_the_demo_book_the_conclusion_cites_both_averages_the_section_prints(
        tmp_path, monkeypatch):
    """A copy of the committed demo book, whose bitcoin history starts in 2018: the
    section prints both averages, and the conclusion's two figures are those two."""
    import os
    import shutil
    import socket
    from pathlib import Path
    import src.db as db
    import src.macro as macro
    import src.prices as prices
    from src.asof import format_long_date

    def _offline(*_a, **_k):
        raise OSError("network blocked")

    copy = tmp_path / "demo.db"
    shutil.copyfile(Path(__file__).resolve().parent.parent / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(socket, "getaddrinfo", _offline)
    monkeypatch.setattr(prices._SESSION, "get", _offline)
    monkeypatch.setattr(macro, "fetch_fred_series", _offline)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    prices._reset_trailing_memo()
    section, rolling = _section_on(monkeypatch)
    printed = re.search(r"Pre-2020 average: (-?\d\.\d\d)\. Post-2020 average: (-?\d\.\d\d)\.",
                        section["rolling_prose"])
    cited = re.search(r"it averaged (-?\d\.\d\d) before 2020 and (-?\d\.\d\d) from 2020 "
                      r"through ([A-Z][a-z]+ \d+, \d{4})\. That would indicate",
                      section["conclusion"])
    assert printed and cited, (section["rolling_prose"], section["conclusion"][-420:])
    assert cited.groups()[:2] == printed.groups()
    assert cited.group(3) == format_long_date(rolling.index.max().date())
    assert section["conclusion"] == ae.conclusion(rolling)


def test_with_no_average_before_2020_the_trigger_says_so():
    late = _rolling().loc["2020-01-01":]
    text = ae.conclusion(late)
    assert ("- The rolling 60-day correlation with SPY, on average from 2020 on, falls "
            "to its pre-2020 average: this sample holds none before 2020, and it has "
            "averaged 0.39 from 2020 through July 16, 2026. That would indicate a return "
            "to diversifying behavior.\n") in text
    assert "**As of July 16, 2026, Bitcoin does not belong" in text


# ── a full-sample figure is labelled as one ───────────────────────────────────

def test_the_equity_like_bullet_says_which_sample_its_figure_is_from():
    assert ae.equity_like_argument(0.31) == (
        "Equity-like correlation with US Large Core (0.31 over the full sample, 2018 to "
        "present): co-movement spikes during stress, when a hedge is most valuable")
    assert ae.SAMPLE_START[:4] == "2018"
    assert "post-2020" not in ae.equity_like_argument(0.31)


def test_the_bullet_appears_only_above_its_threshold():
    assert ae.EQUITY_LIKE_ABOVE == 0.3
    assert ae.equity_like_argument(0.3) is None
    assert ae.equity_like_argument(0.3001) is not None
    assert ae.equity_like_argument(float("nan")) is None


def test_the_pdfs_bullet_carries_the_full_sample_correlation_it_tabulates(book, monkeypatch):
    """The figure in the bullet is the correlation table's US Large Core entry, the
    full-sample one, and no post-2020 label sits on it."""
    from src import reports
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    section = reports._build_asset_eval_section()
    corr = ae.compute_full_sample_correlations(
        ae.get_candidate_returns("BTC-USD", ae.SAMPLE_START),
        ae.get_sleeve_returns(ae.SAMPLE_START))
    expected = ae.equity_like_argument(float(corr["US Large Core"]))
    bullets = [a for a in section["args_against"] if "US Large Core" in a]
    assert expected is not None, "premise: the correlation is above the threshold on this book"
    assert bullets == [expected]
    assert not [a for a in section["args_against"] if "post-2020" in a]


# ── the Real Assets row is the sleeve's benchmark ─────────────────────────────

def _returns(ticker: str) -> pd.Series:
    return ae._price_series(ticker, ae.SAMPLE_START, date.today().isoformat())


def test_the_basket_is_read_from_the_sleeves_benchmark_spec(book):
    assert ae.real_assets_basket() == [("VNQ", 0.6), ("DBC", 0.4)]
    assert ae.sleeve_benchmarks()[ae.REAL_ASSETS] == ae.real_assets_basket()


def test_the_real_assets_row_is_sixty_forty_vnq_and_dbc(book):
    """Recomputed here from the two legs' own series, and set against the 50% VNQ + 50%
    PDBC the row used to be: the two differ, so the row cannot be both."""
    table = ae.build_univariate_table(rf_annual=0.03)
    vnq, dbc, pdbc = _returns("VNQ"), _returns("DBC"), _returns("PDBC")
    common = vnq.index.intersection(dbc.index)
    spec = (vnq.reindex(common).pct_change() * 0.6
            + dbc.reindex(common).pct_change() * 0.4).dropna()
    expected = ae.compute_univariate_stats(spec, 0.03)
    row = table.loc["Real Assets"]
    for key, value in expected.items():
        assert row[key] == pytest.approx(value, rel=1e-12, abs=1e-12), key
    old_common = vnq.index.intersection(pdbc.index)
    old = ae.compute_univariate_stats(
        (vnq.reindex(old_common).pct_change() * 0.5
         + pdbc.reindex(old_common).pct_change() * 0.5).dropna(), 0.03)
    moved = [k for k in expected if not np.isclose(expected[k], old[k], rtol=1e-9, atol=1e-12)]
    assert moved, "premise: the spec's basket and the old one give different figures"


def test_the_row_follows_the_spec_when_the_spec_changes(book):
    """Read from the book, not typed: with the sleeve's benchmark set to 50/50, the row
    is the 50/50 basket of the same two legs."""
    before = ae.build_univariate_table(rf_annual=0.03).loc["Real Assets"]
    con = sqlite3.connect(book)
    with con:
        assert con.execute("UPDATE asset_classes SET benchmark_ticker = 'VNQ (50%) + DBC (50%)' "
                           "WHERE benchmark_ticker = 'VNQ (60%) + DBC (40%)'").rowcount == 1
    con.close()
    assert ae.real_assets_basket() == [("VNQ", 0.5), ("DBC", 0.5)]
    after = ae.build_univariate_table(rf_annual=0.03).loc["Real Assets"]
    assert not np.isclose(before["sharpe"], after["sharpe"], rtol=1e-9)


def test_a_book_with_no_real_assets_sleeve_has_no_such_row_and_does_not_raise(book, monkeypatch):
    monkeypatch.setattr(ae, "sleeve_benchmarks", lambda: {"US Large Core": [("SPY", 1.0)]})
    assert ae.real_assets_basket() == []
    table = ae.build_univariate_table(rf_annual=0.03)
    assert "Real Assets" not in table.index and "BTC" in table.index


def test_no_basket_weight_or_ticker_is_typed_into_the_table_builder():
    """Read from the function's source: the row's legs come from real_assets_basket()
    and neither the old pair nor a weight sits in it."""
    import ast
    import inspect
    function = ast.parse(inspect.getsource(ae.build_univariate_table)).body[0]
    assert ast.get_docstring(function), "premise: the first statement is the docstring"
    code = " ; ".join(ast.unparse(statement) for statement in function.body[1:])
    assert "real_assets_basket()" in code
    for typed in ("PDBC", "'VNQ'", "'DBC'", "0.5", "0.6", "0.4"):
        assert typed not in code, typed
