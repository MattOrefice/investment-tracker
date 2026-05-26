"""Capital deployment engine — contribution allocation and band-breach rebalancing.

Pure functions only — no DB access. DB-backed helpers live in the page layer.
"""
from __future__ import annotations

import pandas as pd

_CASH_SLEEVE = "Cash / SPAXX"
_CASH_TICKER = "SPAXX"

SUM_INVARIANT_TOLERANCE = 0.10


def compute_drift(
    sleeve_weights: dict[str, float],
    saa_targets: dict[str, float],
    saa_bands: dict[str, float],
) -> pd.DataFrame:
    """
    Compute per-sleeve drift vs SAA targets.

    Args:
        sleeve_weights: actual weight per sleeve (fractions, should sum ~1)
        saa_targets:    target weight per sleeve (fractions)
        saa_bands:      symmetric ±tolerance per sleeve (fractions)

    Returns DataFrame indexed by sleeve name with columns:
        Actual Weight, Target Weight, Band, Drift, In Band.
    Drift = actual − target (negative = underweight).
    """
    rows = []
    for sleeve, actual in sleeve_weights.items():
        target = saa_targets.get(sleeve, 0.0)
        band = saa_bands.get(sleeve, 0.02)
        drift = round(actual - target, 6)
        rows.append(
            {
                "Sleeve":        sleeve,
                "Actual Weight": actual,
                "Target Weight": target,
                "Band":          band,
                "Drift":         drift,
                "In Band":       abs(drift) <= band,
            }
        )
    return pd.DataFrame(rows).set_index("Sleeve")


def suggest_buys(
    drift_df: pd.DataFrame,
    portfolio_value: float,
    cash_to_deploy: float,
    ticker_to_sleeve: dict[str, str],
    prices: dict[str, float],
) -> pd.DataFrame:
    """
    Suggest buy orders to bring underweight sleeves toward target.

    Only considers sleeves with Drift < 0 (underweight). The Cash / SPAXX
    sleeve is never a buy target — it is the funding source. SPAXX ticker
    is excluded from suggestions regardless of sleeve mapping.

    Allocation:
        shortfall_i = |Drift_i| × portfolio_value
        if sum(shortfall) <= cash_to_deploy:  fill each sleeve completely
        else:                                  allocate proportionally to shortfall

    Multi-holding sleeves (e.g. Real Assets → VNQ + PDBC) receive an equal
    split across their tickers.

    Args:
        drift_df:        result of compute_drift()
        portfolio_value: current total portfolio value in dollars
        cash_to_deploy:  dollars to invest
        ticker_to_sleeve: ticker → sleeve name
        prices:          ticker → current price

    Returns DataFrame with columns:
        Ticker, Sleeve, Price, Suggested $, Suggested Shares.
    Empty DataFrame if no underweight non-cash sleeves or cash <= 0.
    """
    _EMPTY = pd.DataFrame(
        columns=["Ticker", "Sleeve", "Price", "Suggested $", "Suggested Shares"]
    )

    if cash_to_deploy <= 0 or portfolio_value <= 0:
        return _EMPTY

    underweight = drift_df[
        (~drift_df["In Band"]) & (drift_df["Drift"] < 0) & (~drift_df.index.isin([_CASH_SLEEVE]))
    ].copy()
    if underweight.empty:
        return _EMPTY

    underweight["Shortfall $"] = underweight["Drift"].abs() * portfolio_value

    total_shortfall = underweight["Shortfall $"].sum()
    if total_shortfall <= cash_to_deploy:
        allocations: dict[str, float] = underweight["Shortfall $"].to_dict()
    else:
        allocations = {
            sleeve: (shortfall / total_shortfall) * cash_to_deploy
            for sleeve, shortfall in underweight["Shortfall $"].items()
        }

    # Build sleeve → [tickers], only tickers with known prices (excludes benchmarks)
    sleeve_to_tickers: dict[str, list[str]] = {}
    for ticker, sleeve in ticker_to_sleeve.items():
        if ticker == _CASH_TICKER:
            continue
        if prices.get(ticker, 0.0) > 0:
            sleeve_to_tickers.setdefault(sleeve, []).append(ticker)

    rows = []
    for sleeve in sorted(allocations, key=lambda s: -allocations[s]):
        dollars = allocations[sleeve]
        tickers_in_sleeve = sorted(sleeve_to_tickers.get(sleeve, []))
        if not tickers_in_sleeve:
            continue
        per_ticker = dollars / len(tickers_in_sleeve)
        for ticker in tickers_in_sleeve:
            price = prices[ticker]   # guaranteed > 0 by filter above
            rows.append(
                {
                    "Ticker":           ticker,
                    "Sleeve":           sleeve,
                    "Price":            round(price, 2),
                    "Suggested $":      round(per_ticker, 2),
                    "Suggested Shares": round(per_ticker / price, 6),
                }
            )

    return pd.DataFrame(rows) if rows else _EMPTY


def suggest_contributions(
    portfolio_value: float,
    cash_to_deploy: float,
    sleeve_weights: dict[str, float],
    saa_targets: dict[str, float],
    ticker_to_sleeve: dict[str, str],
    prices: dict[str, float],
) -> pd.DataFrame:
    """
    Suggest how to allocate new cash contributions across sleeves.

    Hybrid algorithm — close drift first, then maintain SAA policy on residual.

    Step 1: For each investable (non-cash) sleeve, compute current dollar
            shortfall vs SAA target:
                shortfall_i = portfolio_value × max(0, target_i − actual_i)

    Step 2: If sum(shortfall) >= cash: allocate proportionally to shortfall.
            Only underweight sleeves (shortfall > 0) receive cash.

    Step 3: If sum(shortfall) < cash:
            - Fully close all shortfalls.
            - Distribute residual = cash − sum(shortfall) across ALL
              investable sleeves proportionally to their target weights
              (normalized to exclude Cash / SPAXX).

    Cash / SPAXX is excluded; new cash is deployed into investable sleeves.

    Rationale column:
        "close drift"     — sleeve is below target, Step 2 allocation
        "mixed"           — sleeve is below target AND receives residual (Step 3)
        "maintain target" — sleeve is at/above target, residual only (Step 3)

    Args:
        portfolio_value:  current total portfolio value in dollars
        cash_to_deploy:   new cash to invest
        sleeve_weights:   current actual weights per sleeve (fractions)
        saa_targets:      SAA target weights per sleeve (fractions)
        ticker_to_sleeve: ticker → sleeve name
        prices:           ticker → current price

    Returns DataFrame with columns:
        Ticker, Sleeve, Rationale, Price, Suggested $, Suggested Shares.
    Empty DataFrame if cash <= 0 or no investable sleeves with ticker mappings.
    """
    _EMPTY = pd.DataFrame(
        columns=["Ticker", "Sleeve", "Rationale", "Price", "Suggested $", "Suggested Shares"]
    )

    if cash_to_deploy <= 0 or portfolio_value < 0:
        return _EMPTY

    # Investable = all sleeves except cash
    investable = {s: t for s, t in saa_targets.items() if s != _CASH_SLEEVE}
    investable_total = sum(investable.values())
    if not investable or investable_total <= 0:
        return _EMPTY

    V = portfolio_value
    X = cash_to_deploy

    # Dollar shortfall per sleeve (current deficit only — does not include
    # the proportional share of new cash each sleeve would need to stay flat)
    shortfalls = {
        sleeve: V * max(0.0, target - sleeve_weights.get(sleeve, 0.0))
        for sleeve, target in investable.items()
    }
    total_shortfall = sum(shortfalls.values())

    if total_shortfall >= X:
        # Step 2: proportional to shortfall; only underweight sleeves participate
        sleeve_alloc: dict[str, float] = {
            s: (sf / total_shortfall) * X
            for s, sf in shortfalls.items()
            if sf > 0
        }
        step = 2
    else:
        # Step 3: full close + residual proportional to normalized target weight
        residual = X - total_shortfall
        sleeve_alloc = {
            sleeve: shortfalls.get(sleeve, 0.0) + residual * (target / investable_total)
            for sleeve, target in investable.items()
        }
        step = 3

    def _rationale(sleeve: str) -> str:
        if step == 2:
            return "close drift"
        if shortfalls.get(sleeve, 0.0) > 0:
            return "mixed"
        if V * (sleeve_weights.get(sleeve, 0.0) - investable.get(sleeve, 0.0)) > 0.01:
            return "above target"
        return "maintain target"

    # Sleeve → [tickers], only tickers with known prices (excludes benchmarks)
    sleeve_to_tickers: dict[str, list[str]] = {}
    for ticker, sleeve in ticker_to_sleeve.items():
        if ticker == _CASH_TICKER or sleeve == _CASH_SLEEVE:
            continue
        if prices.get(ticker, 0.0) > 0:
            sleeve_to_tickers.setdefault(sleeve, []).append(ticker)

    rows = []
    raw_total = 0.0
    for sleeve in sorted(sleeve_alloc, key=lambda s: -sleeve_alloc[s]):
        dollars = sleeve_alloc[sleeve]
        if dollars < 0.005:
            continue
        tickers_in_sleeve = sorted(sleeve_to_tickers.get(sleeve, []))
        if not tickers_in_sleeve:
            continue
        per_ticker = dollars / len(tickers_in_sleeve)
        raw_total += dollars
        rationale = _rationale(sleeve)
        for ticker in tickers_in_sleeve:
            price = prices[ticker]   # guaranteed > 0 by filter above
            rows.append(
                {
                    "Ticker":           ticker,
                    "Sleeve":           sleeve,
                    "Rationale":        rationale,
                    "Price":            round(price, 2),
                    "Suggested $":      round(per_ticker, 2),
                    "Suggested Shares": round(per_ticker / price, 6),
                }
            )

    if rows:
        final_sum = sum(r["Suggested $"] for r in rows)
        assert abs(final_sum - X) <= SUM_INVARIANT_TOLERANCE, (
            f"suggest_contributions: Suggested $ sum {final_sum:.4f} ≠ cash {X:.4f}; "
            "check that all investable sleeves have at least one priced ticker"
        )
    return pd.DataFrame(rows) if rows else _EMPTY
