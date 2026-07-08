"""Brinson-Fachler (1985) per-sleeve performance attribution."""
import logging
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from src.db import get_connection
from src.prices import get_prices
from src.benchmarks import get_sleeve_benchmark_returns, _SLEEVE_BENCHMARKS

_CASH_SLEEVE = "Cash / SPAXX"  # operational float — excluded from ex-cash BF weights


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

    All inputs are dicts keyed by sleeve name with float values. Weight-dict
    sparsity is legal (a sleeve absent from a weight dict is a genuine 0
    weight); a sleeve absent from a returns dict is not — every caller must
    supply an explicit return for each weighted sleeve, since the .get(s, 0.0)
    defaults below exist only to keep the lookups total, not to paper over
    missing data.
    Returns a DataFrame with one row per sleeve.
    """
    sleeves = sorted(
        set(list(portfolio_weights) + list(benchmark_weights))
    )

    # Completeness precondition: the .get(s, 0.0) defaults below must never be
    # reached by a real sleeve. A sleeve missing from a *weight* dict is a
    # legitimate 0 weight (standard BF); a sleeve missing from a *returns*
    # dict is silent fabrication of a 0% return that the algebra check below
    # cannot detect (it cancels out when w_b == 0, but is otherwise wrong).
    # Fail loudly instead.
    _missing_returns = [
        s for s in sleeves
        if s not in portfolio_sleeve_returns or s not in benchmark_sleeve_returns
    ]
    assert not _missing_returns, (
        f"Sleeve(s) {_missing_returns} carry weight but are missing an "
        f"explicit portfolio and/or benchmark return — the .get(s, 0.0) "
        f"sink defaults would fabricate a return instead of raising"
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


def _first_adj_price(ticker: str, from_date: str, window_days: int = 5) -> Optional[float]:
    """Return the first available adj_close (total-return) price on or after from_date.

    Returns None — never a fabricated 0.0 — if no price is found within the window,
    whether because the window came back empty or because the lookup raised. The
    caller must treat None as an explicit data gap, never as a price of zero.
    """
    end = (date.fromisoformat(from_date) + timedelta(days=window_days)).isoformat()
    try:
        p = get_prices(ticker, from_date, end)
    except Exception:
        logging.exception("Price lookup failed for %s in [%s, %s]", ticker, from_date, end)
        return None
    if not p.empty:
        val = p["adj_close"].iloc[0]
        return float(val) if val and val > 0 else float(p["close"].iloc[0])
    logging.warning(
        "No price found for %s in [%s, %s] (%d-day window from %s)",
        ticker, from_date, end, window_days, from_date,
    )
    return None


def _last_adj_price(ticker: str, up_to_date: str, window_days: int = 5) -> Optional[float]:
    """Return the last available adj_close (total-return) price on or before up_to_date.

    Returns None — never a fabricated 0.0 — if no price is found within the window,
    whether because the window came back empty or because the lookup raised. The
    caller must treat None as an explicit data gap, never as a price of zero.
    """
    start = (date.fromisoformat(up_to_date) - timedelta(days=window_days)).isoformat()
    try:
        p = get_prices(ticker, start, up_to_date)
    except Exception:
        logging.exception("Price lookup failed for %s in [%s, %s]", ticker, start, up_to_date)
        return None
    if not p.empty:
        val = p["adj_close"].iloc[-1]
        return float(val) if val and val > 0 else float(p["close"].iloc[-1])
    logging.warning(
        "No price found for %s in [%s, %s] (%d-day window to %s)",
        ticker, start, up_to_date, window_days, up_to_date,
    )
    return None


def price_gap_notice(price_gaps: list[tuple[str, str]]) -> str:
    """Single-source user-facing notice for a period whose attribution excluded one
    or more sleeves because a held ticker's price could not be found within the
    lookback window. Shared across the Performance page, the Benchmark Attribution
    page, and both PDF report sections so the wording can't drift between them.
    """
    if not price_gaps:
        return ""
    parts = "; ".join(f"{ticker} as of {dt}" for ticker, dt in price_gaps)
    return f"Data unavailable for {parts} — excluded from this period's attribution."


def benchmark_gap_notice(benchmark_gaps: list[tuple[str, str, str]]) -> str:
    """Single-source user-facing notice for a period in which one or more
    benchmark components could not be priced — the benchmark-side mirror of
    price_gap_notice, shared across the Performance page, the Benchmark
    Attribution page, and both PDF report sections so the wording can't drift.

    A sleeve whose benchmark is entirely unpriceable is excluded from the
    period's attribution; a partially-priceable blend degrades to its surviving
    components; the cash sleeve's figures never depend on its benchmark — the
    wording covers all three tiers.
    """
    if not benchmark_gaps:
        return ""
    parts = "; ".join(
        f"{sleeve} ({ticker}) as of {dt}" for sleeve, ticker, dt in benchmark_gaps
    )
    return (
        f"Benchmark data unavailable for {parts} — a sleeve whose benchmark "
        f"cannot be priced at all is excluded from this period's attribution; "
        f"a partially priceable blend is measured on its surviving components; "
        f"the cash sleeve's figures are unaffected (its benchmark is "
        f"informational only)."
    )


def brinson_fachler_period(
    start_date: str,
    end_date: str | None = None,
) -> pd.DataFrame:
    """
    Full Brinson-Fachler attribution over a calendar period.

    Uses beginning-of-period portfolio weights and sleeve-level returns.
    Benchmark weights = target weights from asset_classes table.
    Benchmark sleeve returns = from get_sleeve_benchmark_returns().

    Price gaps: if a held ticker's price cannot be found within the lookback window
    (see _first_adj_price/_last_adj_price), that sleeve is EXCLUDED from this
    period's attribution rather than assigned a fabricated 0% or -100% return. The
    gap is recorded in the returned DataFrame's ``.attrs["price_gaps"]`` — a list of
    (ticker, date) pairs — on every return path, including the early-return empty
    frames, so callers can surface an explicit "data unavailable" notice.

    Benchmark gaps (the mirror of the above, for r_b): if a strategic sleeve's
    benchmark cannot be priced over the window (NaN sentinel from
    get_sleeve_benchmark_returns), that sleeve is EXCLUDED from this period's
    attribution through the same purge path — never assigned a fabricated 0%
    benchmark return that would book its benchmark move as selection skill —
    and the gap is recorded in ``.attrs["benchmark_gaps"]`` — a list of
    (sleeve, ticker, date) tuples — on every return path. Two carve-outs: the
    cash sleeve degrades instead of excluding (its benchmark return is never
    consumed and purging it would zero the cash-drag bridge), and a sleeve with
    no benchmark weight (w_b == 0, e.g. an unmapped ticker's "Unknown" bucket)
    is retained — its r_b placeholder cancels algebraically — and listed in
    ``.attrs["no_benchmark_sleeves"]`` so render surfaces show N/A instead of a
    fabricated 0.00%.
    """
    end = end_date or date.today().isoformat()
    price_gaps: list[tuple[str, str]] = []
    benchmark_gaps: list[tuple[str, str, str]] = []

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

        # Holdings at start of period. DRIP lots (lot_source='drip') are EXCLUDED:
        # sleeves are valued at adj_close, which already embeds dividend
        # reinvestment, so counting DRIP-reinvested shares on top would
        # double-count income (matches get_portfolio_value_series; keeps the
        # BF↔TWR reconciliation identity).
        hold_rows = conn.execute(
            """SELECT ticker,
                      SUM(CASE WHEN LOWER(action)='buy' THEN shares ELSE -shares END) AS net_shares
               FROM trades
               WHERE trade_date <= ?
                 AND (lot_source IS NULL OR lot_source != 'drip')
               GROUP BY ticker
               HAVING net_shares > 0""",
            (start_date,),
        ).fetchall()

        # Portfolio inception date (for historical DRIP accumulation before window)
        inception_row = conn.execute(
            "SELECT MIN(trade_date) AS inception FROM trades"
        ).fetchone()
        portfolio_inception = (
            inception_row["inception"]
            if inception_row and inception_row["inception"]
            else start_date
        )

    if not hold_rows:
        empty = pd.DataFrame()
        empty.attrs["price_gaps"] = price_gaps
        empty.attrs["benchmark_gaps"] = benchmark_gaps
        empty.attrs["no_benchmark_sleeves"] = []
        return empty

    holdings = {r["ticker"]: float(r["net_shares"]) for r in hold_rows}

    # ── Benchmark sleeve returns ──────────────────────────────────────────────
    # Fetched BEFORE the holdings loop (it depends only on the window bounds) so
    # benchmark-gapped sleeves can join the same purge path as portfolio price
    # gaps below. The purge runs before the totals, which is what makes the
    # surviving portfolio weights renormalize implicitly — excluding a sleeve
    # later, at the alignment step, with only the benchmark weights renormalized
    # would break the BF algebra check.
    bm_df = get_sleeve_benchmark_returns(start_date, end)
    benchmark_gaps.extend(bm_df.attrs.get("benchmark_gaps", []))
    bm_returns_raw = bm_df.iloc[-1].to_dict() if not bm_df.empty else {}

    # A sleeve carrying real benchmark weight but missing from the static
    # benchmark map is configuration drift (DB sleeve names vs
    # _SLEEVE_BENCHMARKS), not a per-period data gap — fail loudly instead of
    # silently zero-filling its benchmark return.
    _unmapped = [
        s for s, w in benchmark_weights.items() if w > 0 and s not in bm_df.columns
    ]
    assert not _unmapped, (
        f"Sleeve(s) {_unmapped} carry benchmark weight but have no "
        f"_SLEEVE_BENCHMARKS mapping — benchmark configuration drift"
    )

    # Strategic sleeves whose benchmark could not be priced this period (NaN
    # sentinel from get_sleeve_benchmark_returns) — excluded below exactly like
    # portfolio-side price gaps. Derived from benchmark_weights (not the frame)
    # so a rowless bm_df counts as everything-gapped, never as everything-fine.
    # Cash is exempt: its benchmark return is never consumed (cash leaves the
    # BF universe before the decomposition) and purging it would zero the
    # cash-drag bridge, so it degrades like the portfolio-side BIL path
    # instead. Zero-weight sleeves are exempt too — they are retained with an
    # N/A benchmark (their r_b cancels algebraically).
    benchmark_gapped = {
        s
        for s, w in benchmark_weights.items()
        if w > 0
        and s != _CASH_SLEEVE
        and pd.isna(bm_returns_raw.get(s, float("nan")))
    }

    # BIL prices anchored at portfolio inception so SPAXX weight reflects
    # the full historical appreciation of cash since launch.
    bil_inception    = _first_adj_price("BIL", portfolio_inception)
    # _last_adj_price (backward-fill) aligns with get_portfolio_value_series()
    # which uses ffill() — both return the prior trading-day close for non-trading
    # start dates (e.g. YTD start = Jan 1).
    bil_period_start = _last_adj_price("BIL", start_date)
    bil_period_end   = _last_adj_price("BIL", end)
    for _bil_date, _bil_price in (
        (portfolio_inception, bil_inception),
        (start_date, bil_period_start),
        (end, bil_period_end),
    ):
        if _bil_price is None:
            price_gaps.append(("BIL", _bil_date))

    def _safe_bil_ratio(numerator: Optional[float], denominator: Optional[float]) -> float:
        """BIL/cash price ratio, degrading to a flat 1.0 (no appreciation) rather
        than crashing or dividing by a missing/zero price. Extends the existing
        bil_inception==0 guard to also cover bil_period_start/bil_period_end being
        unavailable — the gap is already recorded in price_gaps above."""
        if numerator is None or denominator is None or denominator <= 0:
            return 1.0
        return numerator / denominator

    # ── Compute beginning-of-period prices and sleeve values ─────────────────
    start_values: dict[str, float] = {}   # sleeve → market value at start
    end_values: dict[str, float] = {}     # sleeve → market value at end
    gapped_sleeves: set[str] = set()      # sleeves excluded this period (price gap)

    for ticker, shares in holdings.items():
        sleeve = ticker_to_sleeve.get(ticker, "Unknown")
        if ticker == "SPAXX":
            # Normalize BIL to portfolio inception so sleeve weight correctly
            # reflects historical BIL appreciation.  Period return is unchanged:
            # p_end / p_start = (BIL_end / BIL_inception) / (BIL_start / BIL_inception)
            #                 = BIL_end / BIL_start.
            p_start = _safe_bil_ratio(bil_period_start, bil_inception)
            p_end   = _safe_bil_ratio(bil_period_end, bil_inception)
            start_values[sleeve] = start_values.get(sleeve, 0.0) + shares * p_start
            end_values[sleeve]   = end_values.get(sleeve, 0.0)   + shares * p_end
        else:
            p_start = _last_adj_price(ticker, start_date)  # backward-fill matches pv ffill
            p_end   = _last_adj_price(ticker, end)
            if p_start is None:
                price_gaps.append((ticker, start_date))
            if p_end is None:
                price_gaps.append((ticker, end))
            if p_start is None or p_end is None:
                # Data gap: exclude this sleeve from the period rather than
                # fabricate a 0% (missing start) or -100% (missing end) return.
                gapped_sleeves.add(sleeve)
                continue
            # Actual (non-DRIP) shares valued at adj_close on both ends → the sleeve
            # total return p_end/p_start − 1, with dividend income already embedded
            # in adj_close. No DRIP-share addition (that would double-count income).
            start_values[sleeve] = start_values.get(sleeve, 0.0) + shares * p_start
            end_values[sleeve]   = end_values.get(sleeve, 0.0)   + shares * p_end

    # Purge gapped sleeves entirely (not partially) so neither the aggregate
    # totals nor the per-sleeve rows are built from an incomplete valuation.
    # Benchmark-gapped sleeves take the same path: one exclusion convention,
    # one renormalization convention (both weight vectors re-sum to 1.0 over
    # the surviving universe, keeping the BF algebra check exact).
    gapped_sleeves |= benchmark_gapped
    for sleeve in gapped_sleeves:
        start_values.pop(sleeve, None)
        end_values.pop(sleeve, None)

    total_start = sum(start_values.values())
    if total_start == 0:
        empty = pd.DataFrame()
        empty.attrs["price_gaps"] = price_gaps
        empty.attrs["benchmark_gaps"] = benchmark_gaps
        empty.attrs["no_benchmark_sleeves"] = []
        return empty

    # ── Phase 38b-2 — ex-cash attribution + explicit operational cash drag ────
    # The benchmark is already ex-cash (9 rescaled sleeves; cash untargeted), so
    # the portfolio side is normalized to INVESTED value (operational SPAXX float
    # excluded) — the 9 strategic weights then sum to 1.0 and match the benchmark.
    # The float's effect on the actual portfolio return is NOT erased: it is broken
    # out as a cash-drag term so the decomposition stays
    #   strategic active (ex-cash) + cash drag = total active vs benchmark
    # and the reported total active / TWR are unchanged.
    cash_start = start_values.get(_CASH_SLEEVE, 0.0)
    cash_end   = end_values.get(_CASH_SLEEVE, 0.0)
    total_end  = sum(end_values.values())

    total_start_excash = total_start - cash_start
    total_end_excash   = total_end - cash_end
    if total_start_excash <= 0:
        empty = pd.DataFrame()
        empty.attrs["price_gaps"] = price_gaps
        empty.attrs["benchmark_gaps"] = benchmark_gaps
        empty.attrs["no_benchmark_sleeves"] = []
        return empty

    r_p_total_incl   = total_end / total_start - 1.0
    r_p_total_excash = total_end_excash / total_start_excash - 1.0
    cash_drag        = r_p_total_incl - r_p_total_excash   # negative when cash drags
    cash_weight      = cash_start / total_start if total_start else 0.0

    # ── Portfolio weights and returns per STRATEGIC sleeve (ex-cash) ──────────
    strategic_values = {s: mv for s, mv in start_values.items() if s != _CASH_SLEEVE}
    portfolio_weights = {
        sleeve: mv / total_start_excash for sleeve, mv in strategic_values.items()
    }
    portfolio_sleeve_returns = {
        sleeve: (end_values.get(sleeve, 0.0) / mv - 1.0) if mv > 0 else 0.0
        for sleeve, mv in strategic_values.items()
    }

    # ── Benchmark sleeve returns (fetched above, before the holdings loop) ───
    # NaN sentinels must never reach brinson_fachler (they would poison the
    # effects and trip its algebra assert): gapped w_b>0 sleeves were purged
    # above; any remaining unpriceable benchmark (the cash sleeve, or a
    # zero-weight sleeve) falls back to 0.0 — which cancels algebraically when
    # w_b == 0 — and is tracked so it renders as N/A, never as a real 0% return.
    bm_sleeve_returns: dict[str, float] = {}
    fabricated_rb: set[str] = set()
    for s in benchmark_weights:
        v = bm_returns_raw.get(s)
        if v is None or pd.isna(v):
            bm_sleeve_returns[s] = 0.0
            fabricated_rb.add(s)
        else:
            bm_sleeve_returns[s] = float(v)

    # ── Align: strategic sleeves only (cash and gapped sleeves excluded from
    # both sides — a gapped sleeve's benchmark weight/return must not be
    # compared against a fabricated portfolio-side number) ────────────────────
    all_sleeves = sorted(
        (set(portfolio_weights) | set(benchmark_weights)) - {_CASH_SLEEVE} - gapped_sleeves
    )
    pw  = {s: portfolio_weights.get(s, 0.0)        for s in all_sleeves}
    bw  = {s: benchmark_weights.get(s, 0.0)        for s in all_sleeves}
    pr  = {s: portfolio_sleeve_returns.get(s, 0.0) for s in all_sleeves}
    # The 0.0 default is only reachable for sleeves outside benchmark_weights
    # (w_b == 0, e.g. "Unknown"), where r_b cancels algebraically in both the
    # sleeve's total effect and R_b_total — tracked below for N/A rendering.
    br  = {s: bm_sleeve_returns.get(s, 0.0)        for s in all_sleeves}

    # Sleeves whose displayed benchmark return would be a placeholder, not a
    # real market number: retained in the decomposition (their r_b cancels
    # with w_b == 0) but surfaced so render surfaces show N/A.
    no_benchmark_sleeves = sorted(
        s for s in all_sleeves if s in fabricated_rb or s not in bm_sleeve_returns
    )

    # Renormalize benchmark weights over the measured (non-gapped) universe so
    # they sum to 1.0 again, matching the portfolio weights (which already sum
    # to 1.0 over this same universe, since total_start_excash was computed
    # from the same purged start_values). Mirrors the renormalization
    # convention already used elsewhere in the app (src/risk.py's
    # run_risk_contribution) when a sleeve is missing from the available data.
    # A no-op when no sleeve is gapped, since benchmark_weights already sum to
    # 1.0 over all 9 sleeves in that case.
    bw_sum = sum(bw.values())
    if bw_sum <= 0:
        empty = pd.DataFrame()
        empty.attrs["price_gaps"] = price_gaps
        empty.attrs["benchmark_gaps"] = benchmark_gaps
        empty.attrs["no_benchmark_sleeves"] = []
        return empty
    bw = {s: w / bw_sum for s, w in bw.items()}

    bf = brinson_fachler(pw, bw, pr, br)
    # Operational-cash figures for the reconciliation bridge (read by the
    # Performance page). .attrs survives pickling / st.cache_data.
    bf.attrs["cash_drag"]        = cash_drag
    bf.attrs["cash_weight"]      = cash_weight
    bf.attrs["r_p_total_excash"] = r_p_total_excash
    bf.attrs["r_p_total_incl"]   = r_p_total_incl
    bf.attrs["price_gaps"]       = price_gaps
    bf.attrs["benchmark_gaps"]   = benchmark_gaps
    bf.attrs["no_benchmark_sleeves"] = no_benchmark_sleeves
    return bf
