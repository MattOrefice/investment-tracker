"""Tests for src/macro.py — CAPE implied return formula, ECY, FRED retry, window_pctile."""
import math
import sys
import pathlib

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.macro import (
    compute_cape_implied_return, compute_ecy, percentile, window_pctile,
    classify_regime, _REGIME_LABELS, format_ur_delta,
)


def test_cape_16_anchor():
    """CAPE=16 is the formula's calibration anchor — implied return = exactly 6.6%."""
    result = compute_cape_implied_return(16.0)
    assert abs(result - 0.066) < 0.001, f"Expected ~6.6%, got {result:.4%}"


def test_cape_25_midrange():
    """CAPE=25 is mid-range; result must match the log-linear formula exactly."""
    expected = -0.070 * math.log(25 / 16) + 0.066
    result   = compute_cape_implied_return(25.0)
    assert abs(result - expected) < 1e-9


def test_cape_30_8_current_is_positive():
    """CAPE=30.8 (current market) must give a POSITIVE return (~+2.0%), not -2.0%."""
    result = compute_cape_implied_return(30.8)
    assert result > 0, (
        f"Implied return at CAPE=30.8 must be positive; got {result:.4%}. "
        "A negative value indicates a sign error in the formula."
    )
    assert abs(result - 0.020) < 0.005, f"Expected ~+2.0%, got {result:.4%}"


def test_cape_44_elevated_is_negative():
    """CAPE=44 (2000-era level) must give a NEGATIVE implied return."""
    result = compute_cape_implied_return(44.0)
    assert result < 0, f"Implied return at CAPE=44 must be negative; got {result:.4%}"


def test_cape_monotonically_decreasing():
    """Higher CAPE must always produce lower implied forward return."""
    capes   = [10, 16, 20, 25, 30, 35, 40, 50]
    returns = [compute_cape_implied_return(c) for c in capes]
    for i in range(len(returns) - 1):
        assert returns[i] > returns[i + 1], (
            f"Monotonicity failed: CAPE={capes[i]} gives {returns[i]:.4%} "
            f"but CAPE={capes[i+1]} gives {returns[i+1]:.4%}"
        )


# ── ECY tests ─────────────────────────────────────────────────────────────────

def test_ecy_positive():
    """CAPE=25, DGS10=3.0%, T10YIE=2.0% → ECY = 100/25 − (3.0−2.0) = 4.0 − 1.0 = 3.0%"""
    assert abs(compute_ecy(25.0, 3.0, 2.0) - 3.0) < 1e-9


def test_ecy_negative():
    """CAPE=40, DGS10=6.0%, T10YIE=2.0% → ECY = 100/40 − (6.0−2.0) = 2.5 − 4.0 = −1.5%"""
    assert abs(compute_ecy(40.0, 6.0, 2.0) - (-1.5)) < 1e-9


def test_ecy_zero():
    """ECY = 0 when CAPE earnings yield exactly equals real bond rate."""
    # CAPE=25 → earnings yield 4.0%; T10Y=6.0%, T10YIE=2.0% → real rate 4.0%
    assert abs(compute_ecy(25.0, 6.0, 2.0) - 0.0) < 1e-9


def test_ecy_higher_cape_lower_value():
    """Higher CAPE reduces the earnings yield, lowering ECY all else equal."""
    ecy_low_cape  = compute_ecy(20.0, 4.0, 2.0)   # earnings yield 5% → ECY 3%
    ecy_high_cape = compute_ecy(40.0, 4.0, 2.0)   # earnings yield 2.5% → ECY 0.5%
    assert ecy_low_cape > ecy_high_cape


# ── FRED retry tests ──────────────────────────────────────────────────────────

def test_fetch_fred_retries_on_transient_failure(monkeypatch):
    """fetch_fred_series retries on transient failure and succeeds on second attempt."""
    import src.macro as _m

    call_count = [0]

    class MockFred:
        def get_series(self, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("Internal Server Error")
            return pd.Series([1.0], index=pd.to_datetime(["2020-01-01"]))

    monkeypatch.setattr(_m, "_get_fred", lambda: MockFred())
    monkeypatch.setattr(_m, "_FRED_RETRY_DELAYS", (0, 0, 0))

    result = _m.fetch_fred_series("DFF", "2020-01-01")
    assert call_count[0] == 2
    assert isinstance(result, pd.Series)
    assert len(result) == 1


def test_fetch_fred_raises_after_all_retries(monkeypatch):
    """fetch_fred_series raises FREDFetchError after all attempts are exhausted."""
    import src.macro as _m
    from src.macro import FREDFetchError

    class MockFred:
        def get_series(self, *args, **kwargs):
            raise RuntimeError("Internal Server Error")

    monkeypatch.setattr(_m, "_get_fred", lambda: MockFred())
    monkeypatch.setattr(_m, "_FRED_RETRY_DELAYS", (0, 0, 0))

    with pytest.raises(FREDFetchError) as exc_info:
        _m.fetch_fred_series("T10Y2Y", "2020-01-01")
    assert "T10Y2Y" in str(exc_info.value)


def test_fredetcherror_carries_series_id():
    """FREDFetchError.series_id attribute must equal the requested series."""
    from src.macro import FREDFetchError
    cause = ValueError("network timeout")
    err   = FREDFetchError("DGS10", cause)
    assert err.series_id == "DGS10"
    assert err.cause is cause
    assert "DGS10" in str(err)


# ── window_pctile unit tests ───────────────────────────────────────────────────

def _synthetic_series(n: int = 300, seed: int = 0) -> pd.Series:
    rng  = np.random.default_rng(seed)
    vals = rng.normal(0, 1, n)
    idx  = pd.date_range("2015-01-01", periods=n, freq="ME")
    return pd.Series(vals, index=idx)


def test_window_pctile_max_uses_full_series():
    """'1800-01-01' sentinel must use the entire series, same as percentile() — and must
    NOT report that as a fallback: it is what the sentinel asks for."""
    s   = _synthetic_series()
    val = float(s.iloc[-1])
    got = window_pctile(s, val, "1800-01-01")
    assert abs(got.value - percentile(s, val)) < 1e-9
    assert got.fell_back is False
    assert got.n == len(s.dropna())


def test_window_pctile_5y_differs_from_full():
    """5Y window percentile must differ from full-history percentile for a trending series."""
    # Monotonically increasing series: last 5 years cluster at the top
    idx = pd.date_range("2010-01-01", periods=180, freq="ME")
    s   = pd.Series(range(180), index=idx, dtype=float)
    val = float(s.iloc[-1])          # maximum value

    full_pctile = percentile(s, val)          # should be ~100
    w5_start    = (idx[-1] - pd.DateOffset(years=5)).isoformat()[:10]
    w5_pctile   = window_pctile(s, val, w5_start).value   # also ~100 since val is still max

    # Both should be 100 for the absolute maximum
    assert full_pctile == pytest.approx(100.0, abs=1.0)
    assert w5_pctile   == pytest.approx(100.0, abs=1.0)

    # Now test a mid-range value — windowed pctile should differ from full
    mid_val     = float(s.iloc[150])          # 150/180 = 83rd percentile full history
    full_mid    = percentile(s, mid_val)
    w5_mid      = window_pctile(s, mid_val, w5_start).value
    # The 5Y window only has values 120..179, so mid_val (150) is below the median
    # of the 5Y window (median ~149.5) — roughly the 50th percentile of that window
    assert w5_mid < full_mid, (
        f"5Y window pctile ({w5_mid:.1f}) should be lower than full ({full_mid:.1f}) "
        "for a value that's mid-history in a monotonically increasing series"
    )


def test_window_pctile_10y_uses_only_windowed_data():
    """10Y window percentile is computed strictly on data from w_start onward."""
    idx = pd.date_range("2005-01-01", periods=240, freq="ME")
    s   = pd.Series(range(240), index=idx, dtype=float)

    w10_start = (idx[-1] - pd.DateOffset(years=10)).isoformat()[:10]
    val       = float(s.loc[w10_start:].median())
    w10_pctile = window_pctile(s, val, w10_start).value

    # Median of the 10Y window = 50th percentile of that window
    assert 48.0 < w10_pctile < 52.0, f"Median should be near 50th pctile; got {w10_pctile:.1f}"


def test_window_pctile_empty_window_falls_back_to_full():
    """If window start is after all data, falls back to full-series percentile."""
    idx = pd.date_range("2010-01-01", periods=60, freq="ME")
    s   = pd.Series(np.linspace(1, 60, 60), index=idx)
    val = float(s.iloc[30])

    # Window starting after end of data
    future_start = "2030-01-01"
    got = window_pctile(s, val, future_start)
    assert got.value == pytest.approx(percentile(s, val), abs=1e-9)
    assert got.fell_back is True, (
        "the fallback now has to ANNOUNCE itself — that it happens silently was the "
        "defect, and this test asserted the silence as the contract"
    )


def test_window_pctile_5y_matches_manual_slice():
    """window_pctile must exactly equal percentile(series[w_start:].dropna(), val)."""
    s         = _synthetic_series(n=120, seed=7)
    w5_start  = (s.index[-1] - pd.DateOffset(years=5)).isoformat()[:10]
    val       = float(s.iloc[-1])
    expected  = percentile(s.loc[w5_start:].dropna(), val)
    assert window_pctile(s, val, w5_start).value == pytest.approx(expected, abs=1e-9)


def test_window_pctile_10y_matches_manual_slice():
    """window_pctile 10Y window must equal percentile(series[w_start:].dropna(), val)."""
    s         = _synthetic_series(n=240, seed=13)
    w10_start = (s.index[-1] - pd.DateOffset(years=10)).isoformat()[:10]
    val       = float(s.quantile(0.75))
    expected  = percentile(s.loc[w10_start:].dropna(), val)
    assert window_pctile(s, val, w10_start).value == pytest.approx(expected, abs=1e-9)


# ── classify_regime unit tests ───────────────────────────────────────────────
# The header used to read "(Layer 1: no None gaps)". That framing WAS the defect:
# it treated the absence of a None label as the invariant, when the real invariant is
# that a label is never OUTSIDE the four. A verdict from no signals is a gap, not the
# absence of one. Only ONE case below changed behaviour — (None, None, None) — which
# is the measure of how narrow the floor is.

@pytest.mark.parametrize("usrec,t10y2y,unrate,expected", [
    # Rule 1: Recession when USREC = 1
    (1.0,  1.0,  6.0, "Recession"),
    (1.0, -0.5,  4.0, "Recession"),
    (1.0,  None, None, "Recession"),
    # Rule 2: Early-cycle — USREC=0, UNRATE > 5.5, curve not deeply inverted
    (0.0,  0.5,  7.0, "Early-cycle"),
    (0.0,  0.0,  6.0, "Early-cycle"),
    (0.0,  None, 8.0, "Early-cycle"),
    # Rule 3a: Late-cycle — curve inverted
    (0.0, -0.5,  4.5, "Late-cycle"),
    (0.0, -0.3,  5.0, "Late-cycle"),
    # Rule 3b: Late-cycle — labor very tight
    (0.0,  0.5,  3.9, "Late-cycle"),
    (0.0,  None, 4.0, "Late-cycle"),
    # Rule 4: Mid-cycle default
    (0.0,  1.0,  4.5, "Mid-cycle"),
    (0.0,  0.0,  5.0, "Mid-cycle"),
    (None, None, None, None),          # all signals missing → NO VERDICT (was "Mid-cycle")
])
def test_classify_regime_rules(usrec, t10y2y, unrate, expected):
    """classify_regime must return the correct label for each signal combination."""
    assert classify_regime(usrec, t10y2y, unrate).label == expected


def test_classify_regime_never_returns_a_label_outside_the_set():
    """INVERTED, not deleted. This asserted that every input combination — INCLUDING
    (None, None, None) — yields one of the four labels. The real invariant survives: a
    label is never outside the set. The assumption that a verdict always EXISTS does not,
    and it was the thing making the defect look correct.

    Same shape as the four tests #191 introduced that then pinned the yield defect open: a
    guard written around the buggy behaviour reads as coverage and blocks the fix.

    The floor is asserted positively too, so this cannot pass by declining everything —
    see test_the_floor_admits_the_curve_default_once_two_signals_are_present and
    test_all_three_present_is_unchanged_from_the_old_behaviour.
    """
    import itertools
    usrec_vals  = [None, 0.0, 1.0]
    t10y2y_vals = [None, -1.0, -0.3, -0.1, 0.0, 0.5, 2.0]
    unrate_vals = [None, 3.5, 4.0, 4.2, 5.0, 5.5, 6.0, 8.0]
    verdicts = 0
    for u, t, r in itertools.product(usrec_vals, t10y2y_vals, unrate_vals):
        v = classify_regime(u, t, r)
        assert v.label is None or v.label in _REGIME_LABELS, (
            f"classify_regime({u}, {t}, {r}) = {v.label!r} not in {_REGIME_LABELS} and not None"
        )
        verdicts += v.label is not None
    assert verdicts > 100, (
        f"only {verdicts} of 168 combinations produced a verdict — the floor is refusing "
        "far more than it should, and this test would pass while the classifier answered "
        "almost nothing"
    )


def test_classify_regime_recession_overrides_all():
    """USREC = 1 must produce 'Recession' regardless of other signal values."""
    combos = [
        (1.0,  2.0, 3.5),   # would otherwise be Late-cycle (tight labor)
        (1.0, -1.0, 7.0),   # would otherwise be Late-cycle (curve inverted)
        (1.0,  0.0, 5.2),   # would otherwise be Mid-cycle
        (1.0,  1.0, 8.0),   # would otherwise be Early-cycle
    ]
    for u, t, r in combos:
        assert classify_regime(u, t, r).label == "Recession", (
            f"Expected 'Recession' for usrec={u}, t10y2y={t}, unrate={r}"
        )


def test_classify_regime_backtest_has_no_gaps():
    """
    Layer 1: synthetic monthly backtest grid must produce a valid label for every row.
    Uses a synthetic 40-year grid of plausible signal combinations — no FRED calls.
    """
    rng = np.random.default_rng(42)
    n   = 480   # 40 years of monthly data
    usrec_sim  = rng.choice([0.0, 1.0], n, p=[0.85, 0.15])
    t10y2y_sim = rng.normal(0.5, 1.2, n)         # mean slightly positive
    unrate_sim = rng.uniform(3.0, 10.0, n)

    labels = [
        classify_regime(float(usrec_sim[i]), float(t10y2y_sim[i]),
                        float(unrate_sim[i])).label
        for i in range(n)
    ]

    assert len(labels) == n, "Backtest produced fewer labels than input rows"
    assert all(lbl in _REGIME_LABELS for lbl in labels), (
        f"Backtest produced invalid label(s): {set(labels) - set(_REGIME_LABELS)}"
    )
    assert None not in labels, "Backtest produced None labels (gaps)"


# ── format_ur_delta ────────────────────────────────────────────────────────

def test_format_ur_delta_zero_is_flat():
    assert format_ur_delta(0.0) == "Flat vs one year ago"

def test_format_ur_delta_near_zero_rounds_flat():
    # 0.4 bps rounds to 0 → still Flat
    assert format_ur_delta(0.4) == "Flat vs one year ago"
    assert format_ur_delta(-0.4) == "Flat vs one year ago"

def test_format_ur_delta_positive():
    assert format_ur_delta(20.0) == "+20 bps from one year ago"

def test_format_ur_delta_negative():
    assert format_ur_delta(-30.0) == "-30 bps from one year ago"


# ── #Group1: a verdict requires signals (per-branch sufficiency) ─────────────
#
# WHY A FLOOR AT ALL. `Mid-cycle` is the DEFAULT branch, so before this change an empty
# argument list returned a confident mid-cycle verdict, and pages/3_Macro.py rendered it
# as a coloured "Current Regime: Mid-cycle" badge with interpretive prose. Measured: with
# the macro cache emptied and the network blocked, that badge still rendered.
#
# WHY PER-BRANCH RATHER THAN A FLAT n>=1 or n>=2. The branches differ in kind:
#   * Recession reads USREC alone, and one signal is COMPLETE — NBER's indicator is
#     definitionally the answer to "is it a recession", not evidence toward it.
#   * Early/Late/Mid are heuristic combinations that mean nothing at n=1. The deciding
#     case is `curve_ok = t10y2y is None or t10y2y > -0.25`: an absent curve actively
#     supplies half the Early-cycle test, so a missing signal VOTES rather than
#     abstaining. A flat n>=1 floor does not catch that, because one signal is present.

def _v(usrec=None, t10y2y=None, unrate=None):
    return classify_regime(usrec, t10y2y, unrate)


def test_no_signals_yields_no_verdict():
    """The site that motivated the change: a verdict from nothing."""
    assert _v().label is None
    assert _v().present == ()
    assert _v().missing == ("usrec", "t10y2y", "unrate")


def test_recession_needs_only_usrec():
    """One signal, and it is complete rather than partial — USREC settles the question."""
    v = _v(usrec=1.0)
    assert v.label == "Recession"
    assert v.present == ("usrec",)
    assert v.missing == ("t10y2y", "unrate")


def test_a_single_heuristic_signal_is_not_enough():
    """THE DECIDING CASE. unrate=6.0 alone returned Early-cycle before this change,
    because curve_ok defaults True when t10y2y is None — the absent curve supplied half
    the test. One present signal is not a floor; this is why the floor is per-branch."""
    assert _v(unrate=6.0).label is None
    assert _v(t10y2y=-1.0).label is None


def test_usrec_zero_alone_is_not_a_verdict():
    """USREC is definitionally sufficient only for the branch it settles. usrec=0.0 rules
    OUT recession; it says nothing about which non-recession phase, so n=1 is not enough
    for the heuristic branches."""
    assert _v(usrec=0.0).label is None


def test_two_signals_support_a_heuristic_verdict():
    v = _v(usrec=0.0, unrate=6.0)
    assert v.label == "Early-cycle"
    assert v.present == ("usrec", "unrate")
    assert v.missing == ("t10y2y",)


def test_the_floor_admits_the_curve_default_once_two_signals_are_present():
    """Above the floor, `curve_ok`'s neutral default is accepted as the modelling choice
    it is. Asserted so the boundary is explicit rather than incidental: this is the same
    input pattern as test_a_single_heuristic_signal_is_not_enough plus one signal, and it
    flips from no-verdict to a verdict."""
    assert _v(unrate=6.0).label is None
    assert _v(usrec=0.0, unrate=6.0).label == "Early-cycle"


def test_all_three_present_is_unchanged_from_the_old_behaviour():
    """The floor must not perturb the fully-populated case, which is every historical
    backtest point and the normal render. Same expectations as the pre-existing rule
    table."""
    assert _v(1.0, 0.5, 4.0).label == "Recession"
    assert _v(0.0, 0.5, 6.0).label == "Early-cycle"
    assert _v(0.0, -1.0, 5.0).label == "Late-cycle"
    assert _v(0.0, 0.5, 4.0).label == "Late-cycle"      # labor_tight
    assert _v(0.0, 0.5, 5.0).label == "Mid-cycle"


def test_verdict_coverage_is_ordered_and_partitions_the_signals():
    """present + missing must account for all three signals, in a stable order, so a
    caller can render "from 2 of 3" without recomputing what was supplied."""
    for u, t, r in [(None, None, None), (1.0, None, None), (0.0, 0.5, 6.0),
                    (None, 0.5, None), (0.0, None, 6.0)]:
        v = classify_regime(u, t, r)
        assert tuple(sorted(v.present + v.missing)) == ("t10y2y", "unrate", "usrec")
        assert len(v.present) + len(v.missing) == 3
        assert v.present == tuple(n for n in ("usrec", "t10y2y", "unrate")
                                 if dict(usrec=u, t10y2y=t, unrate=r)[n] is not None)


def test_no_verdict_is_not_an_error_state():
    """label=None must be a value the caller inspects, not an exception. app.py imports
    pages unwrapped, and fifteen other panels on this page degrade rather than crash — a
    raise here would take the page down for one badge."""
    v = _v()
    assert v.label is None
    assert isinstance(v.present, tuple) and isinstance(v.missing, tuple)


# ── the page consumers, pinned as SOURCE ─────────────────────────────────────
#
# WHY SOURCE AND NOT EXECUTION. pages/3_Macro.py cannot run in CI: demo mode has no
# populated macro_cache, so every panel would attempt an outbound FRED call, which is
# exactly what @pytest.mark.live_data exists to keep out of the default suite. The
# executing evidence is a local render with the cache emptied and the network blocked
# (socket + prices._SESSION.get) — that render is what found the defect and confirmed the
# fix, and it is recorded in the PR rather than automated here.
#
# These two layers FAIL DIFFERENTLY and both are kept: a source pin catches the
# fabrication being reintroduced by an edit; the live render catches the page as a reader
# meets it. Neither subsumes the other.

def _page_src() -> str:
    """pages/3_Macro.py with COMMENTS STRIPPED.

    Comments are removed because the first cut of these tests asserted
    `"(_cur_usrec or 0)" not in src` and went red on the comment EXPLAINING that the `or 0`
    fallback had been removed. A source assertion that reads comments can be broken by
    prose describing the fix — and, worse, satisfied by prose promising one. Only code
    counts, which is the same rule as "read the assertion, not the comment above it",
    applied to the thing being asserted about.
    """
    import io
    import tokenize
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "pages" / "3_Macro.py").read_text(
        encoding="utf-8")
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type != tokenize.COMMENT:
            out.append(tok)
    return tokenize.untokenize(out)


def test_the_usrec_metric_does_not_map_absence_to_zero():
    """`(_cur_usrec or 0) >= 0.5` rendered "None" — no recession — from an ABSENT
    indicator. Its two neighbours in the same st.columns(3) already rendered "—", so this
    metric was the odd one out among its own siblings.

    Measured before: NBER Recession (USREC) -> "None" with the cache emptied.
    Measured after:  NBER Recession (USREC) -> "—".
    """
    src = _page_src()
    assert "(_cur_usrec or 0)" not in src, (
        "the `or 0` fallback is back: an absent USREC renders as a definitive 'None'"
    )
    assert "_cur_usrec is None" in src, "absence is not distinguished from 0"


def test_the_badge_handles_a_declined_verdict():
    """The classifier can now decline, and the badge must not index a colour map with
    None. _REGIME_COLORS/_REGIME_PROSE are dict lookups keyed on the four labels."""
    src = _page_src()
    assert "_verdict.label is None" in src, "the page does not branch on a declined verdict"
    assert "_REGIME_COLORS[_verdict.label]" in src, (
        "the colour lookup is not on the verdict's label"
    )
    assert "no verdict" in src, "the declined state renders no text of its own"


def test_the_badge_states_what_the_verdict_rests_on():
    """The coverage line renders even when nothing is missing — "Classified from all 3
    signals" is strictly more than a bare label, which is the whole point of returning
    coverage alongside the value rather than only on failure."""
    src = _page_src()
    # ASSERT THE CALL, NOT THE NAME. This asserted `"_coverage_line" in src`, which the
    # helper's own DEFINITION satisfies — a mutant deleting the st.caption() call left the
    # def in place and the test stayed green. The presence of a helper reads as its use,
    # the same way a captured exception variable reads as a handled one.
    assert "st.caption(_coverage_line(_verdict))" in src, (
        "the coverage line is defined but not rendered"
    )
    assert src.count("_coverage_line") >= 2, "definition without a call site"
    assert "of 3 signals" in src


def test_the_two_orphaned_error_variables_now_reach_the_reader():
    """_rec_err and _usrec_err were captured at the load site and never referenced again —
    the only two of fifteen FRED error variables with no consumer. A USREC outage removed
    recession shading from 16 charts and blanked a metric with no reason stated anywhere.

    Asserted by counting occurrences, because a capture alone reads as handling.
    """
    src = _page_src()
    for var in ("_rec_err", "_usrec_err"):
        assert f"_panel_error(" in src and var in src, f"{var} has no consumer"
        assert src.count(var) >= 3, (
            f"{var} appears {src.count(var)}x — capture plus a guard plus a call is the "
            "minimum for it to reach _panel_error"
        )


def test_both_consumers_project_the_verdict_and_there_are_only_two():
    """The contract changed from str to RegimeVerdict, so every call site must either bind
    the verdict or project .label — a leftover string comparison would silently never
    match, and a bare f-string would render "RegimeVerdict(label=...)".

    The count is pinned at two because a third call site appearing without review is how a
    contract change leaks: src/asset_evaluation.py imports classify_regime and never calls
    it (a dead import, filed as #240), so these two are the whole live surface.
    """
    src = _page_src()
    assert src.count("macro.classify_regime(") == 2, (
        f"{src.count('macro.classify_regime(')} call sites, expected 2 — a new consumer "
        "needs its own handling for label=None"
    )
    assert "_verdict = macro.classify_regime(" in src, "the badge does not bind the verdict"
    assert ").label]" in src, "the backtest chart does not project .label"

def test_the_docs_carry_the_floor_argument():
    """docs/regime_classifier.md must keep the reasoning, not just the rule.

    The rule (">= 2 signals for the heuristic branches") is recoverable from the code. The
    ARGUMENT is not: that neutrality has a floor, that Mid-cycle being the default is what
    makes a floor necessary, and that curve_ok's True-when-None means an absent curve VOTES
    rather than abstains — which is why a flat n>=1 floor does not catch it. A future
    reader deciding whether to relax the threshold needs the argument, and the doc is where
    it lives.
    """
    from pathlib import Path

    doc = (Path(__file__).resolve().parent.parent / "docs" /
           "regime_classifier.md").read_text(encoding="utf-8")
    assert "**neutrality has a floor**" in doc, "the sentence carrying the argument is gone"
    assert "curve_ok" in doc, "the deciding case is not named"
    assert "definitionally the answer" in doc, (
        "the per-branch distinction — USREC settles Recession, the others mean nothing at "
        "n=1 — is not stated"
    )
    assert "one of four canonical phases" not in doc, (
        "the Purpose section still claims a label is always one of four; the classifier "
        "can now decline"
    )


# ── Group 2: a percentile declares the window it actually used ───────────────
#
# TWO DEFECTS, NOT ONE, and they need opposite dispositions:
#   * percentile() returned 50.0 for an empty series — a FABRICATED VALUE from nothing.
#     There is no percentile of no observations, so it REFUSES (None).
#   * window_pctile() fell back to the full series when the requested window was empty —
#     a REAL number under a FALSE LABEL. The number is worth keeping; what was missing is
#     the disclosure that the window is not the one asked for.
#
# They compose: an empty window fell through to the full series, and if that was empty too
# the caller got 50.0 with a window label on it.
#
# WHY ONLY window_pctile CARRIES COVERAGE. A percentile has exactly two things to declare
# (how many observations, and which window), and only window_pctile has provenance to
# report. Giving percentile() a coverage tuple would push a NamedTuple through ~24 call
# sites to carry an `n` nobody reads — structure transferring without content.

def test_percentile_refuses_an_empty_series():
    """50.0 is the median rank — the single most plausible-looking wrong answer, and it
    reads as a real measurement. Measured consequence at reports.py: a fabricated 50 maps
    to a "Moderate" allocation stance (see #244)."""
    assert percentile(pd.Series(dtype=float), 5.0) is None
    assert percentile(pd.Series([np.nan, np.nan]), 5.0) is None


def test_percentile_is_unchanged_on_a_populated_series():
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    assert percentile(s, 3.0) == pytest.approx(75.0)
    assert percentile(s, 0.5) == pytest.approx(0.0)
    assert percentile(s, 9.0) == pytest.approx(100.0)


def test_window_pctile_reports_the_window_it_used():
    """The load-bearing case. A series ending before the window start yields an empty
    window; the value is the full-series percentile and fell_back says so."""
    idx = pd.date_range("2000-01-01", periods=120, freq="MS")
    s = pd.Series(np.linspace(1.0, 10.0, 120), index=idx)

    got = window_pctile(s, 5.0, "2020-01-01")          # window starts after the data ends
    assert got.fell_back is True
    assert got.n == 120, "n must be the observations actually used, i.e. the full series"
    assert got.value == pytest.approx(percentile(s, 5.0))


def test_window_pctile_does_not_flag_a_populated_window():
    idx = pd.date_range("2000-01-01", periods=120, freq="MS")
    s = pd.Series(np.linspace(1.0, 10.0, 120), index=idx)

    got = window_pctile(s, 5.0, "2005-01-01")
    assert got.fell_back is False
    assert 0 < got.n < 120, "n must be the WINDOWED count, not the full series"
    assert got.value == pytest.approx(percentile(s.loc["2005-01-01":], 5.0))


def test_the_max_sentinel_is_not_a_fallback():
    """'1800-01-01' means "use everything" BY DESIGN, so "fell back" and "did what was
    asked" are the same thing and there is nothing to disclose. Flagging it would train the
    reader to ignore the marker on the two panels where it fires every single render."""
    idx = pd.date_range("2000-01-01", periods=50, freq="MS")
    s = pd.Series(np.linspace(1.0, 5.0, 50), index=idx)

    got = window_pctile(s, 3.0, "1800-01-01")
    assert got.fell_back is False, "the Max sentinel must not read as a fallback"
    assert got.n == 50


def test_window_pctile_refuses_when_even_the_full_series_is_empty():
    """The composition of the two defects: empty window -> full series -> also empty. The
    old code returned 50.0 here, with a window label attached."""
    empty = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    got = window_pctile(empty, 5.0, "2020-01-01")
    assert got.value is None
    assert got.n == 0


def test_window_pctile_n_is_the_basis_of_the_number_not_the_series_length():
    """n exists so a caller can say "over 816 observations" truthfully. If it reported the
    series length rather than the slice actually used, the disclosure would be wrong in the
    non-fallback case — the case that renders on every normal load."""
    idx = pd.date_range("1990-01-01", periods=400, freq="MS")
    s = pd.Series(np.arange(400.0), index=idx)
    got = window_pctile(s, 100.0, "2020-01-01")
    windowed = s.loc["2020-01-01":].dropna()
    assert got.n == len(windowed)
    assert got.n != len(s)


def test_the_wrapper_docstrings_do_not_promise_a_number_that_may_be_absent():
    """factor_percentile and valuation_percentile are pure delegation to macro.percentile,
    and both said "Historical percentile rank (0-100)" — a claim None falsifies. They are
    the reason a contract change here reaches pages/1_SAA.py and page 3's factor panels."""
    import src.factor_regime as fr
    import src.factor_valuation as fv

    for fn in (fr.factor_percentile, fv.valuation_percentile):
        doc = fn.__doc__ or ""
        # ASSERT THE CLAIM, not the word. This asserted `"None" in doc`, which the
        # EXPLANATORY paragraph below the claim also satisfies — a mutant that replaced the
        # return-value sentence with "always a number." left the word "None" elsewhere in
        # the docstring and the test stayed green (P12). Same shape as a helper's
        # definition satisfying an assertion about its use.
        assert "or **None** when" in doc, (
            f"{fn.__name__}'s docstring no longer states that the RETURN may be None, "
            "while it delegates to a function that now can return None"
        )


def test_every_selectable_window_site_renders_a_scope_note():
    """COMPLETENESS, asserted rather than assumed. The wiring pass was mechanical and it
    MISSED ONE site — `ecy_pctile_w  = ...` had two spaces before the `=` where the pattern
    expected one, so 12 of 13 were wired and the run reported success. A transform that
    silently covers less than it claims is the same defect class as a test that does.

    The rule: a site whose window is USER-SELECTABLE must have a scope note. The three
    credit-spread panels hardcode w_start to the Max sentinel and have no window control,
    so they cannot fall back and must NOT be wired — asserted as a negative below, so this
    test cannot be satisfied by wiring everything indiscriminately.
    """
    import re

    src = _page_src()

    selectable, sentinel_only = [], []
    for m in re.finditer(r"_(\w+)_pct = _window_pctile\((\w+), [\w\.]+, (\w+)\)", src):
        selectable.append((m.group(1), m.group(3), m.group(2)))
    for m in re.finditer(r"(\w+)_pctile_w = _window_pctile\((\w+), [\w\.]+, (\w+)\)", src):
        sentinel_only.append((m.group(1), m.group(3)))

    assert len(selectable) == 13, (
        f"expected 13 selectable-window percentile sites, found {len(selectable)}: "
        f"{[n for n, _, _ in selectable]}"
    )
    for name, start, series in selectable:
        assert f"_pctile_scope_note(_{name}_pct, " in src, (
            f"{name} unpacks the verdict but renders no scope note"
        )
        assert f"{name}_pctile_w = _{name}_pct.value" in src, (
            f"{name} does not project .value into the name its captions use"
        )

    assert sorted(n for n, _ in sentinel_only) == ["ccc", "hy", "ig"], (
        f"the un-wired sites should be exactly the three hardcoded-Max credit panels, "
        f"got {sorted(n for n, _ in sentinel_only)}"
    )
    for name, start in sentinel_only:
        assert re.search(rf'{start}\s*=\s*"1800-01-01"', src), (
            f"{name} is not wired for a scope note but its window is not the Max "
            f"sentinel either — it can fall back silently"
        )


def test_the_scope_note_is_silent_unless_the_window_was_empty():
    """A disclosure that always warns teaches the reader to skip it — this page's own
    docstring rule. Asserted on the helper's source because the guard is the first thing a
    later edit would drop."""
    src = _page_src()
    i = src.index("def _pctile_scope_note")
    body = src[i:i + 1400]
    assert "if pct is None or not pct.fell_back:" in body and "return" in body, (
        "the scope note lost its early return, so it now fires on every render"
    )


def test_no_percentile_caption_references_a_raw_window_label():
    """A marker that CONTRADICTS the caption beside it gives the reader two claims and no
    basis for choosing. The whole argument for calling this fabricated provenance was that
    the label is load-bearing, so the label itself has to become conditional — a false
    label with a footnote keeps the defect and adds prose.

    Asserted as a NEGATIVE over every percentile caption: no f-string that renders a
    windowed percentile may interpolate the raw window variable, only the derived
    `*_scope` / `*_scope_short` phrases which collapse to "the full series" on a fallback.
    """
    import re

    src = _page_src()
    offenders = []
    for line in src.splitlines():
        if "_pctile_w" not in line:
            continue
        if re.search(r"\{_?\w*_window\}", line):
            offenders.append(line.strip()[:100])
    assert not offenders, (
        "these percentile captions still interpolate the window the reader SELECTED "
        f"rather than the basis actually used: {offenders}"
    )


def test_every_wired_panel_derives_both_scope_phrases():
    """Count-asserted, because the caption pass is mechanical and a miss is silent — the
    wiring pass before it reported success having covered 12 of 13 sites (a two-space
    alignment defeated the pattern). A presence assertion would pass on one panel."""
    src = _page_src()
    n_scope = src.count('_scope = (f"the ')
    n_short = src.count("_scope_short = (")
    assert n_scope == 13, f"{n_scope} panels derive a scope phrase, expected 13"
    assert n_short == 13, f"{n_short} panels derive a short scope phrase, expected 13"
    assert 'else "the full series")' in src and 'else "full series")' in src
