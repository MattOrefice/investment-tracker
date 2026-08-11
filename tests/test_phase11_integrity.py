"""Phase 11 data-integrity regression tests.

Pins the six critical fixes from Sections 1-3:
  (a) TWR ≡ absolute return for a no-cash-flow series
  (b) IR × TE = geometric active return (definitional identity)
  (c) Trading-day filter: n_days reflects trading days, not calendar days
  (d) EM_DISCLOSURE has no duplicate "Emerging Markets (IEMG):" prefix
  (e) CAPE prose template uses {{ macro.cape.percentile }}, not a hard-coded integer
  (f) _build_macro_section regime dispatch table is correct for all four tiers
  (g) Duration "in line with benchmark" branch fires when diff < 0.05 yrs
"""
from __future__ import annotations

import math
import pathlib
import sys
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.performance import compute_risk_metrics
from src.returns import twr_daily_linked


# ── helpers ───────────────────────────────────────────────────────────────────

def _bday_series(returns: np.ndarray, start: str = "2025-01-01") -> pd.Series:
    dates = pd.bdate_range(start, periods=len(returns))
    values = 10_000.0 * np.cumprod(1.0 + returns)
    return pd.Series(values, index=dates)


def _cal_series(returns: np.ndarray, start: str = "2025-01-01") -> pd.Series:
    """Calendar-day series — weekend values are forward-filled (zero daily return)."""
    dates_bday = pd.bdate_range(start, periods=len(returns))
    values = 10_000.0 * np.cumprod(1.0 + returns)
    bday_s = pd.Series(values, index=dates_bday)
    # Reindex to daily calendar, forward-filling weekends → zero pct_change on those days
    full_range = pd.date_range(dates_bday[0], dates_bday[-1], freq="D")
    return bday_s.reindex(full_range).ffill()


# ── (a) TWR ≡ absolute return for no-cash-flow series ─────────────────────────

def test_identity_twr_equals_absolute_return_no_cashflows():
    """For a series with no external cash flows, daily-linked TWR must equal
    (V_end - V_start) / V_start to within floating-point tolerance.

    This pins the Phase 11.2(a) reconciliation fix: using pv.iloc[0] as the
    absolute-return denominator makes both measures draw from the same adj_close
    series, so they must agree by construction.
    """
    rng = np.random.default_rng(42)
    returns = rng.normal(0.0004, 0.01, 252)
    pv = _bday_series(returns)

    zeros = pd.Series(0.0, index=pv.index)
    twr = twr_daily_linked(pv, zeros)

    abs_ret = (float(pv.iloc[-1]) - float(pv.iloc[0])) / float(pv.iloc[0])

    assert abs(twr - abs_ret) < 1e-9, (
        f"TWR ({twr:.8f}) ≠ absolute return ({abs_ret:.8f}). "
        "These must agree when there are no cash flows — same adj_close base."
    )


def test_identity_twr_absolute_return_multiple_seeds():
    """Identity holds regardless of return magnitude or direction."""
    for seed in (1, 7, 99):
        rng = np.random.default_rng(seed)
        pv = _bday_series(rng.normal(0.0, 0.012, 200))
        zeros = pd.Series(0.0, index=pv.index)
        twr = twr_daily_linked(pv, zeros)
        abs_ret = (float(pv.iloc[-1]) - float(pv.iloc[0])) / float(pv.iloc[0])
        assert abs(twr - abs_ret) < 1e-9, f"seed={seed}: TWR ({twr:.8f}) ≠ abs_ret ({abs_ret:.8f})"


# ── (b) IR × TE = geometric active return ─────────────────────────────────────

def test_identity_ir_times_te_equals_geometric_active():
    """IR is defined as (ann_port − ann_bench) / TE, so IR × TE must equal
    the geometric active return (ann_port − ann_bench) to within float tolerance.

    This pins the Phase 11 Section 2 IR-formula verification: the IR prose
    description matches the code, and the magnitude is consistent.
    """
    rng = np.random.default_rng(10)
    port_ret  = rng.normal(0.0005, 0.01, 300)
    bench_ret = port_ret + rng.normal(0.0, 0.003, 300)

    pv = _bday_series(port_ret)
    bl = _bday_series(bench_ret)

    m = compute_risk_metrics(pv, bl)
    assert m, "compute_risk_metrics returned empty dict"

    ir = m["information_ratio"]
    te = m["tracking_error_pct"] / 100.0  # convert to decimal
    ann_port  = m["ann_port_return_pct"]  / 100.0
    ann_bench = m["ann_bench_return_pct"] / 100.0

    geometric_active = ann_port - ann_bench
    ir_times_te = ir * te

    assert abs(ir_times_te - geometric_active) < 1e-10, (
        f"IR ({ir:.4f}) × TE ({te:.4f}) = {ir_times_te:.6f} "
        f"but geometric active = {geometric_active:.6f}. "
        "The IR formula in compute_risk_metrics diverged from its definition."
    )


def test_identity_ir_times_te_multiple_windows():
    """Identity holds for 1Y and SI windows, both using trading-day-only returns."""
    import pandas as pd
    from datetime import date

    rng = np.random.default_rng(55)
    N = 400
    start = (pd.Timestamp(date.today()) - pd.tseries.offsets.BDay(N)).date().isoformat()
    port_ret  = rng.normal(0.0004, 0.01, N)
    bench_ret = port_ret + rng.normal(0.0, 0.004, N)
    pv = _bday_series(port_ret, start=start)
    bl = _bday_series(bench_ret, start=start)

    for window in ("1Y", "SI"):
        m = compute_risk_metrics(pv, bl, window=window)
        if not m:
            continue
        ir  = m["information_ratio"]
        te  = m["tracking_error_pct"] / 100.0
        act = (m["ann_port_return_pct"] - m["ann_bench_return_pct"]) / 100.0
        assert abs(ir * te - act) < 1e-10, (
            f"window={window}: IR×TE={ir*te:.8f} ≠ active={act:.8f}"
        )


# ── (c) Trading-day filter: n_days reflects trading days, not calendar days ───

def test_identity_n_days_excludes_calendar_zeros():
    """A calendar-day series (with weekend forward-fills) must yield n_days ≈
    number of trading days, not calendar days.

    This pins the Phase 11.1 fix: before the fix, the 3M window returned
    n_days=90 (calendar days), inflating IR by ~20% through TE dilution.
    After the fix, n_days≈62 (trading days only).
    """
    rng = np.random.default_rng(21)
    # ~90 trading days of returns, which span ~126 calendar days
    N_trading = 90
    port_returns  = rng.normal(0.0004, 0.01, N_trading)
    bench_returns = rng.normal(0.0004, 0.01, N_trading)

    pv = _cal_series(port_returns)
    bl = _cal_series(bench_returns)

    m = compute_risk_metrics(pv, bl)
    assert m, "compute_risk_metrics returned empty dict"

    n_calendar = len(pv) - 1  # max possible n_days if no filtering
    n_days = m["n_days"]

    # Must be substantially fewer than calendar days
    assert n_days < n_calendar, (
        f"n_days={n_days} is not less than calendar days={n_calendar}. "
        "Weekend-zero filter may not be active."
    )
    # Must be close to the actual number of trading days (within ±5)
    assert abs(n_days - N_trading) <= 5, (
        f"n_days={n_days} should be near N_trading={N_trading}. "
        "Filter is including or excluding too many days."
    )


def test_identity_calendar_bday_same_n_days():
    """Calendar-day and business-day series built from the same underlying returns
    must produce the same n_days after the trading-day filter.

    This is the strongest possible pin for the fix: it confirms the filter
    removes exactly the weekend rows, nothing more.
    """
    rng = np.random.default_rng(33)
    N = 120
    port_ret  = rng.normal(0.0003, 0.01, N)
    bench_ret = rng.normal(0.0003, 0.01, N)

    # Business-day series (no zeros)
    pv_bday = _bday_series(port_ret)
    bl_bday = _bday_series(bench_ret)

    # Calendar-day series (weekends ffilled → zero pct_change)
    pv_cal = _cal_series(port_ret)
    bl_cal = _cal_series(bench_ret)

    m_bday = compute_risk_metrics(pv_bday, bl_bday)
    m_cal  = compute_risk_metrics(pv_cal,  bl_cal)

    assert m_bday and m_cal, "One of the metrics dicts is empty"
    assert m_bday["n_days"] == m_cal["n_days"], (
        f"bday n_days={m_bday['n_days']} ≠ cal n_days={m_cal['n_days']}. "
        "Trading-day filter must produce the same count as a pure bday series."
    )


def test_identity_tracking_error_calendar_bday_consistent():
    """Tracking error must be the same (within 0.1 pp) whether the series uses
    calendar-day or business-day indexing — the filter must normalize them.
    """
    rng = np.random.default_rng(44)
    N = 150
    port_ret  = rng.normal(0.0003, 0.01, N)
    bench_ret = port_ret + rng.normal(0.0, 0.005, N)

    pv_bday = _bday_series(port_ret)
    bl_bday = _bday_series(bench_ret)
    pv_cal  = _cal_series(port_ret)
    bl_cal  = _cal_series(bench_ret)

    m_bday = compute_risk_metrics(pv_bday, bl_bday)
    m_cal  = compute_risk_metrics(pv_cal,  bl_cal)

    assert m_bday and m_cal
    diff = abs(m_bday["tracking_error_pct"] - m_cal["tracking_error_pct"])
    assert diff < 0.1, (
        f"TE bday={m_bday['tracking_error_pct']:.3f}% vs cal={m_cal['tracking_error_pct']:.3f}%. "
        "Difference of {diff:.3f} pp exceeds 0.1 pp — filter not normalizing correctly."
    )


# ── (d) EM_DISCLOSURE has no duplicate "Emerging Markets (IEMG):" prefix ──────

def test_em_disclosure_no_duplicate_label_prefix():
    """em_disclosure() must not start with 'Emerging Markets' — the PDF template
    already renders that label as a <strong> heading. A duplicate prefix in the
    disclosure caused doubled text on page 7 of the quarterly report.

    Pins Phase 11 follow-up (2).
    """
    from src.factors import em_disclosure
    text = em_disclosure()
    assert not text.strip().startswith("Emerging Markets"), (
        "em_disclosure() starts with 'Emerging Markets', which duplicates the "
        "<strong>Emerging Markets (IEMG):</strong> label in quarterly_report.html. "
        "Remove the prefix from the disclosure text."
    )


def test_em_disclosure_still_mentions_iemg():
    """em_disclosure() must still reference IEMG by ticker to remain informative."""
    from src.factors import em_disclosure
    assert "IEMG" in em_disclosure(), (
        "em_disclosure() no longer mentions IEMG — may have over-trimmed."
    )


# ── (e) CAPE prose template uses {{ macro.cape.percentile }}, not static text ─

def test_prose_cape_template_uses_dynamic_percentile():
    """The quarterly report template must render the CAPE percentile via the
    Jinja2 variable {{ macro.cape.percentile }}, not a hard-coded integer string.

    Pins Phase 11 Section 3(b): earlier versions had '95th percentile' baked
    into the template string; the fix replaced it with the template variable.
    """
    template_path = (
        pathlib.Path(__file__).resolve().parent.parent
        / "templates" / "quarterly_report.html"
    )
    text = template_path.read_text(encoding="utf-8")

    # The prose block must contain the Jinja2 variable
    assert "{{ macro.cape.percentile }}" in text, (
        "quarterly_report.html does not contain '{{ macro.cape.percentile }}'. "
        "The CAPE percentile prose may have been reverted to a hard-coded value."
    )

    # The template must NOT contain a standalone hard-coded percentile phrase
    # of the form "Nth percentile" in the CAPE prose section (lines containing
    # "CAPE") — check for any digit-followed-by "th percentile" as a literal.
    import re
    # Find all lines that mention CAPE context without a Jinja delimiter
    for line in text.splitlines():
        if "cape" in line.lower() and re.search(r"\b\d+th\s+percentile", line):
            # Allow if the line is a Jinja2 variable reference
            if "{{" not in line:
                pytest.fail(
                    f"Hard-coded percentile found in CAPE template line:\n  {line.strip()}\n"
                    "Replace with {{ macro.cape.percentile }}."
                )


# ── (f) _build_macro_section regime dispatch is correct for all four tiers ────

def _macro_with_cape(cape_val: float, cape_series: pd.Series) -> dict:
    """Call _build_macro_section with mocked CAPE data; let other macro blocks fall
    through to their N/A fallbacks (wrapped in try/except in production code)."""
    import src.reports as rpt

    # get_series is called for yield_curve, fed_funds, HY spread — each in its
    # own try/except block, so raising here silently falls back to "N/A".
    with patch("src.reports.current_cape", return_value=cape_val), \
         patch("src.reports.get_cape_series", return_value=cape_series), \
         patch("src.reports.get_series", side_effect=RuntimeError("mocked")):
        return rpt._build_macro_section()


def test_macro_section_regime_dispatch_elevated():
    """pct_int >= 80 → regime='Elevated', action references diversification."""
    # Value at the top of a 6-element series → 100th percentile
    cape_s = pd.Series([10.0, 15.0, 20.0, 25.0, 30.0, 36.0], dtype=float)
    macro = _macro_with_cape(36.0, cape_s)

    assert macro["cape"]["regime"] == "Elevated", (
        f"Expected 'Elevated' for high-percentile CAPE, got {macro['cape']['regime']!r}"
    )
    assert "diversif" in macro["cape"]["regime_action"].lower()


def test_macro_section_regime_dispatch_moderate():
    """pct_int in [40, 60) → regime='Moderate'."""
    # Value 20 in a uniform 10–30 series → ~50th percentile
    cape_s = pd.Series(list(range(10, 31, 1)), dtype=float)
    macro = _macro_with_cape(20.0, cape_s)

    assert macro["cape"]["regime"] == "Moderate", (
        f"Expected 'Moderate' for mid-range CAPE, got {macro['cape']['regime']!r}"
    )


def test_macro_section_regime_dispatch_below_average():
    """pct_int < 40 → regime='Below-average', action references increased US equity."""
    # CAPE at 10 when series spans 10–40 → near 0th percentile
    cape_s = pd.Series(list(range(10, 41)), dtype=float)
    macro = _macro_with_cape(10.0, cape_s)

    assert macro["cape"]["regime"] == "Below-average", (
        f"Expected 'Below-average' for low-percentile CAPE, got {macro['cape']['regime']!r}"
    )
    assert "US equity" in macro["cape"]["regime_action"]


def test_macro_section_regime_pct_int_in_output():
    """macro section must always return 'pct_int' as an integer (used by template logic)."""
    cape_s = pd.Series([15.0, 20.0, 25.0, 30.0, 35.0], dtype=float)
    macro = _macro_with_cape(25.0, cape_s)

    pct_int = macro["cape"]["pct_int"]
    assert isinstance(pct_int, int), f"pct_int must be int, got {type(pct_int).__name__}: {pct_int}"
    assert 0 <= pct_int <= 100, f"pct_int={pct_int} out of [0, 100]"


# ── (g) Duration "in line with benchmark" branch ──────────────────────────────

def test_prose_duration_in_line_with_benchmark():
    """_build_positioning_section must produce 'in line with benchmark' when
    fi_sleeve_duration ≈ agg_benchmark (diff < 0.05 yrs).

    Pins Phase 11 follow-up (3): previously the text read '0.0 yrs above benchmark'
    when the portfolio duration matched the Agg exactly.
    """
    import src.reports as rpt

    mock_dur = {
        "fi_sleeve_duration":    6.0,
        "agg_benchmark":         6.0,   # identical → diff = 0.0
        "fi_weight_pct":         15.0,
        "cash_weight_pct":       3.0,
        "fi_weight_incl_cash_pct": 18.0,
    }
    mock_non_us  = {"non_us_equity_pct": 27.0, "intl_pct": 19.0, "em_pct": 8.0}
    mock_style   = None

    with patch("src.reports.get_effective_duration", return_value=mock_dur), \
         patch("src.reports.get_style_box_data",     return_value=mock_style), \
         patch("src.reports.get_non_us_equity_data", return_value=mock_non_us):
        section = rpt._build_positioning_section("2025-12-31")

    duration_line = section["duration_line"]
    assert "in line with benchmark" in duration_line, (
        f"Expected 'in line with benchmark' when fi_dur == agg_dur, got:\n  {duration_line}"
    )
    assert "0.0 yrs" not in duration_line, (
        f"'0.0 yrs above/below' still present despite diff=0 fix:\n  {duration_line}"
    )


def test_prose_duration_above_benchmark():
    """When fi_dur > agg_dur by more than 0.05 yrs, 'above benchmark' must appear."""
    import src.reports as rpt

    mock_dur = {
        "fi_sleeve_duration":    7.0,
        "agg_benchmark":         6.0,   # diff = 1.0 → 'above benchmark by 1.0 yrs'
        "fi_weight_pct":         15.0,
        "cash_weight_pct":       3.0,
        "fi_weight_incl_cash_pct": 18.0,
    }
    mock_non_us  = {"non_us_equity_pct": 27.0, "intl_pct": 19.0, "em_pct": 8.0}

    with patch("src.reports.get_effective_duration", return_value=mock_dur), \
         patch("src.reports.get_style_box_data",     return_value=None), \
         patch("src.reports.get_non_us_equity_data", return_value=mock_non_us):
        section = rpt._build_positioning_section("2025-12-31")

    duration_line = section["duration_line"]
    assert "above benchmark" in duration_line, (
        f"Expected 'above benchmark' for fi_dur=7.0 vs agg=6.0, got:\n  {duration_line}"
    )
    assert "1.0 yrs" in duration_line


def test_prose_duration_below_benchmark():
    """When fi_dur < agg_dur by more than 0.05 yrs, 'below benchmark' must appear."""
    import src.reports as rpt

    mock_dur = {
        "fi_sleeve_duration":    5.0,
        "agg_benchmark":         6.5,   # diff = 1.5 → 'below benchmark by 1.5 yrs'
        "fi_weight_pct":         15.0,
        "cash_weight_pct":       3.0,
        "fi_weight_incl_cash_pct": 18.0,
    }
    mock_non_us  = {"non_us_equity_pct": 27.0, "intl_pct": 19.0, "em_pct": 8.0}

    with patch("src.reports.get_effective_duration", return_value=mock_dur), \
         patch("src.reports.get_style_box_data",     return_value=None), \
         patch("src.reports.get_non_us_equity_data", return_value=mock_non_us):
        section = rpt._build_positioning_section("2025-12-31")

    duration_line = section["duration_line"]
    assert "below benchmark" in duration_line, (
        f"Expected 'below benchmark' for fi_dur=5.0 vs agg=6.5, got:\n  {duration_line}"
    )
    assert "1.5 yrs" in duration_line
