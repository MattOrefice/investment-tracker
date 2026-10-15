"""The quarterly PDF's claims #426 listed, each checked against the code and the rendered
report (decided by the owner on October 15, 2026). This file holds the ones that held and
were fixed. Two others have their tests where their code is tested:
- the Drift Status rule: tests/test_prose_consistency.py;
- "Coverage checks incomplete": tests/test_reports.py.
"Equity-like correlation" did not hold: it already names the sample it prints (#478 I12).
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
import pytest

import src.asset_evaluation as ae
from src import reports
from tests.test_asset_eval_subanalysis_disclosure import _BASE, _Blank, _render

TEMPLATE = reports.TEMPLATES_DIR / "quarterly_report.html"
CURVE_WEIGHTS = [0.00, 0.01, 0.02, 0.03, 0.05, 0.07, 0.10]


def _template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


# ── the TWR sentence ──────────────────────────────────────────────────────────

def test_the_twr_sentence_says_what_twr_daily_linked_computes():
    """It said "Cash flows assumed at beginning of day" beside a formula that, like the
    code, nets the day's flow from the day's change over the bare prior value."""
    from src.returns import twr_daily_linked
    # 100 grows to 110 and a 50 deposit lands: end-of-day timing gives 10 / 100 = 10%,
    # beginning-of-day timing 10 / 150 = 6.7%.
    idx = pd.date_range("2026-01-02", periods=2, freq="D")
    moved = twr_daily_linked(pd.Series([100.0, 160.0], index=idx),
                             pd.Series([0.0, 50.0], index=idx))
    assert moved == pytest.approx(0.10), "premise: the flow earns nothing on its day"
    block = _template()[_template().index("<dt>Time-Weighted Return</dt>"):][:900]
    assert "Cash flows count at the end of the day" in block, block
    assert "beginning of day" not in block


# ── the Sharpe banner reads the curve it plots ────────────────────────────────

def _section_with_curve(monkeypatch, sharpe: "list[float] | None") -> dict:
    curve = (pd.DataFrame({"btc_alloc": CURVE_WEIGHTS, "sharpe": sharpe})
             if sharpe is not None else pd.DataFrame())
    empty = {"build_univariate_table": pd.DataFrame,
             "compute_full_sample_correlations": lambda: pd.Series(dtype=float),
             "compute_rolling_correlation": lambda: pd.Series(dtype=float),
             "compute_mv_analysis": dict,
             "compute_drawdown_sensitivity": pd.DataFrame}
    for name, make in empty.items():
        monkeypatch.setattr(ae, name, lambda *a, _m=make, **k: _m())
    monkeypatch.setattr(ae, "compute_marginal_sharpe_curve", lambda *a, **k: curve)
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    return reports._build_asset_eval_section()


def test_a_curve_that_peaks_below_ten_percent_names_its_best_weight(monkeypatch):
    """The case #426 reported: the curve rises to 3% and falls back below its 0% value by
    10%. Compared at 10% alone it printed "none found", that no weight improved."""
    out = _section_with_curve(monkeypatch, [0.50, 0.52, 0.55, 0.56, 0.53, 0.51, 0.49])
    assert out["unavailable"] == [], out["unavailable"]
    assert len(out["args_for"]) == 1, out["args_for"]
    assert out["args_for"][0].startswith(
        "Sharpe-improving against SAA target weights: the curve's highest Sharpe is at 3% "
        "allocation (0.500 at 0% → 0.560, +0.060 over 2018-01-01–present)"), out["args_for"]
    body = _render({**_BASE, **out})
    assert "none found" not in body and "not assessed" not in body


def test_a_curve_no_weight_of_which_beats_zero_says_none_found_over_its_range(monkeypatch):
    out = _section_with_curve(monkeypatch, [0.50, 0.49, 0.48, 0.47, 0.45, 0.43, 0.40])
    assert out["args_for"] == [] and out["sharpe_curve_top"] == "10%", out
    body = _render({**_BASE, **out})
    assert ("Arguments for inclusion: none found. The marginal-Sharpe curve was computed "
            "at BTC allocations from 0% to 10%, and no weight between 0% and 10% improved "
            "the portfolio's Sharpe ratio against SAA targets.") in body, body


def test_an_empty_curve_is_not_none_found(monkeypatch):
    """Nothing to read: not a failure (nothing is recorded), and not a finding either."""
    out = _section_with_curve(monkeypatch, None)
    assert out["unavailable"] == [] and out["args_for"] == [] and out["sharpe_curve_top"] is None
    body = _render({**_BASE, **out})
    assert "Arguments for inclusion: not assessed" in body and "none found" not in body


# ── "Full rationale in the SAA section" ───────────────────────────────────────

def test_the_methodology_points_to_no_saa_section():
    tpl = _template()
    headings = [_text(h) for h in re.findall(r"<h2[^>]*>(.*?)</h2>", tpl, re.S)]
    assert headings and not any("SAA" in h or "Strategic" in h for h in headings), (
        "premise: the PDF has no SAA section", headings)
    assert "SAA section" not in tpl


# ── the rolling-correlation prose ─────────────────────────────────────────────

@pytest.mark.parametrize("pre, post", [(0.05, 0.40), (0.30, -0.10)],
                         ids=["rose-after-2020", "fell-after-2020"])
def test_the_rolling_prose_states_the_averages_and_no_reading(monkeypatch, pre, post):
    """It called the pre-2020 average "consistent with the 'uncorrelated alternative'
    narrative" and the post-2020 one "persistently elevated", whatever the two were."""
    idx = pd.date_range("2018-01-01", "2025-12-31", freq="D")
    rolling = pd.Series(np.where(idx < pd.Timestamp("2020-01-01"), pre, post), index=idx)
    monkeypatch.setattr(ae, "compute_rolling_correlation", lambda *a, **k: rolling)
    for name, make in {"build_univariate_table": pd.DataFrame,
                       "compute_full_sample_correlations": lambda: pd.Series(dtype=float),
                       "compute_mv_analysis": dict,
                       "compute_marginal_sharpe_curve": pd.DataFrame,
                       "compute_drawdown_sensitivity": pd.DataFrame}.items():
        monkeypatch.setattr(ae, name, lambda *a, _m=make, **k: _m())
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    out = reports._build_asset_eval_section()
    assert out["rolling_prose"] == f"Pre-2020 average: {pre:.2f}. Post-2020 average: {post:.2f}."


# ── the no-trades banner ──────────────────────────────────────────────────────

def test_the_no_trades_banner_names_what_the_report_prints():
    """It listed "investment theses"; the template renders no thesis anywhere."""
    from jinja2 import Environment, FileSystemLoader
    tpl = _template()
    assert tpl.count("theses") == 0, "premise: the template prints no thesis"
    env = Environment(loader=FileSystemLoader(str(reports.TEMPLATES_DIR)), autoescape=True,
                      undefined=_Blank)
    text = _text(env.get_template("quarterly_report.html").render(has_trades=False,
                                                                  css_content=""))
    assert ("No trades have been logged. This report contains structural information "
            "only: SAA targets and macro context.") in text


# ── an alpha that rounds to 0 bps ─────────────────────────────────────────────

@pytest.mark.parametrize("alpha, phrase", [
    (0.3, "in line with the S&P 500"),
    (-0.4, "in line with the S&P 500"),
    (0.5, "in line with the S&P 500"),
    (0.6, "outperforming the S&P 500 by 1 bps"),
    (-12.2, "underperforming the S&P 500 by 12 bps"),
    (290.0, "outperforming the S&P 500 by 290 bps"),
])
def test_an_alpha_that_rounds_to_zero_reads_in_line(alpha, phrase):
    """It read "outperforming (or underperforming) ... by 0 bps". The cut is the figure
    the sentence would print, so 0.5 (which prints as 0) is in line and 0.6 is not."""
    assert reports._versus_benchmark(alpha, "the S&P 500") == phrase


def test_in_line_is_a_declared_versus_benchmark_class():
    from src.prose_helpers import LABEL_CLASSES
    assert LABEL_CLASSES["versus_benchmark"] == ("out", "under", "in_line")
