"""The risk-free rate as the pages and the PDF use it (audit item 15a).

Each check compares what a consumer USED with what src.risk_free derives for the same
dates, and what the caption SAYS with the same derivation, so neither side can drift
alone. Offline throughout: FRED is blocked, so the rate comes from the stored series,
the path a caption must date.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

import src.db as db
import src.macro as macro
import src.prices as prices
from src import risk_free

ROOT = Path(__file__).resolve().parent.parent.parent


def _offline(*_a, **_k):
    raise OSError("offline")


# ── Performance, on the frozen book ──────────────────────────────────────────

@pytest.fixture(scope="module")
def perf(frozen_book_module):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    import src.performance as performance
    calls = []
    real = performance.compute_risk_metrics

    def _record(pv, bl, rf_annual=0.045, window="SI", cashflows=None):
        calls.append((window, rf_annual, pv.index))
        return real(pv, bl, rf_annual=rf_annual, window=window, cashflows=cashflows)

    mp = pytest.MonkeyPatch()
    mp.setattr(performance, "compute_risk_metrics", _record)
    st.cache_data.clear()
    at = AppTest.from_file(str(ROOT / "pages" / "2_Performance.py"), default_timeout=600).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    yield at, calls
    st.cache_data.clear()
    mp.undo()


def _rate(window, index):
    from src.performance import window_bounds
    return risk_free.rate_over(*window_bounds(window, index), risk_free.bill_series())


def test_every_window_uses_its_own_average_bill_rate(perf):
    _, calls = perf
    assert {w for w, _, _ in calls} == {"SI", "1Y", "YTD", "3M", "1M"}
    for window, used, index in calls:
        assert used == pytest.approx(_rate(window, index).annual), window
    assert all(abs(used - 0.045) > 1e-4 for _, used, _ in calls), "not the old fixed 4.5%"


def test_the_caption_states_the_rate_the_window_used(perf):
    at, calls = perf
    si = next(index for w, _, index in calls if w == "SI")
    expected = f"Risk-free rate, Since Inception: {risk_free.describe(_rate('SI', si))}."
    caps = [str(c.value) for c in at.caption]
    assert expected in caps, [c for c in caps if "Risk-free" in c]
    assert "FRED was unavailable" in expected and "stored series, which ends" in expected


def test_a_window_past_the_stored_series_names_its_last_rate(perf):
    at, calls = perf
    one_m = next(index for w, _, index in calls if w == "1M")
    rate = _rate("1M", one_m)
    assert rate.start == rate.through, "premise: the stored series ends before the window"
    at.radio(key="risk_metrics_window").set_value("1 Month").run()
    caps = [str(c.value) for c in at.caption]
    assert f"Risk-free rate, 1 Month: {risk_free.describe(rate)}." in caps
    assert "the 3-month Treasury bill rate on " in risk_free.describe(rate)


def test_the_method_note_states_the_rule_not_a_fixed_rate(perf):
    at, _ = perf
    texts = " ".join(str(e.value) for e in list(at.caption) + list(at.markdown))
    assert "RF = 4.5%" not in texts
    assert ("Sharpe and Sortino use the window's average 3-month Treasury bill rate "
            "(FRED DGS3MO) as the risk-free rate.") in texts


# ── Asset Evaluation page and PDF Section 5, on the demo book ────────────────

@pytest.fixture(scope="module")
def demo_offline(tmp_path_factory):
    import src.config as config
    mp = pytest.MonkeyPatch()
    copy = tmp_path_factory.mktemp("rf") / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    mp.setattr(db, "DB_PATH", copy)
    mp.setattr(db, "_migrated_paths", set())
    mp.setattr(db, "_RUNTIME_CACHE", None)
    mp.setattr(prices._SESSION, "get", _offline)
    mp.setattr(macro, "fetch_fred_series", _offline)
    mp.setattr(config, "IS_DEMO", True)
    yield mp
    mp.undo()


def _sample_rate():
    import src.asset_evaluation as ae
    btc = ae.get_candidate_returns("BTC-USD", ae.SAMPLE_START)
    slv = ae.get_sleeve_returns(ae.SAMPLE_START)
    return ae.sample_risk_free(btc, slv)


@pytest.fixture
def seen(demo_offline):
    """Wrap every Sharpe-bearing call in src.asset_evaluation to record its rate, for
    one test only: a wrapper left in place would be wrapped again by the next test."""
    import src.asset_evaluation as ae
    mp = pytest.MonkeyPatch()
    seen = {}
    for name in ("build_univariate_table", "compute_mv_analysis",
                 "compute_marginal_sharpe_curve", "compute_drawdown_sensitivity"):
        real = getattr(ae, name)

        def _wrap(*a, _real=real, _name=name, **k):
            import inspect
            bound = inspect.signature(_real).bind(*a, **k)
            bound.apply_defaults()
            seen[_name] = bound.arguments["rf_annual"]
            return _real(*a, **k)

        mp.setattr(ae, name, _wrap)
    yield seen
    mp.undo()


def test_the_asset_evaluation_page_uses_and_states_the_samples_rate(seen):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    rate = _sample_rate()
    st.cache_data.clear()
    at = AppTest.from_file(str(ROOT / "pages" / "5_Asset_Evaluation.py"),
                           default_timeout=600).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert set(seen) == {"build_univariate_table", "compute_mv_analysis",
                         "compute_marginal_sharpe_curve", "compute_drawdown_sensitivity"}
    assert all(v == pytest.approx(rate.annual) for v in seen.values()), seen
    text = " ".join(str(e.value) for e in list(at.caption) + list(at.markdown))
    assert f"**Risk-free rate:** {risk_free.describe(rate)}, the sample's own window." in text
    assert "450 bps" not in text
    st.cache_data.clear()


def test_the_pdf_section_uses_and_states_the_samples_rate(seen):
    from src import reports
    rate = _sample_rate()
    section = reports._build_asset_eval_section()
    assert section["disposition"] != "failed", section.get("failure_reason")
    assert set(seen) == {"build_univariate_table", "compute_mv_analysis",
                         "compute_marginal_sharpe_curve", "compute_drawdown_sensitivity"}
    assert all(v == pytest.approx(rate.annual) for v in seen.values()), seen
    assert section["rf_note"] == f"Risk-free rate: {risk_free.describe(rate)}."
    template = (ROOT / "templates" / "quarterly_report.html").read_text(encoding="utf-8")
    assert "{{ asset_eval.rf_note }}" in template
