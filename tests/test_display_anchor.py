"""Display anchor C (last settled trading day) for period-return surfaces.

The Performance page clips every displayed period series (portfolio value +
benchmarks) to the last SETTLED trading day, so a live mid-session partial
intraday bar is never used as a period-window endpoint — which would swing the
displayed 1M/3M returns by the half-day move (~1:1) and make the surfaces
mutually inconsistent. C always excludes today; on a settled window it equals
last_real_price_date, so the deterministic BF reconciliation test (which anchors
on last_real) is unaffected.
"""
import datetime

from src.holdings import last_real_price_date, last_settled_price_date

INC = "2025-05-01"

# A safely-settled historical end: a week ago is always past the current session
# and within committed data (never a freshly-fetched partial bar), so these
# assertions are deterministic and immune to sibling-test cache pollution.
_SETTLED_END = (datetime.date.today() - datetime.timedelta(days=8)).isoformat()


def test_settled_anchor_excludes_today():
    """C is always strictly before today, so an in-progress (partial intraday)
    today-bar can never be a displayed period-window endpoint."""
    c = last_settled_price_date(INC)
    assert c < datetime.date.today().isoformat()


def test_settled_equals_real_on_settled_window():
    """On a settled historical window (end well before today), C == last_real.

    This is the invariant that keeps the BF reconciliation test unchanged: it
    anchors on last_real over committed (settled) data, where C and B coincide.
    """
    assert last_settled_price_date(INC, _SETTLED_END) == last_real_price_date(INC, _SETTLED_END)


def test_settled_anchor_is_deterministic():
    """C for a fixed settled end does not vary across calls (no wall-clock or
    partial-bar dependence)."""
    assert last_settled_price_date(INC, _SETTLED_END) == last_settled_price_date(INC, _SETTLED_END)


def test_risk_window_aligns_with_return_window():
    """The risk-metric trailing-window START (_window_cutoff) anchors on the value
    series' last date — the SAME settled frontier the period-RETURN windows use
    (return side offsets back from series.index[-1]; see _benchmark_period_return /
    _bpr_helper). So the 1M/3M/1Y risk window covers the same span as the return
    window under the same label, and the cutoff is wall-clock-independent.

    Completes #36's settled-frontier alignment on the risk-metrics surface and
    guards against a silent regression back to a date.today()-anchored start.
    """
    import pandas as pd
    from src.performance import _window_cutoff

    last = pd.Timestamp("2026-06-08")  # a settled series end (== C post-#36 clip)
    # Return-side anchor: offset back from the series' last date.
    assert _window_cutoff("1M", last) == last - pd.Timedelta(days=30)
    assert _window_cutoff("3M", last) == last - pd.Timedelta(days=90)
    assert _window_cutoff("1Y", last) == last - pd.Timedelta(days=365)
    assert _window_cutoff("YTD", last) == pd.Timestamp(2026, 1, 1)
    assert _window_cutoff("SI", last) is None

    # Wall-clock independence: a different "today" (a different series end) shifts
    # the window only by the series-end delta — never by date.today().
    other = pd.Timestamp("2026-03-31")
    assert _window_cutoff("1M", other) == other - pd.Timedelta(days=30)


# ── Period Returns insufficient-history row suppression ───────────────────────

def test_period_suppression_short_history():
    """Short history (2-day portfolio): trailing windows (1M/3M/1Y) begin before
    inception → suppressed; YTD ('this year so far') and SI (actual life) always
    legitimate → shown. Data-shape-driven (history length vs window), not mode."""
    from src.returns import period_window_predates_inception
    C, inception = "2026-06-08", "2026-06-06"  # 2 days of history
    assert period_window_predates_inception("1M", C, inception) is True
    assert period_window_predates_inception("3M", C, inception) is True
    assert period_window_predates_inception("1Y", C, inception) is True
    assert period_window_predates_inception("YTD", C, inception) is False  # always show
    assert period_window_predates_inception("SI", C, inception) is False   # never suppress


def test_period_suppression_long_history_unaffected():
    """Full history (demo shape): every window start is after inception → nothing
    suppressed. Pins the demo-stays-unchanged invariant (suppression can't over-fire)."""
    from src.returns import period_window_predates_inception
    C, inception = "2026-06-08", "2025-05-01"
    for p in ("1M", "3M", "YTD", "1Y", "SI"):
        assert period_window_predates_inception(p, C, inception) is False


def test_period_suppression_boundary_per_row():
    """45-day history: 1M (start C−30, after inception) shows; 3M/1Y (start before
    inception) suppress — proves the predicate fires per-row, not all-or-nothing."""
    import datetime
    from src.returns import period_window_predates_inception
    C = datetime.date(2026, 6, 8)
    inception = (C - datetime.timedelta(days=45)).isoformat()
    Cs = C.isoformat()
    assert period_window_predates_inception("1M", Cs, inception) is False  # 30 < 45 → after inception
    assert period_window_predates_inception("3M", Cs, inception) is True   # 90 > 45 → before inception
    assert period_window_predates_inception("1Y", Cs, inception) is True
    assert period_window_predates_inception("SI", Cs, inception) is False
