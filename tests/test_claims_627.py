"""#627, decided by the owner on October 16, 2026, page and PDF alike. Macro Context and
Asset Evaluation are built outside the quarter lock, so nothing here moves a locked figure.

  * The CAPE stances all go, as the elevated one did in #625: the regime keeps its label
    and draws no allocation conclusion, and the Macro Context paragraph keeps only its
    policy sentence. The unknown note ends at "so no valuation reading is drawn from it."
  * The conclusion's second trigger reads its range from the marginal-Sharpe curve
    ("An allocation of up to 10% improves ..."); with no curve it names no size. It typed
    "A 5–10% allocation".
  * The page's operational bullets and the Macro page's CAPE caption lose their uncited
    sentences (tests/render/test_asset_evaluation_604_render.py and
    tests/render/test_cape_read_dates_render.py read them off the rendered pages).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import src.asset_evaluation as ae
import src.reports as reports

TAIL = (" improves the portfolio's Sharpe ratio after explicit tax-drag adjustment (the "
        "analytical gap in the constrained mean-variance analysis).\n")


def _curve(allocs):
    return pd.DataFrame({"btc_alloc": allocs, "sharpe": np.linspace(1.0, 1.1, len(allocs))})


def test_the_curves_top_is_read_only_from_a_curve_with_its_zero_point():
    assert ae.curve_top_allocation(_curve([0.0, 0.01, 0.05, 0.10])) == "10%"
    assert ae.curve_top_allocation(_curve([0.0, 0.02, 0.15])) == "15%"
    assert ae.curve_top_allocation(_curve([0.01, 0.10])) is None, "no 0% point"
    assert ae.curve_top_allocation(_curve([0.0])) is None, "nothing above 0%"
    assert ae.curve_top_allocation(pd.DataFrame(columns=["btc_alloc", "sharpe"])) is None
    assert ae.curve_top_allocation(None) is None


def test_the_second_trigger_names_the_curves_range_or_no_size():
    with_curve = ae.conclusion(None, "10%")
    assert "\n- An allocation of up to 10%" + TAIL in with_curve
    without = ae.conclusion(None, None)
    assert "\n- A Bitcoin allocation" + TAIL in without
    for text in (with_curve, without):
        assert "5–10%" not in text and "positive Sharpe contribution" not in text
        assert text.count("\n- ") == 3


def test_the_pdfs_conclusion_reads_the_curve_the_section_read():
    """The builder reads the conclusion after the curve: its trigger's range is the
    section's own sharpe_curve_top. Checked in the source, since the end-to-end builds in
    tests/test_asset_evaluation_says_what_it_computes.py compare the two on real books."""
    import inspect
    src = inspect.getsource(reports._build_asset_eval_section)
    assert ('result["conclusion"] = ae.conclusion(_conclusion_rolling, '
            'result["sharpe_curve_top"])') in src
    assert src.index('result["sharpe_curve_top"] = ae.curve_top_allocation(msc)') < src.index(
        'result["conclusion"] = ae.conclusion(_conclusion_rolling')


def test_every_cape_band_keeps_its_label_and_draws_no_stance():
    assert reports._CAPE_REGIME_WORDS == {
        "elevated": "Elevated", "above_average": "Above-average",
        "moderate": "Moderate", "below_average": "Below-average"}
    assert reports._CAPE_UNKNOWN == "Uncertain"
    template = (reports.TEMPLATES_DIR / "quarterly_report.html").read_text(encoding="utf-8")
    assert "regime_action" not in template and "US equity valuations" not in template
    import inspect
    builder = inspect.getsource(reports._build_macro_section)
    for stance in ("support modest diversification", "no strong tilt signal",
                   "support increased US equity exposure", "suggest maintaining",
                   "regime_action"):
        assert stance not in builder, stance
