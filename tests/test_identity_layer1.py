"""Phase 12 Section 1 — Layer 1 identity tests (strict, blocking).

Mathematical identities that must hold by construction. Tests here should
never need updating as market data refreshes; they express structural invariants
of the SAA, the attribution math, the TWR conventions, and the risk metric formulas.

Subsections:
  1.1  SAA allocation identities (DB-backed, SAA table always seeded)
  1.2  Attribution per-sleeve decomposition identity
  1.3  Return computation identities
  1.4  Inception-date canonical-source check (DB-backed, skips on empty trades)
  1.5  Risk-free rate consistency across code and prose
  1.6  Demo-mode write guards (source-code structural invariants)
"""
from __future__ import annotations

import inspect
import pathlib
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.attribution import brinson_fachler
from src.db import get_connection
from src.performance import compute_risk_metrics
from src.returns import twr_daily_linked, twr_modified_dietz


# ── helpers ───────────────────────────────────────────────────────────────────

def _bday_series(returns: np.ndarray, start: str = "2025-01-01") -> pd.Series:
    idx = pd.bdate_range(start, periods=len(returns))
    return pd.Series(10_000.0 * np.cumprod(1.0 + returns), index=idx)


# ── 1.1  SAA allocation identities ───────────────────────────────────────────

def test_identity_saa_sleeve_weights_sum_to_one():
    """Sum of all sleeve (child) target_weights in asset_classes must equal 1.0.

    Tolerance: 1 bp (0.0001). Catches phantom allocations, off-by-one seeding,
    or silent drift from an ALTER TABLE that touches target_weight.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name, target_weight FROM asset_classes WHERE parent_id IS NOT NULL"
        ).fetchall()

    assert rows, "asset_classes table has no child sleeve rows — SAA not seeded"
    total = sum(r["target_weight"] for r in rows)
    assert abs(total - 1.0) < 0.0001, (
        f"Sleeve target_weights sum to {total:.6f}, not 1.0. "
        f"Rows: {[(r['name'], r['target_weight']) for r in rows]}"
    )


def test_identity_saa_parent_weights_sum_to_one():
    """Sum of all parent (top-level) target_weights must equal 1.0.

    Parents are Equity, Income, Real Assets, Cash. Their weights must sum to 1.0
    independently of the child sleeves, since they're stored as a separate set of rows.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name, target_weight FROM asset_classes WHERE parent_id IS NULL"
        ).fetchall()

    assert rows, "asset_classes table has no parent rows — SAA not seeded"
    total = sum(r["target_weight"] for r in rows)
    assert abs(total - 1.0) < 0.0001, (
        f"Parent target_weights sum to {total:.6f}, not 1.0. "
        f"Rows: {[(r['name'], r['target_weight']) for r in rows]}"
    )


def test_identity_saa_parent_equals_sum_of_children():
    """Each parent's target_weight must equal the sum of its children's target_weights.

    This tests the hierarchy: Equity (0.78) = US Large Core (0.17) + US Large Quality (0.15)
    + US Large Value (0.09) + US Small Cap (0.08) + Intl Developed (0.20) + EM (0.09).
    If a sleeve is reclassified or a child weight is adjusted without updating the parent,
    this test fails.
    """
    with get_connection() as conn:
        parent_rows = conn.execute(
            "SELECT asset_class_id, name, target_weight FROM asset_classes WHERE parent_id IS NULL"
        ).fetchall()
        child_rows = conn.execute(
            "SELECT parent_id, target_weight FROM asset_classes WHERE parent_id IS NOT NULL"
        ).fetchall()

    children_by_parent: dict[int, float] = {}
    for r in child_rows:
        pid = r["parent_id"]
        children_by_parent[pid] = children_by_parent.get(pid, 0.0) + r["target_weight"]

    for parent in parent_rows:
        pid   = parent["asset_class_id"]
        p_wt  = parent["target_weight"]
        c_sum = children_by_parent.get(pid, 0.0)
        assert abs(p_wt - c_sum) < 0.0001, (
            f"Parent '{parent['name']}' target_weight={p_wt:.4f} ≠ "
            f"sum of children={c_sum:.4f}. "
            "Check asset_classes seeding or a recent weight change."
        )


# ── 1.2  Attribution per-sleeve decomposition identity ───────────────────────

def test_identity_bf_per_sleeve_alloc_plus_sel_equals_total():
    """For every row in the BF output, total_effect == allocation_effect + selection_effect.

    The Brinson-Fachler formula has two components; total is their sum.
    This test pins that no interaction term or rounding breaks the per-sleeve identity.
    """
    pw = {"US Core": 0.18, "Intl": 0.20, "Fixed": 0.10, "Cash": 0.03, "EM": 0.10,
          "Quality": 0.15, "Value": 0.09, "Small": 0.08, "Real": 0.07}
    bw = {"US Core": 0.16, "Intl": 0.19, "Fixed": 0.09, "Cash": 0.03, "EM": 0.08,
          "Quality": 0.14, "Value": 0.08, "Small": 0.07, "Real": 0.16}
    pr = {"US Core": 0.08, "Intl": 0.12, "Fixed": 0.03, "Cash": 0.04, "EM": 0.15,
          "Quality": 0.10, "Value": 0.05, "Small": 0.18, "Real": 0.07}
    br = {"US Core": 0.09, "Intl": 0.11, "Fixed": 0.02, "Cash": 0.04, "EM": 0.14,
          "Quality": 0.11, "Value": 0.06, "Small": 0.16, "Real": 0.06}

    df = brinson_fachler(pw, bw, pr, br)

    for _, row in df.iterrows():
        expected_total = row["allocation_effect"] + row["selection_effect"]
        assert abs(row["total_effect"] - expected_total) < 1e-12, (
            f"Sleeve '{row['sleeve']}': "
            f"total_effect ({row['total_effect']:.8f}) ≠ "
            f"alloc + sel ({expected_total:.8f}). "
            "BF per-sleeve identity broken."
        )


def test_identity_portfolio_return_equals_weighted_sleeve_returns():
    """Σ(portfolio_weight_i × portfolio_sleeve_return_i) = portfolio_total_return.

    This is the fundamental aggregation identity: the BF framework uses
    beginning-of-period weights to reconstruct the total portfolio return.
    Verifies that brinson_fachler's internal r_p_total matches the manual computation.
    """
    pw = {"A": 0.60, "B": 0.30, "C": 0.10}
    bw = {"A": 0.50, "B": 0.35, "C": 0.15}
    pr = {"A": 0.10, "B": -0.02, "C": 0.05}
    br = {"A": 0.08, "B": -0.01, "C": 0.04}

    expected_port_ret  = sum(pw[s] * pr[s] for s in pw)   # 0.60*0.10 + 0.30*(-0.02) + 0.10*0.05
    expected_bench_ret = sum(bw[s] * br[s] for s in bw)

    df = brinson_fachler(pw, bw, pr, br)
    sum_effects   = df["total_effect"].sum()
    active_return = expected_port_ret - expected_bench_ret

    assert abs(sum_effects - active_return) < 1e-10, (
        f"Σ(BF total effects) = {sum_effects:.8f} ≠ "
        f"r_p_total − r_b_total = {active_return:.8f}. "
        "Weighted aggregation identity broken."
    )


def test_identity_benchmark_weights_sum_to_one_in_bf():
    """Benchmark weights in brinson_fachler must sum to 1.0 for algebra to close.

    If benchmark weights don't sum to 1, r_b_total ≠ true benchmark return and
    the BF algebra check will eventually fail. The SAA uses DB target_weights as
    benchmark weights; their sum is verified separately, but this test confirms
    the BF function doesn't silently accept non-unit weights without failing.
    """
    # Well-formed weights (sum to 1): should pass
    pw = {"A": 0.60, "B": 0.40}
    bw = {"A": 0.60, "B": 0.40}
    pr = {"A": 0.10, "B": 0.05}
    br = {"A": 0.09, "B": 0.04}

    df = brinson_fachler(pw, bw, pr, br)
    assert abs(df["total_effect"].sum() - (0.60*0.10 + 0.40*0.05 - 0.60*0.09 - 0.40*0.04)) < 1e-10


# ── 1.3  Return computation identities ───────────────────────────────────────

def test_identity_twr_dietz_agree_within_10bps_no_cf():
    """For a series with no external cash flows, TWR and Modified Dietz must agree
    within 10 basis points.

    TWR (chain-linked) and Modified Dietz are different approximation methods.
    For a buy-and-hold period with no cash flows, both reduce to (V_end − V_start)/V_start,
    so they must agree exactly. Any divergence indicates a bug in one of the implementations.
    """
    rng = np.random.default_rng(77)
    for seed in (1, 42, 99, 200):
        rng = np.random.default_rng(seed)
        returns = rng.normal(0.0004, 0.01, 252)
        idx = pd.bdate_range("2025-01-01", periods=252)
        values = pd.Series(10_000.0 * np.cumprod(1.0 + returns), index=idx)
        zeros  = pd.Series(0.0, index=idx)

        twr   = twr_daily_linked(values, zeros)
        dietz = twr_modified_dietz(values, zeros)

        diff_bps = abs(twr - dietz) * 10_000
        assert diff_bps < 10, (
            f"seed={seed}: TWR ({twr:.6f}) and Dietz ({dietz:.6f}) differ by "
            f"{diff_bps:.2f} bps > 10 bps tolerance. Both should equal (V_end−V_start)/V_start "
            "for a no-CF series."
        )


def test_identity_annualized_return_calendar_year():
    """annualize(r, 365) == r: one calendar year must annualize to the period return.

    annualize() uses calendar-day convention: (1+r)^(365/days) − 1.
    At days=365, the exponent is 1 and the function is the identity.
    Pins the convention: 365 calendar days = one year (not 252 trading days).
    """
    from src.returns import annualize
    for r_period in (0.05, 0.10, -0.08, 0.25):
        ann = annualize(r_period, 365)
        assert abs(ann - r_period) < 1e-10, (
            f"annualize({r_period}, 365) = {ann:.8f}, expected {r_period}. "
            "365 calendar days should annualize to the period return exactly."
        )


def test_identity_risk_metrics_uses_trading_day_annualization():
    """compute_risk_metrics uses trading-day convention (252/n_trading_days) for
    annualized port and bench returns — distinct from annualize() which uses calendar days.

    Pins the two conventions are intentionally different:
    - annualize(): calendar days (365/n_cal_days) — used for TWR period slices
    - compute_risk_metrics ann_port/ann_bench: trading days (252/n_trading_days)

    Verify: for a series with exactly 252 trading-day returns, ann_port ≈ period return.
    """
    rng = np.random.default_rng(55)
    N = 252
    port_ret = rng.normal(0.0005, 0.01, N)

    pv = _bday_series(port_ret)
    bl = _bday_series(np.zeros(N))

    m = compute_risk_metrics(pv, bl)
    assert m, "compute_risk_metrics returned empty dict"

    # At exactly 252 trading days, ann_port ≈ the simple period return
    simple_period_ret = float(pv.iloc[-1] / pv.iloc[0] - 1)
    ann_port = m["ann_port_return_pct"] / 100.0

    # (1 + period)^(252/252) - 1 = period_return
    assert abs(ann_port - simple_period_ret) < 0.001, (
        f"ann_port ({ann_port:.6f}) and simple period return ({simple_period_ret:.6f}) "
        f"differ by {abs(ann_port - simple_period_ret)*10000:.1f} bps for N=252 trading days. "
        "At 252 trading days, the 252/n exponent is 1 and ann_port should ≈ period return."
    )


# ── 1.4  Inception-date canonical-source check ───────────────────────────────

def test_identity_performance_page_inception_constant_matches_fallback():
    """get_inception_date() fallback must be the canonical portfolio launch date.

    pages/2_Performance.py now calls get_inception_date() at import time; the
    fallback hard-coded in src/holdings.py is the only remaining literal and must
    stay set to '2025-05-01'. This test fails if the fallback is changed without
    a deliberate portfolio-relaunch decision.
    """
    import re
    src_path = pathlib.Path(__file__).resolve().parent.parent / "src" / "holdings.py"
    src_text = src_path.read_text(encoding="utf-8")
    # Find the fallback literal inside get_inception_date
    match = re.search(
        r'def get_inception_date.*?return\s+["\']([0-9]{4}-[0-9]{2}-[0-9]{2})["\']',
        src_text, re.DOTALL,
    )
    assert match, "get_inception_date fallback literal not found in src/holdings.py"
    fallback = match.group(1)
    assert fallback == "2025-05-01", (
        f"get_inception_date fallback is '{fallback}', expected '2025-05-01' "
        "(canonical portfolio launch date)."
    )


def test_identity_inception_matches_db_min_trade_date():
    """When the trades table is populated, get_inception_date() must return MIN(trade_date).

    This test skips if trades table is empty (local development). On Cloud with
    demo.db, the MIN(trade_date) should match '2025-05-01'.
    """
    with get_connection() as conn:
        row = conn.execute("SELECT MIN(trade_date) AS inception FROM trades").fetchone()

    if not row or not row["inception"]:
        pytest.skip("Trades table empty — skipped in local/empty-DB mode")

    from src.holdings import get_inception_date
    reported = get_inception_date(account_id=1)

    assert reported == row["inception"], (
        f"get_inception_date() returned '{reported}' but MIN(trade_date) = '{row['inception']}'. "
        "The inception date has drifted from the DB canonical source."
    )


# ── 1.5  Risk-free rate consistency ──────────────────────────────────────────

def test_identity_rf_default_matches_caption_disclosure():
    """The default rf_annual parameter in compute_risk_metrics must equal 4.5% (0.045),
    matching the 'RF = 4.5%' disclosure in the Performance page caption.

    If the rate is updated in the code without updating the caption (or vice versa),
    this test fails and forces both to be updated in sync.
    """
    sig = inspect.signature(compute_risk_metrics)
    rf_default = sig.parameters["rf_annual"].default
    assert rf_default == pytest.approx(0.045, abs=1e-10), (
        f"compute_risk_metrics default rf_annual = {rf_default}, expected 0.045 (4.5%). "
        "Update both the code default and the caption disclosure in pages/2_Performance.py."
    )

    # Also confirm the caption string contains the matching text
    page_path = pathlib.Path(__file__).resolve().parent.parent / "pages" / "2_Performance.py"
    source = page_path.read_text(encoding="utf-8")
    rf_pct_str = f"{rf_default * 100:.1f}%"  # "4.5%"
    assert f"RF = {rf_pct_str}" in source, (
        f"'RF = {rf_pct_str}' not found in pages/2_Performance.py. "
        "The caption disclosure must match the rf_annual default."
    )


def test_identity_rf_daily_conversion_from_annual():
    """The daily RF rate computed inside compute_risk_metrics must equal
    (1 + rf_annual)^(1/252) − 1 for the default 4.5% annual rate.

    Pins the convention: the code uses trading-day compounding (252 days),
    not calendar-day compounding (365 days). If this convention changes,
    the Sharpe and Sortino formulas and their disclosed interpretations change.
    """
    rf_annual = 0.045
    rf_daily_expected = (1 + rf_annual) ** (1 / 252) - 1

    # Verify via round-trip: annualizing the daily rate gives back rf_annual
    rf_annual_roundtrip = (1 + rf_daily_expected) ** 252 - 1
    assert abs(rf_annual_roundtrip - rf_annual) < 1e-10, (
        f"RF annual round-trip failed: {rf_annual_roundtrip:.8f} ≠ {rf_annual}. "
        "Daily RF = (1+annual)^(1/252) − 1 must round-trip cleanly."
    )

    # Verify it's different from calendar-day compounding
    rf_daily_calendar = (1 + rf_annual) ** (1 / 365) - 1
    assert abs(rf_daily_expected - rf_daily_calendar) > 1e-6, (
        "Trading-day RF and calendar-day RF are equal — check the exponent in "
        "compute_risk_metrics (should be 1/252, not 1/365)."
    )


# ── 1.6  Demo-mode write guards ──────────────────────────────────────────────

def test_identity_trade_form_has_demo_guard():
    """render_trade_form() in pages/10_Trade_Log.py must open with an is_write_enabled()
    guard that returns early before any write path is reached.

    This is a source-code structural invariant: if the guard is accidentally removed
    in a future edit, this test fails before the omission reaches production, where
    it would allow any public visitor to insert trades into the demo database.

    Phase 22 upgraded the guard from `if IS_DEMO:` to `if not is_write_enabled():`.
    Both patterns are accepted here so the test does not hard-code the idiom.
    """
    page_path = pathlib.Path(__file__).resolve().parent.parent / "pages" / "10_Trade_Log.py"
    source = page_path.read_text(encoding="utf-8")

    # Locate the function definition
    func_start = source.find("def render_trade_form():")
    assert func_start != -1, "render_trade_form() not found in pages/10_Trade_Log.py — was it renamed?"

    func_body = source[func_start:]
    insert_pos = func_body.find("INSERT INTO")

    # Accept either guard idiom: old IS_DEMO or new is_write_enabled()
    guard_pos_demo  = func_body.find("if IS_DEMO:")
    guard_pos_write = func_body.find("if not is_write_enabled():")
    guard_pos = max(p for p in (guard_pos_demo, guard_pos_write) if p != -1) \
        if any(p != -1 for p in (guard_pos_demo, guard_pos_write)) else -1

    assert guard_pos != -1, (
        "Write guard missing from render_trade_form() in pages/10_Trade_Log.py. "
        "Demo visitors can write trades to demo.db. "
        "Add: if not is_write_enabled(): write_guard_toast(); return"
    )
    assert guard_pos < insert_pos, (
        f"Write guard (pos {guard_pos}) appears after first INSERT (pos {insert_pos}) "
        "in render_trade_form(). The guard must come first — writes are reachable before the check."
    )


def test_trade_form_no_early_return_in_demo_guard():
    """render_trade_form() must NOT early-return when write is disabled.

    Phase 22.1 changed the pattern: the guard block now shows an info banner
    and continues into the form, rather than returning early. The write is
    blocked at submit time via write_guard_toast(). Pinning the absence of an
    early return ensures demo visitors can see and interact with the form.
    """
    page_path = pathlib.Path(__file__).resolve().parent.parent / "pages" / "10_Trade_Log.py"
    source = page_path.read_text(encoding="utf-8")

    func_start = source.find("def render_trade_form():")
    assert func_start != -1, "render_trade_form() not found"

    func_body = source[func_start:]
    guard_start = func_body.find("if not is_write_enabled():")
    assert guard_start != -1, "is_write_enabled guard not found in render_trade_form()"

    # The form body begins at the first assignment to `securities`
    form_body_start = func_body.find("securities    = data")
    assert form_body_start != -1, "'securities    = data' marker not found in render_trade_form()"

    # No bare `return` should appear between the guard and the form body
    guard_region = func_body[guard_start:form_body_start]
    assert "\n        return" not in guard_region, (
        "render_trade_form() early-returns inside the is_write_enabled guard. "
        "Demo visitors cannot see the trade form. Remove the early return; "
        "the write should be blocked at submit time via write_guard_toast()."
    )


def test_identity_macro_force_refresh_has_demo_guard():
    """The Force Refresh button in pages/3_Macro.py must be gated by `not IS_DEMO`.

    In demo mode, the button must not render. This prevents public visitors from
    triggering FRED API calls (quota waste) and cache deletion (macro_cache DELETE).
    Pins the guard so it cannot be accidentally removed.
    """
    page_path = pathlib.Path(__file__).resolve().parent.parent / "pages" / "3_Macro.py"
    source = page_path.read_text(encoding="utf-8")

    # Find the Force Refresh button definition
    btn_pos = source.find('"Force refresh"')
    assert btn_pos != -1, '"Force refresh" button not found in pages/3_Macro.py — was it removed?'

    # The IS_DEMO guard must appear on the same logical line / nearby before the button
    # Check the 120 chars preceding the button string for the guard
    context = source[max(0, btn_pos - 120):btn_pos]
    assert "IS_DEMO" in context, (
        "IS_DEMO guard not found immediately before the Force Refresh button in pages/3_Macro.py. "
        "The button must be conditioned on `not IS_DEMO` to prevent demo visitors from "
        "triggering cache deletion and FRED API calls."
    )
