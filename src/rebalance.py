"""Rebalancing engine — buy-only, cash-deploy mode.

Pure functions only — no DB access. DB-backed helpers live in the page layer.
"""
from __future__ import annotations

import pandas as pd

_CASH_SLEEVE = "Cash / SPAXX"
_CASH_TICKER = "SPAXX"


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

    # Build sleeve → [tickers], excluding SPAXX ticker
    sleeve_to_tickers: dict[str, list[str]] = {}
    for ticker, sleeve in ticker_to_sleeve.items():
        if ticker == _CASH_TICKER:
            continue
        sleeve_to_tickers.setdefault(sleeve, []).append(ticker)

    rows = []
    for sleeve in sorted(allocations, key=lambda s: -allocations[s]):
        dollars = allocations[sleeve]
        tickers_in_sleeve = sorted(sleeve_to_tickers.get(sleeve, []))
        if not tickers_in_sleeve:
            continue
        per_ticker = dollars / len(tickers_in_sleeve)
        for ticker in tickers_in_sleeve:
            price = prices.get(ticker, 0.0)
            if price <= 0:
                continue
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
