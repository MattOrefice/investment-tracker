"""The Macro page's readings, as decided by the owner on October 15, 2026 (#424).

A sentence chosen by a computed figure covers zero and missing inputs: a figure that
prints as zero reads as zero, and a sentence missing an input does not render. A label
that grades a figure names the span it is graded on. The page as it renders is checked
in tests/render/test_macro_424_render.py.
"""
from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest

from src import factor_regime, macro

ROOT = Path(__file__).resolve().parent.parent


# ── the Treasury caption's series IDs ─────────────────────────────────────────

def test_fred_ids_carry_mo_only_for_month_tenors():
    tenors = ["3M", "1Y", "2Y", "5Y", "7Y", "10Y", "20Y", "30Y"]
    assert [macro.treasury_series_id(t) for t in tenors] == [
        "DGS3MO", "DGS1", "DGS2", "DGS5", "DGS7", "DGS10", "DGS20", "DGS30"]
    assert macro.treasury_series_id("6M") == "DGS6MO"
    for bad in ("10", "Y", "10D", ""):
        with pytest.raises(ValueError):
            macro.treasury_series_id(bad)


def test_the_page_fetches_the_ids_the_caption_names():
    """Each tenor the caption can name is one the page fetches by that ID."""
    from src.macro import MACRO_PAGE_SERIES
    fetched = {sid for sid, _ in MACRO_PAGE_SERIES}
    for tenor in ["3M", "1Y", "2Y", "5Y", "7Y", "10Y", "20Y", "30Y"]:
        assert macro.treasury_series_id(tenor) in fetched, tenor


# ── inflation: the 2% line, core CPI, the breakeven ───────────────────────────

def test_the_2_percent_line_says_whose_target_it_is():
    assert macro.INFLATION_TARGET_LINE == "2% (the Fed's target is set for PCE inflation)"


@pytest.mark.parametrize("cpi, reading", [
    (4.6, "Core CPI at 4.6% is well above 2%, an inflationary regime"),
    (3.1, "Core CPI at 3.1% is above 2%. Whether this reflects"),
    (2.0, "Core CPI at 2.0% is near 2%, a regime"),
    (1.2, "Core CPI at 1.2% is below 2%. Disinflationary conditions"),
])
def test_core_cpi_is_read_against_2_percent_not_the_feds_target(cpi, reading):
    text = macro.interpret_core_cpi(cpi)
    assert text.startswith(reading), text
    for gone in ("target", "disinflation is in progress", "preferred gauge"):
        assert gone not in text, gone


@pytest.mark.parametrize("cpi", [None, float("nan")])
def test_without_core_cpi_the_breakeven_makes_no_comparison(cpi):
    assert macro.breakeven_vs_cpi(2.3, cpi) is None


@pytest.mark.parametrize("cpi, framing", [
    (3.0, "pricing disinflation: it expects inflation to fall from current levels."),
    (2.4, "in line with current realized inflation"),
    (2.3, "in line with current realized inflation"),
    (1.2, "pricing persistence near or above current CPI levels."),
])
def test_the_breakeven_comparison_reads_both_figures(cpi, framing):
    text = macro.breakeven_vs_cpi(2.3, cpi)
    assert text.startswith(f"Compared to Core CPI at {cpi:.1f}%, the market is "), text
    assert framing in text and "target" not in text


# ── readings at zero ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [0.0, 0.003, -0.003])
def test_a_real_yield_that_prints_as_zero_is_at_zero(value):
    assert macro.interpret_real_10y(value) == "Real 10Y at 0.00% is at zero."


def test_real_yields_either_side_of_zero_keep_their_readings():
    assert "is modestly positive" in macro.interpret_real_10y(0.006)
    assert "is negative" in macro.interpret_real_10y(-0.006)
    assert "is well above zero" in macro.interpret_real_10y(2.85)


@pytest.mark.parametrize("bps", [0.0, 0.4, -0.4])
def test_a_curve_that_prints_as_0_bps_is_flat_at_zero(bps):
    text = macro.interpret_curve_spread(bps)
    assert text.startswith("The yield curve is flat at 0 bps: the 10Y−2Y spread is zero."), text
    for wrong in ("positive", "inverted", "-0 bps", "+0 bps", "re-inversion"):
        assert wrong not in text, wrong


def test_curves_either_side_of_zero_keep_their_readings():
    assert "positive but compressed" in macro.interpret_curve_spread(0.6)
    assert macro.interpret_curve_spread(-0.6).startswith("The yield curve is inverted at -1 bps")


@pytest.mark.parametrize("value", [0.0, 0.004, -0.004])
def test_cfnai_that_prints_as_zero_is_on_the_neutral_line(value):
    assert macro.cfnai_sentence(value) == "At 0.00, the index is at zero, the neutral line."


def test_cfnai_either_side_of_zero_keeps_its_reading():
    assert macro.cfnai_sentence(0.12).startswith("At +0.12, the index is above zero")
    assert macro.cfnai_sentence(-0.08).startswith("At -0.08, the index is below zero")


# ── GDP: "near trend" goes, and the rate stands alone ─────────────────────────

@pytest.mark.parametrize("value", [macro.GDP_TREND, 2.75, macro.GDP_ABOVE_TREND - 0.01])
def test_from_the_trend_to_above_trend_the_rate_stands_alone(value):
    assert macro.interpret_gdp_growth(value) == f"Real GDP growth is {value:.1f}%."


@pytest.mark.parametrize("value", [0.0, 0.04, -0.04])
def test_growth_that_prints_as_zero_is_flat(value):
    assert macro.interpret_gdp_growth(value) == "Real GDP growth is 0.0%: output was flat."


def test_outside_the_band_growth_is_still_set_against_the_cited_trend():
    for value in (1.2, macro.GDP_ABOVE_TREND + 0.5):
        assert f"~{macro.GDP_TREND:.1f}%" in macro.interpret_gdp_growth(value)
    assert "is negative" in macro.interpret_gdp_growth(-1.0)


# ── grades that name their span ───────────────────────────────────────────────

@pytest.mark.parametrize("series, pctile, grade", [
    ("IG", 10, "is tight against its history since Sep 2023, reflecting"),
    ("IG", 45, "is near the median of its history since Sep 2023, consistent"),
    ("IG", 80, "is elevated against its history since Sep 2023, reflecting"),
    ("CCC", 10, "is tight against its history since Sep 2023: broad risk-on"),
    ("CCC", 45, "is near the median of its history since Sep 2023, a moderate"),
    ("CCC", 80, "is elevated against its history since Sep 2023. CCC widening"),
])
def test_credit_grades_name_the_history_they_are_graded_on(series, pctile, grade):
    text = macro.credit_reading(series, 84.0, pctile, "Sep 2023")
    assert grade in text, text
    assert "historically" not in text and "available history" not in text
    assert text.endswith(".")


def test_an_elevated_ccc_is_set_against_hy_when_there_is_one():
    text = macro.credit_reading("CCC", 763.0, 80, "Sep 2023", 340.0)
    assert "elevated against its history since Sep 2023. The CCC/HY spread differential is 423 bps." in text


@pytest.mark.parametrize("pctile, grade", [
    (85, "strong for that window"), (59, "near that window's median"),
    (12, "weak for that window"),
])
def test_the_dollars_grade_names_its_window(pctile, grade):
    label, impl = macro.dollar_reading(pctile, "window")
    assert label == grade
    assert "historical" not in label + impl
    assert macro.dollar_reading(pctile, "series")[0] == grade.replace("window", "series")


# ── valuation paragraphs that read their figures ──────────────────────────────

def test_the_gap_above_the_average_carries_its_explanation():
    text = macro.earnings_gap_paragraph(26.3, 41.4, "read Oct 4, 2026")
    assert "earnings are running ≈57% above their inflation-adjusted ten-year average. CAPE smooths away" in text


def test_the_gap_below_the_average_says_below_and_explains_nothing():
    text = macro.earnings_gap_paragraph(30.0, 26.4, "read Oct 4, 2026")
    assert text.endswith("earnings are running ≈12% below their inflation-adjusted ten-year average.")
    assert "above" not in text and "≈-" not in text and "elevated-earnings" not in text


def test_a_gap_that_prints_as_zero_is_level():
    text = macro.earnings_gap_paragraph(30.0, 30.1, "read Oct 4, 2026")
    assert text.endswith("earnings are running level with their inflation-adjusted ten-year average.")


def test_three_lenses_in_their_usual_order():
    text = macro.three_lenses_paragraph(26.3, 22.1, 41.4, "read Oct 4, 2026")
    assert "Forward sits lowest" in text and "CAPE sits highest" in text
    assert text.endswith("rather than the earnings it has averaged.")


@pytest.mark.parametrize("ttm, fwd, cape, lowest, highest", [
    (20.0, 22.0, 30.0, False, True),     # forward above trailing
    (26.0, 22.0, 24.0, True, False),     # CAPE below trailing
    (22.0, 22.0, 30.0, False, True),     # forward ties trailing as printed
    (22.04, 22.0, 30.0, False, True),    # 22.04 prints as 22.0
])
def test_three_lenses_states_only_the_order_the_figures_have(ttm, fwd, cape, lowest, highest):
    text = macro.three_lenses_paragraph(ttm, fwd, cape, "read Oct 4, 2026")
    assert ("Forward sits lowest" in text) is lowest
    assert ("CAPE sits highest" in text) is highest
    assert "When the three disagree" not in text


# ── the factor proxy names its price date ─────────────────────────────────────

FF, PROXY = date(2026, 8, 31), date(2026, 9, 30)


def test_the_proxy_reading_names_its_date():
    text = factor_regime.interpret_factor(4.5, 66.0, 0.9, 40.0, "size",
                                          ff_as_of=FF, proxy_as_of=PROXY)
    assert "The long-only proxy (IWM−IWB) reads +0.9% as of September 30, 2026, " \
           "confirming the same direction." in text
    assert "current to today" not in text


def test_without_a_date_the_proxy_claims_no_currency():
    text = factor_regime.interpret_factor(3.0, 60.0, -1.0, 40.0, "style")
    assert "reads -1.0%, the opposite sign, so the two measures send a mixed signal" in text
    assert "more recent" not in text and "current to today" not in text


def test_the_proxy_is_more_recent_only_when_its_date_is_later():
    later = factor_regime.interpret_factor(3.0, 60.0, -1.0, 40.0, "style",
                                           ff_as_of=FF, proxy_as_of=PROXY)
    assert "so its more recent reading sends a mixed signal" in later
    assert "the proxy's more recent reading suggests that edge may be fading" in later
    earlier = factor_regime.interpret_factor(3.0, 60.0, -1.0, 40.0, "style",
                                             ff_as_of=FF, proxy_as_of=date(2026, 6, 8))
    assert "as of June 8, 2026" in earlier
    assert "more recent" not in earlier and "fading" not in earlier
    assert earlier.endswith("is being rewarded on the academic measure, but the long-only "
                            "proxy disagrees.")
    lag = factor_regime.interpret_factor(-3.0, 30.0, 1.0, 60.0, "size",
                                         ff_as_of=FF, proxy_as_of=date(2026, 6, 8))
    assert lag.endswith("has been a headwind on the academic measure, though the long-only "
                        "proxy disagrees.")


@pytest.mark.parametrize("ff", [0.0, 0.04, -0.04])
def test_a_premium_that_prints_as_zero_outperformed_nothing(ff):
    text = factor_regime.interpret_factor(ff, 50.0, 2.0, 60.0, "size",
                                          ff_as_of=FF, proxy_as_of=PROXY)
    assert text.startswith("Over the trailing year, small-caps have matched large-caps: "
                           "the Fama-French SMB premium is 0.0%, in the 50th percentile "
                           "of its history.")
    assert "outperformed" not in text and "lagged" not in text
    assert "confirming" not in text and "opposite" not in text
    assert text.endswith("has been neither rewarded nor a headwind on the academic measure "
                         "over the trailing year.")


def test_a_proxy_that_prints_as_zero_neither_confirms_nor_disagrees():
    text = factor_regime.interpret_factor(3.0, 60.0, 0.03, 50.0, "style",
                                          ff_as_of=FF, proxy_as_of=PROXY)
    assert "reads 0.0% as of September 30, 2026." in text
    assert "confirming" not in text and "opposite" not in text
    assert text.endswith("is being rewarded on the academic measure.")


# ── the sleeve phrase's fallback ──────────────────────────────────────────────

def _sized():
    """The page's _sized, compiled from its source, so the fallback can be exercised
    without rendering a page whose other panels read the same weights."""
    tree = ast.parse((ROOT / "pages" / "3_Macro.py").read_text(encoding="utf-8"))
    [fn] = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_sized"]
    ns: dict = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "3_Macro.py", "exec"), ns)
    return ns["_sized"]


def test_the_sleeve_phrase_names_its_target(monkeypatch):
    import src.sleeve_config as sc
    monkeypatch.setattr(sc, "strategic_sleeve_weights", lambda: {"TIPS": 0.041})
    assert _sized()("TIPS", "TIPS") == "4.1% TIPS"


def test_without_the_db_the_sleeve_phrase_is_the_noun_alone(monkeypatch):
    import src.sleeve_config as sc

    def fail():
        raise RuntimeError("no db")

    monkeypatch.setattr(sc, "strategic_sleeve_weights", fail)
    assert _sized()("TIPS", "TIPS") == "TIPS"
    monkeypatch.setattr(sc, "strategic_sleeve_weights", lambda: {})
    assert _sized()("US Small Cap", "small-cap") == "small-cap"
