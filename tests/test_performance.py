"""Tests for src/performance.py — VaR, CVaR, and risk metrics."""
import sys
import pathlib
import math

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.performance import MIN_TRADING_DAYS, compute_risk_metrics, trading_days_in_window


def _make_series(returns: np.ndarray, start: str = "2025-01-01") -> pd.Series:
    """Build a portfolio value series from a daily return array."""
    dates = pd.bdate_range(start, periods=len(returns))
    values = 10_000 * np.cumprod(1 + returns)
    return pd.Series(values, index=dates)


# The day the window tests' series end, and the day they pin as today: a fixed session
# late in a year, so the year-to-date window is far past its floor whenever the suite
# runs. The two tests below ended their series on date.today(), and failed from the
# first session of each January until the window had figures again (#496).
SERIES_END = "2026-12-15"


def _series_ending(returns: np.ndarray, end: str = SERIES_END) -> pd.Series:
    """A value series of one business day per return, its last day ``end``."""
    dates = pd.bdate_range(end=end, periods=len(returns))
    assert dates[-1] == pd.Timestamp(end), f"premise: {end} is a business day"
    return pd.Series(10_000 * np.cumprod(1 + returns), index=dates)


@pytest.fixture
def at_series_end(monkeypatch):
    """Today pinned to SERIES_END, so no window in these tests can follow the calendar."""
    from datetime import date as _date
    from tests.conftest import pin_today, unpin_leftovers
    pin_today(monkeypatch, _date.fromisoformat(SERIES_END))
    yield
    unpin_leftovers()


# ── VaR and CVaR unit tests ───────────────────────────────────────────────────

def test_var_95_positive_loss():
    """VaR(95%) must be a positive number (loss, not return)."""
    rng = np.random.default_rng(1)
    returns = rng.normal(0.0005, 0.01, 100)
    pv = _make_series(returns)
    bl = _make_series(np.zeros(100))

    m = compute_risk_metrics(pv, bl)
    assert "var_95_pct" in m
    assert m["var_95_pct"] > 0, "VaR must be a positive loss magnitude"


def test_cvar_ge_var():
    """CVaR(95%) must be ≥ VaR(95%) — CVaR is the average of worst tail losses."""
    rng = np.random.default_rng(2)
    returns = rng.normal(0.0003, 0.015, 200)
    pv = _make_series(returns)
    bl = _make_series(np.zeros(200))

    m = compute_risk_metrics(pv, bl)
    assert m["cvar_95_pct"] >= m["var_95_pct"], (
        f"CVaR ({m['cvar_95_pct']:.3f}%) must be >= VaR ({m['var_95_pct']:.3f}%)"
    )


def test_var_known_normal_distribution():
    """
    For a large iid N(μ=0, σ=0.01) sample:
    VaR(95%) ≈ 1.645 × σ = 1.645% (daily).
    Allow ±0.3% tolerance for finite-sample noise.
    """
    rng = np.random.default_rng(42)
    N = 2000
    returns = rng.normal(0.0, 0.01, N)
    pv = _make_series(returns)
    bl = _make_series(np.zeros(N))

    m = compute_risk_metrics(pv, bl)
    # 1.645 * 1.0% = 1.645% daily VaR
    assert abs(m["var_95_pct"] - 1.645) < 0.30, (
        f"VaR(95%) for N(0, 0.01) should be ~1.645%; got {m['var_95_pct']:.3f}%"
    )


def test_cvar_known_normal_distribution():
    """
    For large iid N(μ=0, σ=0.01):
    CVaR(95%) ≈ σ × φ(1.645) / 0.05 = 1.0% × 0.1031 / 0.05 ≈ 2.063%.
    Allow ±0.4% tolerance for finite-sample noise.
    """
    rng = np.random.default_rng(42)
    N = 2000
    returns = rng.normal(0.0, 0.01, N)
    pv = _make_series(returns)
    bl = _make_series(np.zeros(N))

    m = compute_risk_metrics(pv, bl)
    # φ(1.645) ≈ 0.1031
    expected_cvar = 0.01 * 0.1031 / 0.05 * 100   # ≈ 2.063%
    assert abs(m["cvar_95_pct"] - expected_cvar) < 0.40, (
        f"CVaR(95%) for N(0, 0.01) should be ~{expected_cvar:.2f}%; got {m['cvar_95_pct']:.3f}%"
    )


def test_risk_metrics_returns_var_cvar_keys():
    """compute_risk_metrics must include 'var_95_pct' and 'cvar_95_pct' in output."""
    rng = np.random.default_rng(7)
    returns = rng.normal(0.0003, 0.01, 50)
    pv = _make_series(returns)
    bl = _make_series(np.zeros(50))

    m = compute_risk_metrics(pv, bl)
    assert "var_95_pct" in m,  "var_95_pct missing from compute_risk_metrics output"
    assert "cvar_95_pct" in m, "cvar_95_pct missing from compute_risk_metrics output"


# ── Alpha CI unit tests ───────────────────────────────────────────────────────

def test_compute_risk_metrics_short_windows(at_series_end):
    """compute_risk_metrics must support 1M, 3M, YTD, and 1Y windows.

    Regression pin: Phase 8u. Extended from SI+1Y to five windows matching the
    BF Attribution period set. Uses a synthetic 400-day series that ends on
    SERIES_END, so every window clears the floor, the year-to-date one included;
    asserts all seven keys are present and values are finite.
    """
    import math as _math
    rng = np.random.default_rng(99)
    N400 = 400
    returns = rng.normal(0.0004, 0.01, N400)
    pv = _series_ending(returns)
    bl = _series_ending(np.zeros(N400))

    expected_keys = {
        "sharpe", "sortino", "max_drawdown_pct", "tracking_error_pct",
        "information_ratio", "var_95_pct", "cvar_95_pct",
    }
    for w in ("1M", "3M", "YTD", "1Y", "SI"):
        m = compute_risk_metrics(pv, bl, window=w)
        assert m, f"window={w!r} returned empty dict — check observation threshold"
        missing = expected_keys - m.keys()
        assert not missing, f"window={w!r} missing keys: {missing}"
        for k in expected_keys:
            assert _math.isfinite(m[k]), (
                f"window={w!r} key {k!r} is not finite: {m[k]}"
            )


def test_risk_metrics_distinguish_across_windows(at_series_end):
    """Window filtering must produce monotonically shorter obs counts and distinct Sharpe.

    Regression pin: Phase 8u.1. Previous tests only asserted non-NaN; this test
    pins that each window produces a distinct observation count (chain assertion)
    and that Sharpe values clearly differ between short and full windows.

    Uses a step-down return series: strongly positive returns for the first 300
    business days, then negative for the last 100 business days. This guarantees:
      - 1M/3M Sharpe is negative (window falls in the negative period)
      - SI Sharpe is positive  (300 positive days dominate 100 negative days)

    If window filtering collapses (all windows return the SI series), 1M Sharpe
    would be positive like SI — and the sign assertion would fail.
    """
    rng = np.random.default_rng(77)
    N = 400
    returns = np.concatenate([
        rng.normal(0.003, 0.006, 300),   # first 300 days: strongly positive
        rng.normal(-0.005, 0.004, 100),  # last 100 days: strongly negative, low vol
        # Low vol in the negative period ensures the 1M sample mean (21 obs)
        # is reliably negative regardless of seed: P(mean>0) < 1e-6.
    ])
    # The series ends on SERIES_END, so all window cutoffs (1M/3M/YTD/1Y) fall within
    # it and the year-to-date window is the same one every day the suite runs (#496).
    pv = _series_ending(returns)
    bl = _series_ending(np.zeros(N))

    results = {w: compute_risk_metrics(pv, bl, window=w) for w in ("1M", "3M", "YTD", "1Y", "SI")}

    for w in ("1M", "3M", "YTD", "1Y", "SI"):
        assert results[w], f"window={w!r} returned empty dict — observation threshold not met"

    # Obs-count chain: shorter calendar window = fewer return observations.
    # This is the strongest possible pin: if any window returns SI obs count,
    # the cutoff filter collapsed regardless of what metric values came out.
    n = {w: results[w]["n_days"] for w in ("1M", "3M", "YTD", "1Y", "SI")}
    assert n["1M"] < n["3M"],  f"1M ({n['1M']} obs) must be < 3M ({n['3M']} obs)"
    assert n["3M"] < n["1Y"],  f"3M ({n['3M']} obs) must be < 1Y ({n['1Y']} obs)"
    assert n["1Y"] < n["SI"],  f"1Y ({n['1Y']} obs) must be < SI ({n['SI']} obs)"
    # YTD from January 1 through SERIES_END, December 15: longer than 3M, shorter
    # than the year back from the same day.
    assert n["3M"] < n["YTD"] < n["1Y"], (
        f"YTD ({n['YTD']} obs) must be between 3M ({n['3M']}) and 1Y ({n['1Y']})"
    )
    # One return per session after the window's first: 2026 has 249 business days
    # from January 1 through December 15.
    assert n["YTD"] == len(pd.bdate_range("2026-01-01", SERIES_END)) - 1 == 248

    # Sharpe must clearly distinguish short vs. long windows.
    # Step-down series: first 300 days strongly positive, last 100 days strongly
    # negative (low vol so the 1M sample mean is deterministically negative).
    # If window filtering collapsed, 1M would report SI values → difference ≈ 0.
    assert results["1M"]["sharpe"] < 0, (
        f"1M Sharpe ({results['1M']['sharpe']:.3f}) should be negative — "
        f"1M window falls in the strongly-negative tail of the test series. "
        f"Positive 1M Sharpe means the cutoff is not filtering correctly."
    )
    assert results["SI"]["sharpe"] > 0, (
        f"SI Sharpe ({results['SI']['sharpe']:.3f}) should be positive — "
        f"300 positive days dominate 100 negative days over the full series."
    )
    sharpe_gap = abs(results["1M"]["sharpe"] - results["SI"]["sharpe"])
    assert sharpe_gap > 1.0, (
        f"1M Sharpe ({results['1M']['sharpe']:.3f}) and SI Sharpe ({results['SI']['sharpe']:.3f}) "
        f"differ by only {sharpe_gap:.3f}. Expected > 1.0 — if gap is small, window "
        f"filtering collapsed and all windows are returning the full SI series. "
        f"Fix: restore .copy() after cutoff slice in src/performance.py."
    )


@pytest.mark.parametrize("today", ["2026-10-12", "2026-12-15", "2027-01-04", "2027-01-12",
                                   "2027-01-28", "2027-01-29", "2027-02-10"])
def test_the_two_window_tests_pass_whatever_the_date(monkeypatch, today):
    """#496's table, kept as a test. Called with today pinned to each of these dates,
    both tests above failed on the three in January. They read no clock now, so each
    passes on all seven."""
    import datetime
    from tests.conftest import pin_today, unpin_leftovers
    pin_today(monkeypatch, datetime.date.fromisoformat(today))
    try:
        assert datetime.date.today().isoformat() == today, "premise: the pin is up"
        test_compute_risk_metrics_short_windows(None)
        test_risk_metrics_distinguish_across_windows(None)
    finally:
        unpin_leftovers()


# ── the floor: 15 trading days (#502) ─────────────────────────────────────────

def _page_series(end: str, days: int = 500, flat_from: "str | None" = None):
    """(pv, bl) shaped as the Performance page's: one row per CALENDAR day ending
    ``end``, a move on every NYSE session and none on a weekend or holiday. From
    ``flat_from`` on, no day moves: closes carried forward."""
    from src.demo_refresh import is_session
    idx = pd.date_range(end=end, periods=days, freq="D")
    rng = np.random.default_rng(99)
    moves = [(rng.normal(0.0004, 0.01), rng.normal(0.0003, 0.008)) for _ in idx]
    live = [is_session(d.date()) and (flat_from is None or d < pd.Timestamp(flat_from))
            for d in idx]
    port = np.array([m[0] if on else 0.0 for m, on in zip(moves, live)])
    bench = np.array([m[1] if on else 0.0 for m, on in zip(moves, live)])
    return (pd.Series(10_000 * np.cumprod(1 + port), index=idx),
            pd.Series(100 * np.cumprod(1 + bench), index=idx))


def _sessions(first: str, last: str) -> int:
    from src.demo_refresh import is_session
    return sum(is_session(d.date()) for d in pd.date_range(first, last, freq="D"))


def test_the_floor_is_fifteen():
    assert MIN_TRADING_DAYS == 15


@pytest.mark.parametrize("end, session_of_the_year, shows", [
    ("2027-01-22", 14, False),     # the year's 14th session: no figures
    ("2027-01-25", 15, True),      # its 15th: figures, on 15 trading days
])
def test_the_ytd_window_is_empty_until_the_years_fifteenth_session(end, session_of_the_year,
                                                                   shows):
    """Both sides of the floor, on a series with a row for every calendar day, which is
    what the page passes. Every other window has figures on both days."""
    pv, bl = _page_series(end)
    assert _sessions("2027-01-01", end) == session_of_the_year, "premise: the calendar"
    assert trading_days_in_window(pv, bl, "YTD") == session_of_the_year
    ytd = compute_risk_metrics(pv, bl, window="YTD")
    if shows:
        assert ytd["n_days"] == session_of_the_year and math.isfinite(ytd["sharpe"])
    else:
        assert ytd == {}
    for w in ("1M", "3M", "1Y", "SI"):
        assert compute_risk_metrics(pv, bl, window=w), w


def test_twenty_calendar_rows_are_not_twenty_trading_days():
    """The floor that went (#502): 20 ROWS. On January 20, 2027 the page's year-to-date
    window holds 20 rows and 12 sessions. It showed figures there, on 12 returns, the
    day after saying it needed 20 trading days. Now it has none until the 15th session."""
    pv, bl = _page_series("2027-01-20")
    assert int((pv.index >= "2027-01-01").sum()) == 20, "premise: 20 calendar rows"
    assert trading_days_in_window(pv, bl, "YTD") == _sessions("2027-01-01", "2027-01-20") == 12
    assert compute_risk_metrics(pv, bl, window="YTD") == {}


@pytest.mark.parametrize("rows_in_year, shows", [(15, False), (16, True), (19, True)])
def test_a_series_of_sessions_only_is_held_to_the_same_fifteen(rows_in_year, shows):
    """One row per business day, as the tests above this section build theirs: the
    window's first row is the base its first return is measured from, so 16 rows are 15
    trading days. 19 rows had no figures under the 20-row floor; they hold 18 trading days."""
    end = pd.bdate_range("2027-01-01", periods=rows_in_year)[-1].strftime("%Y-%m-%d")
    rng = np.random.default_rng(99)
    pv = _series_ending(rng.normal(0.0004, 0.01, 400), end=end)
    bl = _series_ending(np.zeros(400), end=end)
    assert int((pv.index >= "2027-01-01").sum()) == rows_in_year
    assert trading_days_in_window(pv, bl, "YTD") == rows_in_year - 1
    ytd = compute_risk_metrics(pv, bl, window="YTD")
    assert bool(ytd) is shows
    if shows:
        assert ytd["n_days"] == rows_in_year - 1


def test_a_one_month_window_of_nineteen_sessions_has_figures():
    """Why 15 and not 20. The 1 Month window is the 30 days to the last close, and the
    30 days to June 22, 2026 hold 19 sessions (Memorial Day and Juneteenth fall in
    them). A floor of 20 would hide the window that day."""
    pv, bl = _page_series("2026-06-22")
    assert _sessions("2026-05-24", "2026-06-22") == 19, "premise: the calendar"
    assert trading_days_in_window(pv, bl, "1M") == 19
    assert MIN_TRADING_DAYS <= 19 < 20
    assert compute_risk_metrics(pv, bl, window="1M")["n_days"] == 19


def test_days_on_which_nothing_moved_are_not_trading_days():
    """Closes carried forward flat from January 1: the year has rows and no return, so
    the window holds no trading day, however many rows it has (#502's second table)."""
    pv, bl = _page_series("2027-02-10", flat_from="2027-01-01")
    assert int((pv.index >= "2027-01-01").sum()) == 41
    assert trading_days_in_window(pv, bl, "YTD") == 0
    assert compute_risk_metrics(pv, bl, window="YTD") == {}


@pytest.mark.parametrize("window", ["1M", "3M", "YTD", "1Y", "SI"])
def test_the_count_the_page_prints_is_the_count_the_floor_is_held_to(window):
    """trading_days_in_window and compute_risk_metrics count from the same returns, with
    and without flows. The flow here falls on a Saturday, a day with no return until
    the flow is taken out of it, so the two counts differ by that day and each function
    must be given the flows to agree with the other."""
    pv, bl = _page_series("2026-12-15")
    flows = pd.Series(0.0, index=pv.index)
    flows.loc["2026-12-05"] = 250.0
    assert pd.Timestamp("2026-12-05").dayofweek == 5, "premise: a Saturday"
    for cf in (None, flows):
        m = compute_risk_metrics(pv, bl, window=window, cashflows=cf)
        assert trading_days_in_window(pv, bl, window, cashflows=cf) == m["n_days"]
    assert (trading_days_in_window(pv, bl, window, cashflows=flows)
            == trading_days_in_window(pv, bl, window) + 1)
    assert trading_days_in_window(pv.iloc[:0], bl.iloc[:0], window) == 0, "an empty series"


def test_alpha_ci_str_format():
    """alpha_ci_str must produce '+N bps/yr [±lo, ±hi]' format."""
    from src.factors import alpha_ci_str
    mock_result = {
        "alpha_annual_bps": 127.0,
        "_hac_bse": {"const": 0.0005},  # daily SE in decimal
    }
    s = alpha_ci_str(mock_result)
    assert "bps/yr" in s
    assert "[" in s and "]" in s


def test_alpha_ci_str_known_values():
    """
    alpha = 127 bps/yr, SE_daily = 0.0005 decimal
    SE_bps = 0.0005 × 252 × 10000 = 1260 bps
    95% CI: 127 ± 1.96 × 1260 = 127 ± 2469.6
    lo ≈ −2342, hi ≈ +2597
    """
    from src.factors import alpha_ci_str
    se_daily = 0.0005
    a_bps    = 127.0
    se_bps   = se_daily * 252 * 10_000  # = 1260
    lo = a_bps - 1.96 * se_bps           # ≈ −2342
    hi = a_bps + 1.96 * se_bps           # ≈ +2597

    mock_result = {
        "alpha_annual_bps": a_bps,
        "_hac_bse": {"const": se_daily},
    }
    s = alpha_ci_str(mock_result)
    assert "+127 bps/yr" in s
    assert str(int(round(abs(lo)))) in s
    assert str(int(round(hi))) in s


def test_alpha_ci_str_no_hac_bse():
    """When _hac_bse is missing, alpha_ci_str falls back to plain bps string."""
    from src.factors import alpha_ci_str
    mock_result = {"alpha_annual_bps": -94.0, "_hac_bse": {}}
    s = alpha_ci_str(mock_result)
    assert "bps/yr" in s
    assert "[" not in s   # no CI when SE is unavailable
