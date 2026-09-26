"""Macro accuracy (2026-09-25 audit, item 5), rendered on synthetic FRED data.

The page said "Late-cycle — the yield curve is inverted or labor markets are
historically tight" beside a +0.31% curve and 4.1% unemployment its own panel called
mid-cycle; "the 6% TIPS sleeve" with TIPS at 4.1%; "As of Apr 2026" for Q2 GDP; rate
volatility "0.79%" for 79 bp; "~2.5% (CBO estimate)"; an ETF-proxy percentile of "full
history" ranked against a few months; "outperformed ... by 0.0%"; credit percentiles "of
full history" on a series that starts in September 2023; two different real 10Y
yields; and "NBER Recession: None". Each is checked here as a visitor reads it.

FRED is stubbed at src.macro.get_series with deterministic series built around the
audit's own values, so the render is offline and repeatable.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import src.config as config
import src.db as db
import src.prices as prices

ROOT = Path(__file__).resolve().parent.parent.parent
END = pd.Timestamp("2026-09-24")


def _series(series_id: str, start: str) -> pd.Series:
    rng = np.random.default_rng(abs(hash(series_id)) % (2 ** 32))
    monthly = pd.date_range("1948-01-01", "2026-08-01", freq="MS")
    daily = pd.bdate_range("1976-06-01", END)
    if series_id == "USREC":
        s = pd.Series(0.0, index=monthly)
    elif series_id == "UNRATE":
        s = pd.Series(np.clip(5.7 + np.cumsum(rng.normal(0, 0.15, len(monthly))), 3.4, 10.5),
                      index=monthly)
        s.iloc[-1] = 4.1
    elif series_id == "A191RL1Q225SBEA":
        q = pd.date_range("1947-01-01", "2026-04-01", freq="QS")
        s = pd.Series(rng.normal(2.8, 3.0, len(q)), index=q)
        s.iloc[-1] = 3.0
    elif series_id in ("BAMLH0A0HYM2", "BAMLC0A0CM", "BAMLH0A3HYC"):
        idx = pd.bdate_range("2023-09-01", END)
        base = {"BAMLH0A0HYM2": 3.2, "BAMLC0A0CM": 0.95, "BAMLH0A3HYC": 7.5}[series_id]
        s = pd.Series(base + rng.normal(0, 0.1, len(idx)).cumsum() * 0.05, index=idx)
    elif series_id in ("CPILFESL",):
        s = pd.Series(np.linspace(20, 330, len(monthly)), index=monthly)
    elif series_id in ("CFNAIDIFF",):
        s = pd.Series(rng.normal(0, 0.2, len(monthly)), index=monthly)
    elif series_id == "NFCI":
        w = pd.date_range("1971-01-08", END, freq="W-FRI")
        s = pd.Series(rng.normal(-0.4, 0.3, len(w)), index=w)
    else:
        level = {"T10Y2Y": 0.9, "DFF": 3.88, "DGS10": 4.2, "DGS3MO": 4.24, "DGS2": 3.9,
                 "DGS1": 4.0, "DGS5": 4.0, "DGS7": 4.1, "DGS20": 4.6, "DGS30": 4.7,
                 "T10YIE": 2.3, "DFII10": 2.85, "DTWEXBGS": 120.0}.get(series_id, 1.0)
        s = pd.Series(level + rng.normal(0, 0.05, len(daily)).cumsum() * 0.2, index=daily)
        if series_id == "T10Y2Y":
            s.iloc[-1] = 0.31
        if series_id == "DFII10":
            s.iloc[-1] = 2.85
    return s[s.index >= pd.Timestamp(start)]


@pytest.fixture(scope="module")
def macro_page(tmp_path_factory):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    import src.macro as macro
    mp = pytest.MonkeyPatch()
    tmp = tmp_path_factory.mktemp("macro")
    copy = tmp / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    mp.setattr(db, "DB_PATH", copy)
    mp.setattr(db, "_migrated_paths", set())
    mp.setattr(db, "_RUNTIME_CACHE", None)
    mp.setattr(prices._SESSION, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    mp.setattr(macro, "get_series", _series)
    mp.setattr(config, "IS_DEMO", True)
    st.cache_data.clear()
    at = AppTest.from_file(str(ROOT / "pages" / "3_Macro.py"), default_timeout=600).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    texts = [str(e.value) for e in list(at.caption) + list(at.markdown) + list(at.info)]
    yield at, texts
    st.cache_data.clear()
    mp.undo()


def _any(texts, needle):
    return any(needle in t for t in texts)


def test_the_regime_verdict_cites_what_fired(macro_page):
    at, texts = macro_page
    assert _any(texts, "Late-cycle: unemployment at 4.1% is below the 4.2% tight-labor "
                       "threshold. Not triggered: the 2/10 curve at +0.31% is above the "
                       "-0.25% inversion trigger.")
    assert not _any(texts, "The yield curve is inverted or labor markets are historically tight")


def test_the_unemployment_panel_speaks_on_the_classifiers_basis(macro_page):
    _, texts = macro_page
    assert _any(texts, "below the regime classifier's 4.2% tight-labor threshold, a late-cycle signal")
    assert not _any(texts, "mid-cycle labor market")


def test_no_recession_reads_as_no_recession(macro_page):
    at, _ = macro_page
    usrec = next(m for m in at.metric if m.label == "NBER Recession (USREC)")
    assert usrec.value == "No recession"


def test_the_tips_sleeve_weight_is_the_saas(macro_page):
    _, texts = macro_page
    from src.sleeve_config import strategic_sleeve_weights
    tips = f"{strategic_sleeve_weights()['TIPS'] * 100:.1f}%"
    assert tips == "4.1%", "premise: the demo's TIPS target"
    assert _any(texts, f"directly relevant to the {tips} TIPS sleeve")
    assert not _any(texts, "the 6% TIPS sleeve")


def test_gdp_is_labelled_by_its_quarter(macro_page):
    _, texts = macro_page
    assert _any(texts, "As of Q2 2026 (quarterly release")
    assert not _any(texts, "As of Apr 2026")


def test_rate_volatility_is_in_basis_points(macro_page):
    at, texts = macro_page
    rv = next(m for m in at.metric if m.label == "Rate Volatility (10Y Realized)")
    assert str(rv.value).endswith(" bp") and "%" not in str(rv.value)
    assert _any(texts, "Rate volatility at ") and not _any(texts, "% (annualized) is")


def test_the_gdp_trend_cites_its_source_not_cbo(macro_page):
    _, texts = macro_page
    assert _any(texts, "the FOMC's longer-run median projection, September 2026")
    assert not _any(texts, "CBO estimate")     # not "CBO": the rate-vol caption names CBOE


def test_the_header_states_credits_base(macro_page):
    _, texts = macro_page
    assert _any(texts, "Credit percentiles use the history FRED provides, which begins "
                       "September 2023.")
    head = next(t for t in texts if t.startswith("Last updated:"))
    assert "(CAPE/ECY full leg), credit," not in head


def test_one_real_10y_across_the_page(macro_page):
    _, texts = macro_page
    assert _any(texts, "(DFII10, the 10-year TIPS yield, as on the Real 10-Year panel)")
    assert _any(texts, "vs real rate 2.85%")


def test_cross_asset_says_allocations(macro_page):
    _, texts = macro_page
    assert _any(texts, "emerging-markets allocations") and not _any(texts, "overweights")


def test_each_factor_percentile_names_its_own_base(macro_page):
    _, texts = macro_page
    bases = [t for t in texts if "percentile of its history (" in t]
    assert len(bases) >= 4, bases
    assert _any(texts, "Each percentile is measured against its own series' full available history")
    # The old standalone metric caption, "Nth percentile of full history". The value-
    # spread panel says "percentile of full history" of its own long series, correctly.
    import re
    assert not [t for t in texts if re.fullmatch(r"\d+(st|nd|rd|th) percentile of full history", t)]


# ── the functions, at their edges ────────────────────────────────────────────

@pytest.mark.parametrize("spread, mean, must, must_not", [
    (0.0, 4.6, "returned about the same (a spread of 0.0 points). That is 4.6 points below",
     "by 0.0"),
    (0.01, 0.0, "returned about the same", "outperformed"),
    (-0.3, -0.3, "underperformed international developed (EFA) by 0.3 percentage points. "
                 "That is near its 5-year rolling average of -0.3 points", "4.6%"),
    (12.0, 1.0, "outperformed international developed (EFA) by 12.0 percentage points. "
                "That is 11.0 points above", "%."),
])
def test_the_cross_asset_sentence_reads_at_zero_ties_and_sign_changes(spread, mean, must, must_not):
    from src.macro import interpret_us_vs_intl_spread
    text = interpret_us_vs_intl_spread(spread, mean)
    assert must in text and must_not not in text, text


def test_the_classifier_and_its_explanation_share_thresholds(monkeypatch):
    """Move a threshold: the label and the explanation move together."""
    from src import macro
    assert macro.classify_regime(0.0, 0.31, 4.1).label == "Late-cycle"
    monkeypatch.setattr(macro, "REGIME_UNRATE_TIGHT", 4.0)
    assert macro.classify_regime(0.0, 0.31, 4.1).label == "Mid-cycle"
    assert "between 4.0% and 5.5%" in macro.regime_explanation("Mid-cycle", 0.31, 4.1)


def test_a_shallow_inversion_says_the_regime_does_not_count_it():
    from src.macro import interpret_curve_spread
    assert "does not count it as inverted" in interpret_curve_spread(-10)
    assert "does not count it as inverted" not in interpret_curve_spread(-40)


def test_ecy_from_the_real_yield():
    from src.macro import compute_ecy_real
    assert compute_ecy_real(40.0, 2.85) == pytest.approx(2.5 - 2.85)


def test_the_demo_prices_the_proxy_legs_over_the_same_history():
    """The data half of the factor-regime bug: IWB and IWF began in April 2025."""
    import sqlite3
    con = sqlite3.connect(f"file:{(ROOT / 'data' / 'demo.db').as_posix()}?mode=ro", uri=True)
    first = dict(con.execute("SELECT ticker, MIN(price_date) FROM prices WHERE ticker IN "
                             "('IWM', 'IWB', 'IWD', 'IWF') GROUP BY ticker").fetchall())
    con.close()
    assert first["IWB"] == first["IWM"] and first["IWF"] == first["IWD"], first
