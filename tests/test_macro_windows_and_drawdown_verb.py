"""Two statements the report typed are read from what it computes (#529).

  * The Macro Context footnote said "Historical percentile computed over available data
    window. HY spread data available from May 2023 only due to FRED licensing
    restrictions", and the methodology "available May 2023+". Two of the four
    percentiles are cut at a date in the code, not ranked over the data available, and
    the HY series FRED serves starts later with each fetch. Both places now print one
    sentence built from the code's cut-off and the first date of each series read.
  * The Asset Evaluation section said a BTC allocation "deepened" the 2022 drawdown, in
    its second argument against inclusion and in the note under the drawdown table,
    whatever the table's two figures were. The verb is now those figures'.

Macro Context and Asset Evaluation are computed at each render, so neither is in the
locked-figures golden.
"""
from __future__ import annotations

import re
from datetime import date

import numpy as np
import pandas as pd
import pytest
from jinja2 import ChainableUndefined, Environment, FileSystemLoader

import src.asset_evaluation as ae
import src.reports as reports

FULL = ("Each percentile ranks a reading within its own series: CAPE from January 1871, the "
        "start of its record; the 10Y-2Y spread and the fed funds rate from January 1, 1990, "
        "where this report cuts their history; the HY spread from October 20, 2023, the first "
        "date in the series this report read.")


class _Blank(ChainableUndefined):
    def __iter__(self):
        return iter(())

    def __bool__(self):
        return False

    def __len__(self):
        return 0


def _printed(**context) -> str:
    env = Environment(loader=FileSystemLoader(str(reports.TEMPLATES_DIR)),
                      autoescape=True, undefined=_Blank)
    html = env.get_template("quarterly_report.html").render(css_content="", **context)
    import html as html_lib
    return html_lib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))).strip()


# ── the Macro Context windows ────────────────────────────────────────────────

def _series(start: str) -> pd.Series:
    index = pd.bdate_range(start, periods=400)
    return pd.Series(np.linspace(1.0, 2.0, len(index)), index=index)


def _stub(monkeypatch, *, hy_start: str = "2023-10-20", fail=(), cape: bool = True) -> dict:
    """The section's inputs, stubbed: each FRED series starts where it is asked from,
    except the HY series, which starts where FRED's window puts it. Returns what the
    section asked for."""
    asked: dict = {}

    def get_series(series_id, start):
        asked[series_id] = start
        if series_id in fail:
            raise RuntimeError(f"{series_id} unavailable")
        return _series(hy_start if series_id == "BAMLH0A0HYM2" else start)

    def cape_series():
        if not cape:
            raise FileNotFoundError("no CAPE file")
        return pd.Series([15.0, 20.0, 30.0],
                         index=pd.to_datetime(["1871-01-01", "1950-06-01", "2026-09-01"]))

    monkeypatch.setattr(reports, "get_series", get_series)
    monkeypatch.setattr(reports, "get_cape_series", cape_series)
    monkeypatch.setattr(reports, "current_cape", lambda: 30.0)
    return asked


def test_the_sentence_gives_each_readings_own_window(monkeypatch):
    asked = _stub(monkeypatch)
    section = reports._build_macro_section()
    assert section["windows"] == FULL
    assert asked == {"T10Y2Y": reports.MACRO_PERCENTILE_SINCE,
                     "DFF": reports.MACRO_PERCENTILE_SINCE,
                     "BAMLH0A0HYM2": reports._HY_ASKED_FROM}


def test_the_hy_spreads_first_date_is_read_from_the_series_it_used(monkeypatch):
    """FRED serves the series over a rolling window, so a later fetch starts later. The
    sentence follows the series, where it typed May 2023."""
    _stub(monkeypatch, hy_start="2024-03-11")
    assert ("the HY spread from March 11, 2024, the first date in the series this report "
            "read.") in reports._build_macro_section()["windows"]


def test_a_leading_gap_is_not_the_series_first_date(monkeypatch):
    """FRED returns a day with no observation as missing, and the percentile ranks the
    observations (macro.percentile drops the rest). So the window starts at the first
    observation, not at the first row."""
    _stub(monkeypatch)
    stubbed = reports.get_series

    def with_a_leading_gap(series_id, start):
        series = stubbed(series_id, start).copy()
        if series_id == "BAMLH0A0HYM2":
            series.iloc[:3] = np.nan       # October 20, 23 and 24, 2023
        return series

    monkeypatch.setattr(reports, "get_series", with_a_leading_gap)
    assert ("the HY spread from October 25, 2023, the first date in the series this report "
            "read.") in reports._build_macro_section()["windows"]
    assert reports._first_date(pd.Series([np.nan, np.nan])) is None, "no observation, no date"


def test_the_percentile_start_is_the_codes_cut_off(monkeypatch):
    """The contrast: move the constant, and both the request and the sentence move."""
    monkeypatch.setattr(reports, "MACRO_PERCENTILE_SINCE", "1995-06-01")
    asked = _stub(monkeypatch)
    section = reports._build_macro_section()
    assert asked["T10Y2Y"] == asked["DFF"] == "1995-06-01"
    assert ("the 10Y-2Y spread and the fed funds rate from June 1, 1995, where this report "
            "cuts their history") in section["windows"]
    assert "1990" not in section["windows"]


def test_a_reading_that_is_unavailable_is_left_out_of_the_sentence(monkeypatch):
    _stub(monkeypatch, fail={"BAMLH0A0HYM2", "DFF"})
    assert reports._build_macro_section()["windows"] == (
        "Each percentile ranks a reading within its own series: CAPE from January 1871, the "
        "start of its record; the 10Y-2Y spread from January 1, 1990, where this report cuts "
        "its history.")
    _stub(monkeypatch, fail={"BAMLH0A0HYM2", "DFF", "T10Y2Y"}, cape=False)
    assert reports._build_macro_section()["windows"] is None


def test_the_footnote_and_the_methodology_print_the_one_sentence(monkeypatch):
    _stub(monkeypatch)
    text = _printed(macro=reports._build_macro_section(), cape_credit="multpl.com")
    assert text.count(FULL) == 2
    assert f"and HY spread from FRED (St. Louis Fed). {FULL}" in text
    assert f"BAMLH0A0HYM2 = ICE BofA HY OAS. {FULL}" in text


def test_with_no_reading_neither_place_prints_a_window(monkeypatch):
    _stub(monkeypatch, fail={"BAMLH0A0HYM2", "DFF", "T10Y2Y"}, cape=False)
    text = _printed(macro=reports._build_macro_section(), cape_credit="multpl.com")
    assert "Each percentile ranks" not in text
    assert "BAMLH0A0HYM2 = ICE BofA HY OAS. SAA" in text


def test_no_window_is_typed_any_more():
    template = (reports.TEMPLATES_DIR / "quarterly_report.html").read_text(encoding="utf-8")
    source = (reports.TEMPLATES_DIR.parent / "src" / "reports.py").read_text(encoding="utf-8")
    for typed in ("May 2023", "available data window", "licensing restrictions"):
        assert typed not in template, typed
    # The two notes that typed a window, as the code spelled them. (A docstring there
    # quotes what the footnote used to say, so the bare phrases are not searched for.)
    for typed in ("relative to May 2023+ window (FRED restriction)",
                  "relative to post-1990 history", "\"2023-05-01\")", "\"1990-01-01\")"):
        assert typed not in source, typed
    assert source.count("\"1990-01-01\"") == 1 and source.count("\"2023-05-01\"") == 1, (
        "each start is one constant")


def test_the_first_date_helper_never_raises():
    assert reports._first_date(_series("2023-10-20")) == date(2023, 10, 20)
    assert reports._first_date(pd.Series([1.0, 2.0])) is None, "an integer index has no date"
    assert reports._first_date(pd.Series(dtype=float)) is None
    assert reports._first_date(None) is None


# ── the drawdown verb ────────────────────────────────────────────────────────

PORT = {"max_dd": -0.313, "alloc_top": "10.0%", "mdd22_lo": -0.233, "mdd22_hi": -0.261}


@pytest.mark.parametrize("without, with_btc, verb", [
    (-0.233, -0.261, "deepened"), (-0.233, -0.201, "cushioned"),
    (-0.233, -0.233, "left unchanged")])
def test_the_verb_is_the_two_figures(without, with_btc, verb):
    assert ae.drawdown_verb(without, with_btc) == verb


def test_the_argument_says_deepened_only_when_the_drawdown_is_deeper():
    deeper = ae.drawdown_argument(-0.815, "-66.9%", PORT, "2018-01-01")
    assert deeper.endswith(
        "It deepened the portfolio's own 2022 drawdown from -23.3% at 0% allocation to "
        "-26.1% at 10.0%, with no diversification benefit when it was most needed")
    shallower = ae.drawdown_argument(-0.815, "-66.9%", dict(PORT, mdd22_hi=-0.201),
                                     "2018-01-01")
    assert shallower.endswith(
        "It cushioned the portfolio's own 2022 drawdown from -23.3% at 0% allocation to "
        "-20.1% at 10.0%, so on this measure it did not deepen the year's loss")
    assert "deepened" not in shallower and "no diversification benefit" not in shallower
    same = ae.drawdown_argument(-0.815, "-66.9%", dict(PORT, mdd22_hi=-0.233), "2018-01-01")
    assert same.endswith("It left the portfolio's own 2022 drawdown unchanged at -23.3%, "
                         "from 0% allocation to 10.0%")
    for text in (deeper, shallower, same):
        assert text.startswith("Bitcoin's own maximum drawdown is -81.5% over 2018-01-01")


def test_the_reduced_argument_no_longer_presumes_a_direction():
    reduced = ae.drawdown_argument(-0.815, "-66.9%", {}, "2018-01-01")
    assert "(what a BTC allocation did to the 2022 drawdown) is not available this run" in reduced
    assert "deepened" not in reduced and reduced.endswith("Absent, not zero")


def test_the_note_under_the_table_follows_the_same_two_figures():
    assert ae.drawdown_2022_note("-66.9%", PORT) == (
        "That year equities and bonds fell together while Bitcoin fell -66.9%, so adding BTC "
        "deepened the portfolio's 2022 drawdown, from -23.3% at 0% allocation to -26.1% at "
        "10.0%, instead of cushioning it. 2022 is the critical test: diversification value "
        "eroded in the year it was most needed.")
    assert ae.drawdown_2022_note("-66.9%", dict(PORT, mdd22_hi=-0.201)) == (
        "That year equities and bonds fell together while Bitcoin fell -66.9%, and adding BTC "
        "cushioned the portfolio's 2022 drawdown, from -23.3% at 0% allocation to -20.1% at "
        "10.0%. 2022 is the critical test, and on these figures the allocation did not deepen "
        "it.")
    assert ae.drawdown_2022_note("-66.9%", dict(PORT, mdd22_hi=-0.233)) == (
        "That year equities and bonds fell together while Bitcoin fell -66.9%, and adding BTC "
        "left the portfolio's 2022 drawdown unchanged at -23.3%. 2022 is the critical test.")
    assert ae.drawdown_2022_note(None, {}) == (
        "That year equities and bonds fell together. What a BTC allocation did to the "
        "portfolio's 2022 drawdown is not available this run.")


def test_the_template_prints_the_note_and_types_no_verb():
    template = (reports.TEMPLATES_DIR / "quarterly_report.html").read_text(encoding="utf-8")
    body = re.sub(r"\{#.*?#\}", "", template, flags=re.S)
    assert "{{ asset_eval.dd_2022_note }}" in body
    for typed in ("deepened", "cushioning", "cushioned"):
        assert typed not in body, typed
    note = ae.drawdown_2022_note("-66.9%", dict(PORT, mdd22_hi=-0.201))
    text = _printed(asset_eval={
        "disposition": "computed", "unavailable": [], "sample_start": "2018-01-01",
        "dd_rows": [{"alloc": "0%", "cagr": "1%", "max_dd": "-31.3%", "sharpe": "0.50",
                     "mdd22": "-23.3%"}],
        "dd_2022_note": note, "btc_2022_mdd": "-66.9%"})
    assert f"from the same daily series as the rest of the table. {note}" in text


def _dd_section(tmp_path, monkeypatch) -> dict:
    """The section as the report builds it on a copy of the committed demo book,
    offline. Its bitcoin history starts in 2018, so the table holds 2022 figures."""
    import os
    import shutil
    import socket
    from pathlib import Path
    import src.db as db
    import src.macro as macro
    import src.prices as prices

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
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    section = reports._build_asset_eval_section()
    assert section["dd_rows"], "premise: the drawdown table is built"
    return section


def test_on_the_demo_book_the_verb_agrees_with_the_table_it_sits_under(
        tmp_path, monkeypatch):
    """The section as the report builds it: the verb in the note and in the argument
    is the one the table's first and last 2022 figures give."""
    section = _dd_section(tmp_path, monkeypatch)
    lo, hi = (float(section["dd_rows"][i]["mdd22"].rstrip("%")) for i in (0, -1))
    verb = "deepened" if hi < lo else "cushioned" if hi > lo else "left"
    assert verb == "deepened", "premise: on this book the allocation deepened 2022"
    assert f"adding BTC {verb} the portfolio's 2022 drawdown, from {lo:.1f}% at 0% " in (
        section["dd_2022_note"])
    argument = next(a for a in section["args_against"] if a.startswith("Bitcoin's own"))
    assert f"It {verb} the portfolio's own 2022 drawdown from {lo:.1f}% at 0% allocation to " \
           f"{hi:.1f}% at " in argument


def test_with_the_tables_figures_reversed_the_section_says_cushioned(tmp_path, monkeypatch):
    """The contrast, through the real builder: the drawdown sweep's 2022 column made
    shallower with each step of allocation, and both texts say so."""
    sweep = ae.compute_drawdown_sensitivity

    def shallower(*args, **kwargs):
        frame = sweep(*args, **kwargs).copy()
        frame["2022 MDD"] = np.linspace(-0.25, -0.15, len(frame))
        return frame

    monkeypatch.setattr(ae, "compute_drawdown_sensitivity", shallower)
    section = _dd_section(tmp_path, monkeypatch)
    assert "adding BTC cushioned the portfolio's 2022 drawdown, from -25.0% at 0% " in (
        section["dd_2022_note"])
    argument = next(a for a in section["args_against"] if a.startswith("Bitcoin's own"))
    assert "It cushioned the portfolio's own 2022 drawdown from -25.0% at 0% allocation to " \
           "-15.0% at " in argument
    assert "deepened" not in section["dd_2022_note"] and "deepened" not in argument


def test_a_book_with_no_2022_closes_states_no_direction(tmp_path, monkeypatch):
    """The third state, through the real builder. The frozen book's prices start in 2024,
    so its table has no 2022 figure: neither text says what the allocation did."""
    from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    pin_today(monkeypatch)
    point_at_frozen_book(monkeypatch, tmp_path)
    try:
        section = reports._build_asset_eval_section()
    finally:
        unpin_leftovers()
    assert section["dd_rows"] and {row["mdd22"] for row in section["dd_rows"]} == {"\u2014"}
    assert section["dd_2022_note"] == (
        "That year equities and bonds fell together. What a BTC allocation did to the "
        "portfolio's 2022 drawdown is not available this run.")
    argument = next(a for a in section["args_against"] if a.startswith("Bitcoin's own"))
    assert "is not available this run" in argument
    for text in (section["dd_2022_note"], argument):
        assert "deepened" not in text and "cushioned" not in text
