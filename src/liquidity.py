"""Liquidity hierarchy for the personal-mode 'If you need cash' page.

The question this answers: to raise cash for a house or car in the next 5–10 years,
what do you sell first? That ranking is nearly the INVERSE of asset location. Money
in tax-advantaged accounts (Roth, both IRAs, 401k) is effectively LOCKED before 59½ —
a withdrawal triggers a 10% early-withdrawal penalty plus ordinary income tax — while
taxable brokerage is fully liquid, taxed only on the embedded gain. So holdings are
ranked by 'how cheaply can I turn this into spendable cash', cheapest first.

Reuses the existing model — compute_embedded_gain(positions) for per-holding gain +
cost basis (cash rows return NaN gain and are treated as immediately spendable), the
account tax_treatment for the wrapper, and TAX_PROFILE rates. No new tax derivation.
"""
from __future__ import annotations

import pandas as pd

from src.household import compute_embedded_gain
from src.location_config import ordinary_rate, ltcg_rate

# Early-withdrawal penalty on tax-advantaged accounts before age 59½.
EARLY_WITHDRAWAL_PENALTY: float = 0.10
# Embedded-gain % below which a taxable lot is "small gain" (Tier 1, minimal tax).
SMALL_GAIN_PCT: float = 10.0

TIER_LABEL: dict[int, str] = {
    1: "Tier 1 · free / cheap",
    2: "Tier 2 · moderate (15% LTCG)",
    3: "Tier 3 · locked (penalty + tax)",
}


def build_liquidity_ladder(
    positions_df: pd.DataFrame,
    accounts_df: pd.DataFrame,
    tax_profile: dict[str, float],
) -> pd.DataFrame:
    """Rank every household holding by cost to convert to spendable cash, cheapest
    first. Returns a DataFrame with columns:
      symbol, account, tax_treatment, value, embedded_gain, tier, cost_to_cash,
      net_cash (value − cost), cumulative_net_cash (running total, cheapest first),
      note (plain-language reason).

    Tiers:
      1  taxable at a loss / small gain, and taxable cash/money-market — ~$0 tax.
      2  taxable with a meaningful embedded gain — 15% LTCG + PA on the gain.
      3  Roth (contributions free, earnings penalized) and pre-tax IRA/401k
         (10% penalty + ordinary income on the whole withdrawal) — locked.
    """
    ord_rate = ordinary_rate(tax_profile)   # e.g. 22% federal + 3.07% PA
    ltcg = ltcg_rate(tax_profile)           # e.g. 15% federal + 3.07% PA
    eg, _ = compute_embedded_gain(positions_df)

    merged = (
        positions_df
        .merge(accounts_df[["pseudonym", "tax_treatment", "display_name"]], on="pseudonym", how="left")
        .merge(eg[["pseudonym", "symbol", "embedded_gain", "cost_basis", "gain_pct"]],
               on=["pseudonym", "symbol"], how="left")
    )

    rows: list[dict] = []
    for _, r in merged.iterrows():
        tt = r.get("tax_treatment")
        value = float(r["current_value"])
        egv = r["embedded_gain"]
        gain_pct = r["gain_pct"]
        basis = r["cost_basis"]

        if tt == "taxable":
            if pd.isna(egv):                    # cash / money-market — no gain, spendable now
                tier, cost, note = 1, 0.0, "Taxable cash — spendable immediately, no tax"
            else:
                egv = float(egv)
                cost = max(0.0, egv) * ltcg     # only gains are taxed; a loss owes nothing
                if egv <= 0 or (pd.notna(gain_pct) and float(gain_pct) < SMALL_GAIN_PCT):
                    tier, note = 1, "Taxable at a loss or small gain — minimal or no tax"
                else:
                    tier, note = 2, "Taxable with a meaningful gain — 15% LTCG + PA on the gain"
        elif tt == "roth_ira":
            growth = max(0.0, value - (float(basis) if pd.notna(basis) else 0.0))
            cost = growth * (EARLY_WITHDRAWAL_PENALTY + ord_rate)
            tier, note = 3, "Roth — contributions withdraw free; earnings penalized before 59½"
        else:                                   # traditional_ira / workplace_plan / hsa — all pre-tax
            cost = value * (EARLY_WITHDRAWAL_PENALTY + ord_rate)
            tier, note = 3, "Pre-tax — 10% penalty + ordinary income on the whole withdrawal"

        rows.append({
            "symbol": r["symbol"],
            "account": r.get("display_name") or "",
            "tax_treatment": tt,
            "value": round(value, 2),
            "embedded_gain": (round(float(egv), 2) if pd.notna(egv) else None),
            "tier": tier,
            "cost_to_cash": round(float(cost), 2),
            "net_cash": round(value - float(cost), 2),
            "note": note,
        })

    if not rows:
        return pd.DataFrame(columns=[
            "symbol", "account", "tax_treatment", "value", "embedded_gain",
            "tier", "cost_to_cash", "net_cash", "cumulative_net_cash", "note"])

    out = (
        pd.DataFrame(rows)
        .sort_values(["tier", "cost_to_cash", "value"], ascending=[True, True, False])
        .reset_index(drop=True)
    )
    out["cumulative_net_cash"] = out["net_cash"].cumsum().round(2)
    return out
