"""Tests for report-level logic in src/reports.py."""
import sys
import pathlib
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import src.reports as rpt
from src.reports import (
    DEMO_ACCOUNT_LABEL,
    DEMO_REPORT_DISCLAIMER,
    REAL_ACCOUNT_LABEL,
    REPORT_DISCLAIMER,
    _account_label,
    _build_cumulative_chart,
    _build_holdings_chart,
    _drift_status,
    _fmt_bps,
    _fmt_pct,
    _report_disclaimer,
    build_bf_cross_reference,
)
from src.returns import twr_daily_linked

# ── shared fixtures ──────────────────────────────────────────────────────────

_SLEEVES  = ["US Large Core", "International Developed", "Cash / SPAXX"]
_ACTUALS  = [16.0, 19.0, 3.0]
_TARGETS  = [16.0, 19.0, 3.0]

_DATES    = pd.date_range("2025-05-01", periods=60)
_PV_PCT   = pd.Series(np.linspace(0.0, 27.0, 60), index=_DATES)
_SP_PCT   = pd.Series(np.linspace(0.0, 30.0, 60), index=_DATES)
_BL_PCT   = pd.Series(np.linspace(0.0, 26.0, 60), index=_DATES)


# ── _drift_status boundary cases ────────────────────────────────────────────

def test_drift_status_both_under_thresholds():
    """190 bps drift on 1000 bps target (19% relative) → Within (both under)."""
    assert _drift_status(190, 1000) == "Within"


def test_drift_status_absolute_just_over():
    """210 bps drift on 1600 bps target (13.1% relative) → Drift (absolute threshold breached)."""
    assert _drift_status(210, 1600) == "Drift"


def test_drift_status_relative_just_over():
    """95 bps drift on 400 bps target (23.75% relative) → Drift (relative threshold breached)."""
    assert _drift_status(95, 400) == "Drift"


def test_drift_status_both_over():
    """250 bps drift on 300 bps target (83% relative) → Drift (both thresholds breached)."""
    assert _drift_status(250, 300) == "Drift"


def test_drift_status_negative_drift():
    """Negative drift uses abs() — -190 bps on 1000 bps target → Within."""
    assert _drift_status(-190, 1000) == "Within"


def test_drift_status_negative_drift_over():
    """-250 bps on 300 bps target → Drift (both breached)."""
    assert _drift_status(-250, 300) == "Drift"


def test_drift_status_cash_spaxx_scenario():
    """Cash/SPAXX: +291 bps drift, 300 bps target → Drift (absolute AND relative breached)."""
    assert _drift_status(291, 300) == "Drift"


def test_drift_status_us_large_core_scenario():
    """US Large Core: -58 bps drift, 1600 bps target (3.6% relative) → Within."""
    assert _drift_status(-58, 1600) == "Within"


def test_drift_status_zero_target_is_drift():
    """Zero target weight is treated as Drift to avoid division by zero."""
    assert _drift_status(10, 0) == "Drift"


def test_drift_status_exactly_at_absolute_boundary():
    """Exactly 200 bps drift is still Within (boundary is inclusive)."""
    assert _drift_status(200, 2000) == "Within"


def test_drift_status_exactly_at_relative_boundary():
    """Exactly 20% relative drift is still Within (boundary is inclusive)."""
    # 200 bps drift, 1000 bps target = 20.0% exactly
    assert _drift_status(200, 1000) == "Within"


# ── NaN-safe formatting sinks (PR A: reference-benchmark gaps) ──────────────
# A totally-unpriceable naive/blended reference benchmark (src/benchmarks.py)
# carries a NaN sentinel through to these formatters — they must render "—",
# never the literal string "nan%"/"nan bps".

def test_fmt_pct_renders_em_dash_for_nan():
    assert _fmt_pct(float("nan")) == "—"


def test_fmt_pct_renders_normally_for_real_value():
    assert _fmt_pct(0.1234) == "12.34%"


def test_fmt_bps_renders_em_dash_for_nan():
    assert _fmt_bps(float("nan")) == "—"


def test_fmt_bps_renders_normally_for_real_value():
    assert _fmt_bps(123.4) == "+123 bps"
    assert _fmt_bps(-56.7) == "-57 bps"


# ── chart figure config tests ────────────────────────────────────────────────

def test_holdings_chart_has_sleeve_labels():
    """Holdings chart must carry tickvals + ticktext for every sleeve."""
    fig = _build_holdings_chart(_SLEEVES, _ACTUALS, _TARGETS)
    yaxis = fig.layout.yaxis
    assert yaxis.tickmode == "array"
    assert yaxis.tickvals is not None
    assert yaxis.ticktext is not None
    assert len(yaxis.tickvals) == len(_SLEEVES)
    for sleeve in _SLEEVES:
        assert sleeve in yaxis.tickvals


def test_cumulative_return_chart_has_y_tick_values():
    """Cumulative return chart must carry explicit tickvals with % ticktext."""
    fig = _build_cumulative_chart(_PV_PCT, _SP_PCT, _BL_PCT)
    yaxis = fig.layout.yaxis
    assert yaxis.tickvals is not None, "tickvals must be set (kaleido 0.2.1 ignores ticksuffix)"
    assert yaxis.ticktext is not None
    assert len(yaxis.tickvals) == len(yaxis.ticktext)
    assert all("%" in t for t in yaxis.ticktext)


def test_cumulative_return_chart_does_not_swallow_errors():
    """Empty input must raise, not silently return an empty chart."""
    with pytest.raises(Exception):
        _build_cumulative_chart(
            pd.Series(dtype=float),
            pd.Series(dtype=float),
            pd.Series(dtype=float),
        )


# ── _build_executive_summary cover TWR fix ───────────────────────────────────

def _flat_series(start: str, end: str, value: float = 1.0) -> pd.Series:
    return pd.Series(value, index=pd.date_range(start, end, freq="D"))


def test_cover_twr_uses_inception_slice_not_period_scoped():
    """
    portfolio_return_pct must equal twr_daily_linked on the inception series
    sliced to the report period — not a separate period-scoped call whose
    v_start can be $0 on New Year's Day.
    """
    inception  = "2025-05-01"
    start_date = "2026-01-01"
    end_date   = "2026-03-31"

    idx = pd.date_range(inception, end_date, freq="D")
    pv_full = pd.Series(
        1000.0 + (idx - idx[0]).days.astype(float) * (300.0 / (len(idx) - 1)),
        index=idx,
    )
    pv_period    = pv_full[pv_full.index >= pd.Timestamp(start_date)]
    expected_pct = twr_daily_linked(
        pv_period, pd.Series(0.0, index=pv_period.index)
    ) * 100

    sp_flat = _flat_series(inception, end_date)
    bl_flat = _flat_series(inception, end_date)

    with patch.object(rpt, "get_portfolio_value_series", return_value=pv_full), \
         patch.object(rpt, "get_portfolio_account_id", return_value=1), \
         patch.object(rpt, "get_external_cashflow_series",
                      side_effect=lambda s, e, account_id=None: pd.Series(dtype=float)), \
         patch.object(rpt, "get_inception_date", return_value=inception), \
         patch.object(rpt, "get_sp500_series", return_value=sp_flat), \
         patch.object(rpt, "get_custom_blended_series", return_value=bl_flat), \
         patch.object(rpt, "brinson_fachler_period", side_effect=Exception("no db")), \
         patch.object(rpt, "current_cape", side_effect=Exception("no data")), \
         patch.object(rpt, "get_cape_series", side_effect=Exception("no data")):

        result = rpt._build_executive_summary(start_date, end_date)

    actual_pct = float(result["portfolio_return_pct"].rstrip("%"))
    assert abs(actual_pct - expected_pct) < 0.01, (
        f"Cover TWR {actual_pct:.4f}% should match period-sliced inception "
        f"TWR {expected_pct:.4f}%"
    )


def test_cover_twr_nets_external_contribution_not_counted_as_return():
    """
    THE CODE-GAP 5 BUG PROOF at the caller: a $200 mid-period contribution
    steps the value series from $1,000 to $1,200 with a flat market, so the
    true TWR is 0%. Pre-fix reports.py hardcoded cf = pd.Series(0.0, ...) —
    flow-blind — and reported the deposit as a +20% return; against pre-fix
    code this test fails with 20.0 != 0.0.
    """
    inception  = "2025-05-01"
    start_date = "2026-01-01"
    end_date   = "2026-03-31"

    idx     = pd.date_range(inception, end_date, freq="D")
    pv_full = pd.Series(1000.0, index=idx)
    flow_day = pd.Timestamp("2026-02-15")
    pv_full.loc[pv_full.index >= flow_day] = 1200.0
    cf_full = pd.Series(0.0, index=idx)
    cf_full.loc[flow_day] = 200.0

    sp_flat = _flat_series(inception, end_date)
    bl_flat = _flat_series(inception, end_date)

    with patch.object(rpt, "get_portfolio_value_series", return_value=pv_full), \
         patch.object(rpt, "get_portfolio_account_id", return_value=1), \
         patch.object(rpt, "get_external_cashflow_series",
                      side_effect=lambda s, e, account_id=None: cf_full), \
         patch.object(rpt, "get_current_market_value", return_value=1200.0), \
         patch.object(rpt, "get_inception_date", return_value=inception), \
         patch.object(rpt, "get_sp500_series", return_value=sp_flat), \
         patch.object(rpt, "get_custom_blended_series", return_value=bl_flat), \
         patch.object(rpt, "brinson_fachler_period", side_effect=Exception("no db")), \
         patch.object(rpt, "current_cape", side_effect=Exception("no data")), \
         patch.object(rpt, "get_cape_series", side_effect=Exception("no data")):

        result = rpt._build_executive_summary(start_date, end_date)

    actual_pct = float(result["portfolio_return_pct"].rstrip("%"))
    assert abs(actual_pct) < 0.01, (
        f"Contribution counted as return: cover TWR is {actual_pct:.4f}%, "
        "expected 0% — the $200 deposit must be netted out, not credited."
    )


def test_cover_current_value_is_true_mv_not_return_series_endpoint():
    """current_value must be the TRUE current MV (all shares incl DRIP × raw close,
    from get_current_market_value), NOT the total-return series endpoint (adj_close
    × non-DRIP shares). The two differ by the DRIP shares' market worth: here the
    return series ends at $1,200 but the true MV is $1,234, so the cover must show
    $1,234 — the dollar value counts the real DRIP shares the return series omits.
    """
    inception  = "2025-05-01"
    start_date = "2026-01-01"
    end_date   = "2026-03-31"

    idx     = pd.date_range(inception, end_date, freq="D")
    pv_full = pd.Series(1200.0, index=idx)   # total-return series (adj_close × non-DRIP)

    sp_flat = _flat_series(inception, end_date)
    bl_flat = _flat_series(inception, end_date)

    with patch.object(rpt, "get_portfolio_value_series", return_value=pv_full), \
         patch.object(rpt, "get_portfolio_account_id", return_value=1), \
         patch.object(rpt, "get_external_cashflow_series",
                      side_effect=lambda s, e, account_id=None: pd.Series(dtype=float)), \
         patch.object(rpt, "get_current_market_value", return_value=1234.0), \
         patch.object(rpt, "get_inception_date", return_value=inception), \
         patch.object(rpt, "get_sp500_series", return_value=sp_flat), \
         patch.object(rpt, "get_custom_blended_series", return_value=bl_flat), \
         patch.object(rpt, "brinson_fachler_period", side_effect=Exception("no db")), \
         patch.object(rpt, "current_cape", side_effect=Exception("no data")), \
         patch.object(rpt, "get_cape_series", side_effect=Exception("no data")):

        result = rpt._build_executive_summary(start_date, end_date)

    assert result["current_value"] == "$1,234"   # true MV, not the $1,200 return endpoint


# ── Benchmark reconciliation: cover card vs performance table ─────────────────

def _make_exec_summary_mocks(inception: str, start_date: str, end_date: str,
                             pv_full: pd.Series, sp_full: pd.Series,
                             bl_full: pd.Series) -> dict:
    """Call _build_executive_summary with inception-scoped mocks.

    get_custom_blended_series uses side_effect so the mock slices bl_full to
    the requested start date, matching the production code path that calls
    get_custom_blended_series(start_date, end_date) and uses the full returned
    series for the period return.
    """
    def _blended_side_effect(start: str, end: str) -> pd.Series:
        return bl_full[bl_full.index >= pd.Timestamp(start)]

    with patch.object(rpt, "get_portfolio_value_series", return_value=pv_full), \
         patch.object(rpt, "get_portfolio_account_id", return_value=1), \
         patch.object(rpt, "get_external_cashflow_series",
                      side_effect=lambda s, e, account_id=None: pd.Series(dtype=float)), \
         patch.object(rpt, "get_inception_date", return_value=inception), \
         patch.object(rpt, "get_sp500_series", return_value=sp_full), \
         patch.object(rpt, "get_custom_blended_series", side_effect=_blended_side_effect), \
         patch.object(rpt, "brinson_fachler_period", side_effect=Exception("no db")), \
         patch.object(rpt, "current_cape", side_effect=Exception("no data")), \
         patch.object(rpt, "get_cape_series", side_effect=Exception("no data")):
        return rpt._build_executive_summary(start_date, end_date)


def test_cover_narrative_degrades_on_total_reference_benchmark_gap():
    """PR A: when get_sp500_series/get_custom_blended_series both return a
    NaN-sentineled series (a total reference-benchmark data gap — see
    src/benchmarks.py), the cover narrative and formatted strings must degrade
    to explicit "unavailable"/"—" text, never a fabricated number or the
    literal string "nan bps"/"nan%" anywhere.

    PRE-FIX: sp_return/bl_return/alpha_*_bps were NEVER NaN in the first place
    (get_sp500_series/get_custom_blended_series always fabricated a flat
    return on a gap) — this pins the NEW degrade-gracefully contract now that
    a genuine NaN sentinel can reach this function.
    """
    inception  = "2025-05-01"
    start_date = "2026-01-01"
    end_date   = "2026-03-31"

    idx     = pd.date_range(inception, end_date, freq="D")
    pv_full = pd.Series(1000.0 + (idx - idx[0]).days.astype(float), index=idx)

    nan_sp = pd.Series(float("nan"), index=idx)
    nan_sp.attrs["benchmark_gaps"] = [("S&P 500", "SPY", "2026-03-31")]
    nan_bl = pd.Series(float("nan"), index=idx)
    nan_bl.attrs["benchmark_gaps"] = [("US Large Core", "SPY", "2026-03-31")]

    with patch.object(rpt, "get_portfolio_value_series", return_value=pv_full), \
         patch.object(rpt, "get_portfolio_account_id", return_value=1), \
         patch.object(rpt, "get_external_cashflow_series",
                      side_effect=lambda s, e, account_id=None: pd.Series(dtype=float)), \
         patch.object(rpt, "get_current_market_value", return_value=1200.0), \
         patch.object(rpt, "get_inception_date", return_value=inception), \
         patch.object(rpt, "get_sp500_series", return_value=nan_sp), \
         patch.object(rpt, "get_custom_blended_series", return_value=nan_bl), \
         patch.object(rpt, "brinson_fachler_period", side_effect=Exception("no db")), \
         patch.object(rpt, "current_cape", side_effect=Exception("no data")), \
         patch.object(rpt, "get_cape_series", side_effect=Exception("no data")):
        result = rpt._build_executive_summary(start_date, end_date)

    assert result["sp500_return_pct"] == "—"
    assert result["blended_return_pct"] == "—"
    assert result["alpha_sp_str"] == "—"
    assert result["alpha_bl_str"] == "—"
    assert result["alpha_sp_bps"] == 0.0, "numeric field must be neutralized, not NaN"
    assert result["alpha_bl_bps"] == 0.0, "numeric field must be neutralized, not NaN"

    full_narrative = " ".join(result["narrative"]).lower()
    assert "nan" not in full_narrative, (
        f"narrative leaked a literal 'nan': {result['narrative']}"
    )
    assert "unavailable" in full_narrative, (
        f"expected an explicit unavailable clause, got: {result['narrative']}"
    )
    assert any("SPY" in s for s in result["narrative"]), (
        f"expected the shared benchmark_gap_notice naming the gapped ticker, "
        f"got: {result['narrative']}"
    )


def test_cover_sp500_return_uses_inception_slice():
    """
    Cover card sp500_return_pct must equal the sliced-inception return, not a
    period-scoped return that back-fills New Year's Day from Jan 2 instead of
    forward-filling from Dec 31.
    """
    inception  = "2025-05-01"
    start_date = "2026-01-01"
    end_date   = "2026-03-31"

    idx     = pd.date_range(inception, end_date, freq="D")
    pv_full = pd.Series(1000.0, index=idx)
    # SPY grows 0.05% per day from inception — Dec 31 and Jan 2 are distinct values
    sp_full = pd.Series(
        1.0 + (idx - idx[0]).days.astype(float) * 0.0005, index=idx
    )
    bl_full = _flat_series(inception, end_date)

    # Expected: return computed from the inception series sliced to >= start_date
    sp_period     = sp_full[sp_full.index >= pd.Timestamp(start_date)]
    expected_sp   = float(sp_period.iloc[-1] / sp_period.iloc[0] - 1) * 100

    result     = _make_exec_summary_mocks(inception, start_date, end_date,
                                          pv_full, sp_full, bl_full)
    actual_sp  = float(result["sp500_return_pct"].rstrip("%"))

    assert abs(actual_sp - expected_sp) < 0.01, (
        f"Cover SP500 {actual_sp:.4f}% should match inception-sliced "
        f"{expected_sp:.4f}% (gap >{abs(actual_sp - expected_sp):.4f} pp)"
    )


def test_cover_blended_return_uses_period_start():
    """Cover card blended_return_pct must use period-start blended return (fresh start_date, not inception-sliced)."""
    inception  = "2025-05-01"
    start_date = "2026-01-01"
    end_date   = "2026-03-31"

    idx     = pd.date_range(inception, end_date, freq="D")
    pv_full = pd.Series(1000.0, index=idx)
    sp_full = _flat_series(inception, end_date)
    bl_full = pd.Series(
        1.0 + (idx - idx[0]).days.astype(float) * 0.0003, index=idx
    )

    bl_period   = bl_full[bl_full.index >= pd.Timestamp(start_date)]
    expected_bl = float(bl_period.iloc[-1] / bl_period.iloc[0] - 1) * 100

    result     = _make_exec_summary_mocks(inception, start_date, end_date,
                                          pv_full, sp_full, bl_full)
    actual_bl  = float(result["blended_return_pct"].rstrip("%"))

    assert abs(actual_bl - expected_bl) < 0.01, (
        f"Cover blended {actual_bl:.4f}% should match inception-sliced "
        f"{expected_bl:.4f}%"
    )


def test_cover_alpha_bps_are_arithmetic_of_cover_returns():
    """
    alpha_sp_str and alpha_bl_str must be arithmetically self-consistent
    with the cover's own portfolio, SP500, and blended return figures —
    not derived from a different benchmark series.
    """
    inception  = "2025-05-01"
    start_date = "2026-01-01"
    end_date   = "2026-03-31"

    idx     = pd.date_range(inception, end_date, freq="D")
    pv_full = pd.Series(
        1000.0 + (idx - idx[0]).days.astype(float) * 0.3, index=idx
    )
    sp_full = pd.Series(
        1.0 + (idx - idx[0]).days.astype(float) * 0.0005, index=idx
    )
    bl_full = pd.Series(
        1.0 + (idx - idx[0]).days.astype(float) * 0.0003, index=idx
    )

    result = _make_exec_summary_mocks(inception, start_date, end_date,
                                      pv_full, sp_full, bl_full)

    port_pct = float(result["portfolio_return_pct"].rstrip("%"))
    sp_pct   = float(result["sp500_return_pct"].rstrip("%"))
    bl_pct   = float(result["blended_return_pct"].rstrip("%"))
    alpha_sp = float(result["alpha_sp_str"].rstrip(" bps").replace("+", ""))
    alpha_bl = float(result["alpha_bl_str"].rstrip(" bps").replace("+", ""))

    # Tolerance of 2 bps covers the ±0.01pp rounding in the display strings
    assert abs(alpha_sp - (port_pct - sp_pct) * 100) < 2.0, (
        "alpha_sp_str is not arithmetic of cover portfolio and SP500 returns"
    )
    assert abs(alpha_bl - (port_pct - bl_pct) * 100) < 2.0, (
        "alpha_bl_str is not arithmetic of cover portfolio and blended returns"
    )


def test_build_bf_cross_reference_uses_raw_diff_not_selection_effect():
    """sel_bps must be the raw return differential (r_p - r_b), not the
    portfolio-weighted selection_effect (w_p * (r_p - r_b)).

    This is the single source used by BOTH src.reports._build_benchmark_section
    (PDF) and pages/6_Benchmark_Attribution.py (Streamlit) so the two prose
    surfaces can't diverge again — a prior version of the Streamlit page fed
    selection_effect into the same "outperformed by N bps" template, understating
    the displayed differential by a factor of ~1/w_p.
    """
    bf_df = pd.DataFrame([
        # w_p is small (8%) so selection_effect and raw_diff differ sharply —
        # any regression that swaps raw_diff back for selection_effect fails loudly.
        {"sleeve": "US Small Cap", "r_p": 0.15, "r_b": 0.07, "selection_effect": 0.08 * 0.08},
        {"sleeve": "Emerging Markets", "r_p": 0.05, "r_b": 0.05, "selection_effect": 0.0},
        {"sleeve": "TIPS", "r_p": 0.02, "r_b": 0.03, "selection_effect": 0.06 * -0.01},
    ])

    top = build_bf_cross_reference(bf_df, n=3)

    small_cap = next(item for item in top if item["holding"] == "AVUV")
    # raw_diff = 0.15 - 0.07 = 0.08 -> 800 bps. The weight-scaled selection_effect
    # (0.08 * 0.08 = 0.0064 -> 64 bps) must NOT be what's reported.
    assert abs(small_cap["sel_bps"] - 800.0) < 1e-6, (
        f"Expected raw_diff-based sel_bps of 800, got {small_cap['sel_bps']} — "
        "sel_bps must be r_p - r_b, not the weight-scaled selection_effect."
    )


def test_build_bf_cross_reference_empty_df_returns_empty_list():
    """An empty BF frame (e.g. no holdings data) must return [], not None or raise."""
    assert build_bf_cross_reference(pd.DataFrame()) == []


# ── Attribution-sink prose: exclude no_benchmark_sleeves from rankings ───────
# A sleeve with w_b == 0 (attrs['no_benchmark_sleeves']) carries a placeholder
# r_b == 0.0 that cancels algebraically in the decomposition math, but is not
# a real market return — a comparative "X outperformed Y by N bps" sentence
# built from it is fabricated. These sleeves belong in the full table (labeled
# N/A), never in a top-N prose ranking.

def test_build_bf_cross_reference_excludes_no_benchmark_sleeves():
    """A sleeve flagged in attrs['no_benchmark_sleeves'] must be excluded from
    the ranking, not ranked against its fabricated r_b=0.0.

    PROVES the pre-fix bug: pre-fix, "Unknown"'s selection_effect (0.20*0.30 =
    0.06) towers over the mapped sleeve's (0.08*0.08 = 0.0064) and it ranks
    #1 — rendering "... outperformed Unknown by 3000 bps", a fabricated
    comparison against a sleeve with no real benchmark.
    """
    bf_df = pd.DataFrame([
        {"sleeve": "US Small Cap", "r_p": 0.15, "r_b": 0.07, "selection_effect": 0.08 * 0.08},
        {"sleeve": "Unknown",      "r_p": 0.30, "r_b": 0.0,  "selection_effect": 0.20 * 0.30},
    ])
    bf_df.attrs["no_benchmark_sleeves"] = ["Unknown"]

    top = build_bf_cross_reference(bf_df, n=3)

    assert all(item["holding"] != "Unknown" for item in top), (
        f"'Unknown' (no_benchmark_sleeves) leaked into the ranking: {top}"
    )


def test_attribution_section_prose_excludes_no_benchmark_sleeves():
    """sel_commentary and alloc_commentary (the PDF/page prose) must exclude
    a no_benchmark_sleeves sleeve, same as build_bf_cross_reference.

    PROVES the pre-fix bug: pre-fix, "Unknown" has both the largest
    |selection_effect| and |allocation_effect| here and is described in the
    prose against its fabricated r_b=0.0 / w_b=0.0 benchmark.
    """
    bf_df = pd.DataFrame([
        {"sleeve": "US Small Cap", "w_p": 0.08, "w_b": 0.07,
         "r_p": 0.15, "r_b": 0.07,
         "allocation_effect": 0.001, "selection_effect": 0.08 * 0.08, "total_effect": 0.0074},
        {"sleeve": "Unknown", "w_p": 0.20, "w_b": 0.0,
         "r_p": 0.30, "r_b": 0.0,
         "allocation_effect": 0.20 * 0.30, "selection_effect": 0.20 * 0.30, "total_effect": 0.12},
    ])
    bf_df.attrs["no_benchmark_sleeves"] = ["Unknown"]
    bf_df.attrs["price_gaps"] = []
    bf_df.attrs["benchmark_gaps"] = []
    bf_df.attrs["cash_drag"] = 0.0

    with patch.object(rpt, "brinson_fachler_period", return_value=bf_df), \
         patch.object(rpt, "_chart_b64", return_value=None):
        result = rpt._build_attribution_section("2026-01-01", "2026-03-31")

    assert not any("Unknown" in c for c in result["sel_commentary"]), (
        f"'Unknown' leaked into sel_commentary: {result['sel_commentary']}"
    )
    assert result["alloc_commentary"] is None or "Unknown" not in result["alloc_commentary"], (
        f"'Unknown' leaked into alloc_commentary: {result['alloc_commentary']}"
    )


def test_benchmark_attribution_page_uses_shared_bf_cross_reference():
    """pages/6_Benchmark_Attribution.py must import build_bf_cross_reference from
    src.reports rather than re-deriving the top-N selection-effect list inline —
    a guard against the two prose surfaces silently re-diverging (see the
    raw_diff test above).
    """
    page_path = pathlib.Path(__file__).resolve().parent.parent / "pages" / "6_Benchmark_Attribution.py"
    source = page_path.read_text(encoding="utf-8")
    assert "from src.reports import build_bf_cross_reference" in source, (
        "pages/6_Benchmark_Attribution.py no longer imports the shared "
        "build_bf_cross_reference helper — check it hasn't reverted to an "
        "independent (and possibly selection_effect-based) inline implementation."
    )


# ── CODE-GAP 3: PDF demo-mode account-status labeling ───────────────────────
# The quarterly PDF must never assert real-account status ("Personal Brokerage
# Account") when generated from the public demo. These tests pin the mode
# branch directly (no full PDF render — that requires a PDF backend and DB).

def test_account_label_demo_mode_is_not_real_account_text():
    label = _account_label(is_demo=True)
    assert label == DEMO_ACCOUNT_LABEL
    assert label != REAL_ACCOUNT_LABEL
    assert "Personal Brokerage Account" not in label


def test_account_label_personal_mode_matches_existing_real_text():
    assert _account_label(is_demo=False) == REAL_ACCOUNT_LABEL == "Personal Brokerage Account"


def test_report_disclaimer_demo_mode_flags_simulated_portfolio():
    disclaimer = _report_disclaimer(is_demo=True)
    assert disclaimer == DEMO_REPORT_DISCLAIMER
    assert "simulated paper-trade demo portfolio" in disclaimer
    assert "the author's personal investment portfolio" not in disclaimer


def test_report_disclaimer_personal_mode_unchanged():
    assert _report_disclaimer(is_demo=False) == REPORT_DISCLAIMER


def test_generate_quarterly_report_is_demo_param_defaults_from_config():
    """`is_demo` must default to src.config.IS_DEMO (None sentinel resolved at
    call time), not a hardcoded False — otherwise a demo deployment would still
    render real-account labeling unless every call site passed it explicitly."""
    import inspect
    sig = inspect.signature(rpt.generate_quarterly_report)
    assert sig.parameters["is_demo"].default is None
    source = inspect.getsource(rpt.generate_quarterly_report)
    assert "if is_demo is None:" in source and "is_demo = IS_DEMO" in source


def test_template_cover_sub_id_is_mode_aware_not_hardcoded():
    """templates/quarterly_report.html must render cover_sub_id as a template
    variable so the PDF path can label demo reports as illustrative — a
    hardcoded 'Personal Brokerage Account' literal would assert real-account
    status regardless of TRACKER_MODE."""
    tmpl_path = (
        pathlib.Path(__file__).resolve().parent.parent / "templates" / "quarterly_report.html"
    )
    source = tmpl_path.read_text(encoding="utf-8")
    assert "{{ cover_sub_id }}" in source
    assert '<p class="cover-sub-id">Personal Brokerage Account</p>' not in source


# ── Audit #6: autoescape + session-scoped (in-memory) PDF generation ──────────

def test_report_jinja_env_autoescape_on_and_escapes_user_input():
    """The PDF Jinja env must autoescape, so a visitor-supplied recipient name (or
    any ticker / thesis / fund string) cannot inject markup into the report HTML."""
    env = rpt._make_report_env()
    assert env.autoescape is True
    rendered = env.from_string("{{ v }}").render(v="<script>alert(1)</script>")
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_report_template_marks_css_safe_so_autoescape_keeps_styling():
    """With autoescape on, the trusted stylesheet must be marked |safe or its CSS
    (>, &, quotes) would be escaped and the PDF would render unstyled."""
    tmpl = (
        pathlib.Path(__file__).resolve().parent.parent / "templates" / "quarterly_report.html"
    ).read_text(encoding="utf-8")
    assert "{{ css_content|safe }}" in tmpl
    assert "{{ css_content }}" not in tmpl, "raw (un-safe) css_content interpolation must be gone"


def test_generate_quarterly_report_bytes_is_in_memory_no_disk():
    """The session-safe entry point returns PDF bytes and writes no file, so a demo
    visitor's report never lands on the shared Cloud disk where another session
    could reach it."""
    import inspect
    assert hasattr(rpt, "generate_quarterly_report_bytes")
    sig = inspect.signature(rpt.generate_quarterly_report_bytes)
    assert sig.return_annotation is bytes
    assert "output_path" not in sig.parameters, "the bytes entry point must not take/write a path"
    src = inspect.getsource(rpt.generate_quarterly_report_bytes)
    assert ".write_bytes" not in src and "_REPORTS_DIR" not in src, "must not write to disk"
    assert src.rstrip().endswith("return _render_pdf(html_content)")


def test_performance_page_serves_reports_from_session_not_shared_dir():
    """The Performance page must generate reports in memory and re-download from
    per-session state — never glob a shared reports directory, which on Cloud would
    hand one visitor another visitor's report."""
    page = (
        pathlib.Path(__file__).resolve().parent.parent / "pages" / "2_Performance.py"
    ).read_text(encoding="utf-8")
    assert "generate_quarterly_report_bytes" in page
    assert "_REPORTS_DIR" not in page, "the shared reports dir must be gone from the page"
    assert ".glob(" not in page, "no directory globbing to serve another visitor's file"
    assert "st.session_state" in page and "generated_report" in page


# ── CAPE regime: exec reports, Macro concludes, one derivation (PDF #5) ──────────
# Pre-fix the exec summary appended "supporting the diversification rationale" at EVERY
# CAPE level while the Macro section derived a percentile-conditional stance — a same-PDF
# contradiction below the 60th percentile (soft) and reversed below the 40th (opposite).
# Nothing pinned either section's regime logic before; this is the durable guard.

def test_cape_regime_band_boundaries():
    """The single derivation both sections consume. Bands: >=80 / >=60 / >=40 / else."""
    from src.reports import _cape_regime
    assert _cape_regime(80)[0] == "Elevated"
    assert _cape_regime(79)[0] == "Above-average"
    assert _cape_regime(60)[0] == "Above-average"
    assert _cape_regime(59)[0] == "Moderate"
    assert _cape_regime(40)[0] == "Moderate"
    assert _cape_regime(39)[0] == "Below-average"
    assert _cape_regime(0)[0] == "Below-average"


def test_exec_cape_sentence_reports_regime_not_allocation():
    """The exec line reports the reading + derived regime label and draws NO allocation
    conclusion (that is the Macro section's job), and reads naturally at both extremes."""
    from src.reports import _cape_reading_sentence
    for cape, pct, label in [(40.9, 99.0, "Elevated"), (18.0, 62.0, "Above-average"),
                             (16.0, 50.0, "Moderate"), (13.0, 20.0, "Below-average")]:
        s = _cape_reading_sentence(cape, pct)
        assert f"{label} versus history" in s, s
        assert f"in the {pct:.0f}th percentile" in s, s
        low = s.lower()
        # no allocation verb — never the old unconditional conclusion, in either direction
        for banned in ("supporting the diversification", "diversif", "us equity",
                       "us large-cap", "tilt", "overweight", "underweight"):
            assert banned not in low, f"exec drew an allocation conclusion ({banned!r}): {s}"


def test_exec_and_macro_cape_stances_never_contradict(monkeypatch):
    """Across constructed low/mid/high CAPE regimes, the Macro section's stance is the
    shared _cape_regime action and the exec reports the SAME regime label without carrying
    that action — so the two can never state opposite conclusions from one percentile."""
    import pandas as pd
    import src.reports as rpt
    from src.reports import _cape_regime, _cape_reading_sentence

    # Isolate CAPE: block FRED (yield curve / fed funds / HY go to N/A, no percentile call)
    monkeypatch.setattr(rpt, "get_series", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no fred")))
    monkeypatch.setattr(rpt, "compute_cape_implied_return", lambda cv: 0.0)
    monkeypatch.setattr(rpt, "get_cape_series", lambda: pd.Series([10.0, 20.0, 30.0]))

    for cape_val, pct in [(40.9, 99), (24.0, 85), (18.0, 62), (16.0, 50), (14.0, 25), (11.0, 8)]:
        monkeypatch.setattr(rpt, "current_cape", lambda cv=cape_val: cv)
        monkeypatch.setattr(rpt, "percentile", lambda series, val, p=pct: float(p))

        macro = rpt._build_macro_section()["cape"]
        exp_label, exp_action = _cape_regime(pct)

        # Macro consumes the shared derivation (not re-inlined bands)
        assert macro["regime"] == exp_label, (pct, macro["regime"])
        assert macro["regime_action"] == exp_action, (pct, macro["regime_action"])

        # Exec reports the same label and does NOT carry the action → cannot contradict it
        exec_s = _cape_reading_sentence(cape_val, float(pct))
        assert exp_label in exec_s, exec_s
        assert exp_action not in exec_s, f"exec carried the Macro stance: {exec_s}"
        assert "diversif" not in exec_s.lower() and "us equity" not in exec_s.lower()


# ── selection-driver suppression (PDF #7) ───────────────────────────────────────
# A sleeve with no mapped holding ticker (its only securities are its own benchmark
# legs — Real Assets is one PDBC-removal away) must be SUPPRESSED, not rendered with a
# dash ("holding (—)") or the sleeve name in the ticker slot ("Real Assets outperformed
# …"). Suppression is surfaced (suppressed_drivers + a section notice) so a driver never
# silently vanishes.

def test_sleeve_driver_tickers_suppresses_on_missing_ticker():
    from src.reports import _sleeve_driver_tickers
    hold = {"US Large Core": "VOO"}
    bench = {"US Large Core": "SPY", "Real Assets": "VNQ (60%) + DBC (40%)"}
    assert _sleeve_driver_tickers("US Large Core", hold, bench) == ("VOO", "SPY")
    assert _sleeve_driver_tickers("Real Assets", hold, bench) is None     # no holding ticker
    assert _sleeve_driver_tickers("Emerging Markets", hold, bench) is None  # neither mapped


def test_build_bf_cross_reference_suppresses_unmapped_holding(monkeypatch):
    """The sleeve-name echo (worse-in-kind): a no-holding sleeve must be dropped from the
    cross-reference and recorded, never emitted with its name in the ticker slot."""
    import src.reports as rpt
    from src.reports import build_bf_cross_reference
    monkeypatch.setattr(rpt, "sleeve_holding_ticker", lambda: {"US Large Core": "VOO"})
    monkeypatch.setattr(rpt, "sleeve_bench_ticker",
                        lambda: {"US Large Core": "SPY", "Real Assets": "VNQ (60%) + DBC (40%)"})
    bf = pd.DataFrame([
        {"sleeve": "Real Assets",   "r_p": 0.20, "r_b": 0.05, "selection_effect": 0.015},
        {"sleeve": "US Large Core", "r_p": 0.15, "r_b": 0.148, "selection_effect": 0.0003},
    ])
    bf.attrs["no_benchmark_sleeves"] = []
    supp: list = []
    items = build_bf_cross_reference(bf, suppressed=supp)
    assert supp == ["Real Assets"]
    assert all(it["holding"] != "Real Assets" for it in items), items
    assert [it["holding"] for it in items] == ["VOO"]


def test_sel_commentary_suppresses_unmapped_holding(monkeypatch):
    """The dash default: a ranked no-holding sleeve is suppressed (no 'holding (—)'
    sentence) and recorded in suppressed_drivers for the section notice."""
    import src.reports as rpt
    monkeypatch.setattr(rpt, "sleeve_holding_ticker",
                        lambda: {"US Large Core": "VOO", "US Small Cap": "AVUV"})  # Real Assets absent
    monkeypatch.setattr(rpt, "sleeve_bench_ticker",
                        lambda: {"US Large Core": "SPY", "US Small Cap": "IWM",
                                 "Real Assets": "VNQ (60%) + DBC (40%)"})
    monkeypatch.setattr(rpt, "_chart_b64", lambda *a, **k: None)
    bf = pd.DataFrame([
        {"sleeve": "Real Assets",   "w_p": 0.10, "w_b": 0.10, "r_p": 0.20, "r_b": 0.05,
         "allocation_effect": 0.0, "selection_effect": 0.015, "total_effect": 0.015},
        {"sleeve": "US Small Cap",  "w_p": 0.09, "w_b": 0.08, "r_p": 0.13, "r_b": 0.21,
         "allocation_effect": 0.0, "selection_effect": -0.007, "total_effect": -0.007},
        {"sleeve": "US Large Core", "w_p": 0.17, "w_b": 0.17, "r_p": 0.15, "r_b": 0.148,
         "allocation_effect": 0.0, "selection_effect": 0.0003, "total_effect": 0.0003},
    ])
    for k in ("no_benchmark_sleeves", "price_gaps", "benchmark_gaps"):
        bf.attrs[k] = []
    bf.attrs["cash_drag"] = 0.0
    monkeypatch.setattr(rpt, "brinson_fachler_period", lambda *a, **k: bf)

    attr = rpt._build_attribution_section("2026-03-31", "2026-06-30")
    assert attr["suppressed_drivers"] == ["Real Assets"]
    assert len(attr["sel_commentary"]) == 2, attr["sel_commentary"]
    assert not any("(—)" in s or "holding (Real Assets)" in s for s in attr["sel_commentary"])
    assert all("Real Assets:" not in s for s in attr["sel_commentary"])


# ── Failure paths carry their reason ─────────────────────────────────────────
#
# WHY THIS CAME BEFORE THE MARKER WORK. A marker cannot render a reason that was thrown
# away. The attribution section had three exits, two of which produced rows == [], and the
# template gated on `attr.rows` — so "No attribution data available for this period" was
# shown whether the quarter had no trades or the computation raised. One sentence, two
# facts, and a kept document cannot let its reader guess which.
#
# WHAT THE RENDER CORRECTED, recorded because the source reading was confidently wrong
# twice: an in-memory render (WeasyPrint intercepted, nothing written to data/reports/)
# showed the section does NOT vanish — the outer gate has an else branch — and the "+0.0"
# totals in _empty never reach the page, because the table body is gated out. So the defect
# was never fabricated zeros or a missing section. It was the conflation, and that is all.

def _reports_src() -> str:
    """src/reports.py with comments stripped, so no assertion here can be satisfied — or
    broken — by prose describing the fix."""
    import io
    import tokenize
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "src" / "reports.py").read_text(
        encoding="utf-8")
    return tokenize.untokenize(
        [tok for tok in tokenize.generate_tokens(io.StringIO(src).readline)
         if tok.type != tokenize.COMMENT])


def _template_src() -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / "templates" /
            "quarterly_report.html").read_text(encoding="utf-8")


def test_the_attribution_failure_exit_carries_its_reason():
    """`except Exception: return _empty` discarded the only thing the disposition field
    needs. The reason is now attached to the exit that produced it."""
    src = _reports_src()
    assert "except Exception as exc:" in src
    assert '"disposition": "failed"' in src
    assert 'f"{type(exc).__name__}: {exc}"' in src, (
        "the failure exit does not carry the exception type and message"
    )


def test_all_three_attribution_exits_declare_a_disposition():
    """Count-asserted over the three exits, not presence-asserted on one: failed, no_rows,
    computed. A presence assertion passes when two of three are wired, which is exactly the
    failure mode a mechanical edit produces."""
    src = _reports_src()
    for token in ('"disposition": "failed"', '"disposition": "no_rows"',
                  '"disposition":      "computed"'):
        assert token in src, f"no exit declares {token}"
    assert src.count('"disposition"') >= 4, (
        "expected the default plus three exits to set a disposition"
    )


def test_the_two_states_render_different_sentences():
    """The whole point. A template that branched on `attr.rows` alone could not tell them
    apart; it now branches on the disposition and says which."""
    tpl = _template_src()
    assert 'attr.disposition == "failed"' in tpl
    assert 'attr.disposition == "no_rows"' in tpl
    assert "could not be computed" in tpl, "the failure state has no text of its own"
    assert "No attribution to report" in tpl, "the no-rows state has no text of its own"


def test_the_failure_text_states_a_disposition_and_never_an_instruction():
    """The PDF rule, derived from reports.py already dropping staleness_note's remedy while
    keeping its date: a marker states what is missing, as of when, and what was done about
    it — never what the reader should do. A document's reader may have no access to the
    tool, the machine, or the moment."""
    tpl = _template_src()
    i = tpl.index('attr.disposition == "failed"')
    block = tpl[i:i + 700]
    assert "rather than shown with fabricated returns" in block, (
        "the failure text does not say what was done instead"
    )
    for imperative in ("Run ", "run tools/", "Try again", "refresh the", "Please "):
        assert imperative not in block, (
            f"the failure text tells the reader to act: {imperative!r}"
        )


def test_the_failure_text_forbids_the_wrong_inference():
    """"No data" invites "nothing happened". The failure state has to block that reading,
    because the one thing the software knows is that it does NOT know."""
    tpl = _template_src()
    i = tpl.index('attr.disposition == "failed"')
    assert "not a statement about the quarter" in tpl[i:i + 700]


def test_neither_gap_check_swallows_its_failure_any_more():
    """THE G2 SHAPE STATED PROPERLY. These two blocks compute the MARKER'S OWN INPUT, so a
    swallowed failure does not degrade a figure — it empties the channel the disclosure
    reads. The absence of a gap notice stops meaning "no gaps found" and starts meaning
    "either no gaps, or the check never ran", and silence reads as all-clear.

    Asserted by counting bare `except Exception: pass` in the executive-summary builder,
    because a handler that logs and re-swallows would still lose the disclosure.
    """
    import re

    src = _reports_src()
    # SCOPED TO THE TWO BLOCKS THIS PR CHANGED. An earlier version scanned the whole
    # executive-summary builder and failed on a THIRD swallow at the CAPE computation —
    # which feeds a rendered figure rather than a disclosure channel, and is deliberately
    # out of scope until its template guard is checked (filed separately). The test was
    # claiming a scope the change never had.
    start = src.index("_gap_check_failures: list[str] = []")
    end = src.index("_all_positive = worst")
    body = src[start:end]
    bare = re.findall(r"except Exception:\s*\n\s*pass", body)
    assert not bare, (
        f"{len(bare)} bare swallow(s) remain in the two gap-check blocks, where a "
        "swallowed failure empties a disclosure channel"
    )
    assert src.count("_gap_check_failures.append") == 2, (
        "expected both gap-check blocks to record their failure"
    )


def test_an_incomplete_gap_check_is_stated_where_its_notice_would_have_gone():
    """And phrased as what the reader must NOT conclude, which is the only honest content
    of a check that did not run."""
    src = _reports_src()
    assert "Coverage checks incomplete" in src
    assert "not evidence that no gaps" in src, (
        "the notice does not block the all-clear reading, which is its entire purpose"
    )
    # ASSERT THE APPEND, not the text. Composing the sentence is not rendering it — a
    # mutant that changed `narrative.append(...)` to `_unused = (...)` left every string
    # above intact and this test stayed green (D10). Presence is not use, and for a
    # DISCLOSURE that gap is the whole defect: the notice existing in the source while
    # never reaching the reader is indistinguishable from not having written it.
    i = src.index("**Coverage checks incomplete:**")
    assert "narrative.append(" in src[max(0, i - 120):i], (
        "the incomplete-check notice is composed but never appended to the narrative"
    )
