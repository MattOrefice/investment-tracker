"""Brinson-Fachler (1985) per-sleeve performance attribution."""
from datetime import date, timedelta

import pandas as pd

from src.db import get_connection
from src.prices import get_prices
from src.benchmarks import get_sleeve_benchmark_returns, _SLEEVE_BENCHMARKS


def brinson_fachler(
    portfolio_weights: dict[str, float],
    benchmark_weights: dict[str, float],
    portfolio_sleeve_returns: dict[str, float],
    benchmark_sleeve_returns: dict[str, float],
) -> pd.DataFrame:
    """
    Brinson-Fachler decomposition.

    For each sleeve i:
      Allocation effect = (w_p_i − w_b_i) × (r_b_i − r_b_total)
      Selection effect  = w_p_i × (r_p_i − r_b_i)
      Total             = allocation + selection

    Algebra check: sum(total effects) ≈ r_p_total − r_b_total (within 1 bp).

    All inputs are dicts keyed by sleeve name with float values.
    Returns a DataFrame with one row per sleeve.
    """
    sleeves = sorted(
        set(list(portfolio_weights) + list(benchmark_weights))
    )

    r_p_total = sum(
        portfolio_weights.get(s, 0.0) * portfolio_sleeve_returns.get(s, 0.0)
        for s in sleeves
    )
    r_b_total = sum(
        benchmark_weights.get(s, 0.0) * benchmark_sleeve_returns.get(s, 0.0)
        for s in sleeves
    )

    rows = []
    for sleeve in sleeves:
        w_p  = portfolio_weights.get(sleeve, 0.0)
        w_b  = benchmark_weights.get(sleeve, 0.0)
        r_p  = portfolio_sleeve_returns.get(sleeve, 0.0)
        r_b  = benchmark_sleeve_returns.get(sleeve, 0.0)

        alloc = (w_p - w_b) * (r_b - r_b_total)
        sel   = w_p * (r_p - r_b)

        rows.append({
            "sleeve":             sleeve,
            "w_p":                w_p,
            "w_b":                w_b,
            "r_p":                r_p,
            "r_b":                r_b,
            "allocation_effect":  alloc,
            "selection_effect":   sel,
            "total_effect":       alloc + sel,
        })

    df = pd.DataFrame(rows)

    # Algebra check: sum of effects must equal active return within 1 bp
    sum_effects   = df["total_effect"].sum()
    active_return = r_p_total - r_b_total
    discrepancy   = abs(sum_effects - active_return)
    assert discrepancy < 0.0001, (
        f"BF algebra check failed: Σeffects={sum_effects:.6f}, "
        f"R_p−R_b={active_return:.6f}, discrepancy={discrepancy*10000:.2f} bps"
    )

    return df


def compute_two_stage_attribution(
    port_return: float,
    saa_return: float,
    naive_return: float,
    sleeve_saa_weights: dict[str, float],
    sleeve_benchmark_returns: dict[str, float],
) -> dict:
    """
    Two-stage active return decomposition vs. a 60/40 naive baseline.

    Stage 1 (SAA design effect)    = saa_return    − naive_return
    Stage 2 (implementation effect) = port_return   − saa_return
    Total                           = port_return   − naive_return
                                    = Stage 1 + Stage 2  (algebraically exact)

    Per-sleeve Stage 1 contribution = SAA_weight_i × (sleeve_benchmark_return_i − naive_return).
    The per-sleeve sum equals Stage 1 exactly.

    All return arguments are decimals (e.g. 0.12 = 12%).

    Returns a dict with:
        stage1, stage2, total, algebra_residual,
        per_sleeve (dict: sleeve → decimal contribution),
        sleeve_sum_residual.
    """
    stage1 = saa_return  - naive_return
    stage2 = port_return - saa_return
    total  = port_return - naive_return   # = stage1 + stage2 by construction

    per_sleeve: dict[str, float] = {
        sleeve: weight * (sleeve_benchmark_returns.get(sleeve, 0.0) - naive_return)
        for sleeve, weight in sleeve_saa_weights.items()
    }
    sleeve_sum = sum(per_sleeve.values())

    return {
        "stage1":              stage1,
        "stage2":              stage2,
        "total":               total,
        "algebra_residual":    abs(stage1 + stage2 - total),
        "per_sleeve":          per_sleeve,
        "sleeve_sum_residual": abs(sleeve_sum - stage1),
    }


def _first_adj_price(ticker: str, from_date: str, window_days: int = 5) -> float:
    """Return the first available adj_close (total-return) price on or after from_date."""
    end = (date.fromisoformat(from_date) + timedelta(days=window_days)).isoformat()
    try:
        p = get_prices(ticker, from_date, end)
        if not p.empty:
            val = p["adj_close"].iloc[0]
            return float(val) if val and val > 0 else float(p["close"].iloc[0])
    except Exception:
        pass
    return 0.0


def _last_adj_price(ticker: str, up_to_date: str, window_days: int = 5) -> float:
    """Return the last available adj_close (total-return) price on or before up_to_date."""
    start = (date.fromisoformat(up_to_date) - timedelta(days=window_days)).isoformat()
    try:
        p = get_prices(ticker, start, up_to_date)
        if not p.empty:
            val = p["adj_close"].iloc[-1]
            return float(val) if val and val > 0 else float(p["close"].iloc[-1])
    except Exception:
        pass
    return 0.0


def brinson_fachler_period(
    start_date: str,
    end_date: str | None = None,
) -> pd.DataFrame:
    """
    Full Brinson-Fachler attribution over a calendar period.

    Uses beginning-of-period portfolio weights and sleeve-level returns.
    Benchmark weights = target weights from asset_classes table.
    Benchmark sleeve returns = from get_sleeve_benchmark_returns().
    """
    end = end_date or date.today().isoformat()

    # ── Load DB reference data ────────────────────────────────────────────────
    with get_connection() as conn:
        # Ticker → sleeve mapping
        sec_rows = conn.execute(
            """SELECT s.ticker, ac.name AS sleeve
               FROM securities s
               JOIN asset_classes ac ON s.asset_class_id = ac.asset_class_id"""
        ).fetchall()
        ticker_to_sleeve = {r["ticker"]: r["sleeve"] for r in sec_rows}

        # Target weights (benchmark weights)
        wt_rows = conn.execute(
            "SELECT name, target_weight FROM asset_classes WHERE parent_id IS NOT NULL"
        ).fetchall()
        benchmark_weights = {r["name"]: r["target_weight"] for r in wt_rows}

        # Holdings at start of period
        hold_rows = conn.execute(
            """SELECT ticker,
                      SUM(CASE WHEN LOWER(action)='buy' THEN shares ELSE -shares END) AS net_shares
               FROM trades
               WHERE trade_date <= ?
               GROUP BY ticker
               HAVING net_shares > 0""",
            (start_date,),
        ).fetchall()

    if not hold_rows:
        return pd.DataFrame()

    holdings = {r["ticker"]: float(r["net_shares"]) for r in hold_rows}

    # ── Compute beginning-of-period prices and sleeve values ─────────────────
    start_values: dict[str, float] = {}   # sleeve → market value at start
    end_values: dict[str, float] = {}     # sleeve → market value at end

    for ticker, shares in holdings.items():
        sleeve = ticker_to_sleeve.get(ticker, "Unknown")
        if ticker == "SPAXX":
            # SPAXX NAV is always $1; proxy T-bill yield through BIL.
            # p_start stays $1.00; p_end = $1.00 × (BIL_end / BIL_start).
            p_start = 1.0
            bil_start = _first_adj_price("BIL", start_date)
            bil_end   = _last_adj_price("BIL", end)
            p_end = (bil_end / bil_start) if bil_start > 0 else 1.0
        else:
            p_start = _first_adj_price(ticker, start_date)
            p_end   = _last_adj_price(ticker, end)

        start_values[sleeve] = start_values.get(sleeve, 0.0) + shares * p_start
        end_values[sleeve]   = end_values.get(sleeve, 0.0)   + shares * p_end

    total_start = sum(start_values.values())
    if total_start == 0:
        return pd.DataFrame()

    # ── Portfolio weights and returns per sleeve ──────────────────────────────
    portfolio_weights = {
        sleeve: mv / total_start for sleeve, mv in start_values.items()
    }
    portfolio_sleeve_returns = {
        sleeve: (end_values.get(sleeve, 0.0) / mv - 1.0) if mv > 0 else 0.0
        for sleeve, mv in start_values.items()
    }

    # ── Benchmark sleeve returns ──────────────────────────────────────────────
    bm_df = get_sleeve_benchmark_returns(start_date, end)
    bm_returns_raw = (
        bm_df.iloc[-1].to_dict() if not bm_df.empty else {}
    )
    # Fill any missing sleeves with 0
    bm_sleeve_returns = {
        s: bm_returns_raw.get(s, 0.0) for s in benchmark_weights
    }

    # ── Align: only include sleeves present in portfolio weights ─────────────
    all_sleeves = sorted(
        set(list(portfolio_weights) + list(benchmark_weights))
    )
    pw  = {s: portfolio_weights.get(s, 0.0)        for s in all_sleeves}
    bw  = {s: benchmark_weights.get(s, 0.0)        for s in all_sleeves}
    pr  = {s: portfolio_sleeve_returns.get(s, 0.0) for s in all_sleeves}
    br  = {s: bm_sleeve_returns.get(s, 0.0)        for s in all_sleeves}

    return brinson_fachler(pw, bw, pr, br)
