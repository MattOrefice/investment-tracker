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
    roth_contribution_basis: float = 0.0,
    roth_is_qualified_age: bool = False,
) -> pd.DataFrame:
    """Rank every household holding by cost to convert to spendable cash, cheapest
    first. Returns a DataFrame with columns:
      symbol, account, tax_treatment, value, embedded_gain, tier, cost_to_cash,
      net_cash (value − cost), cumulative_net_cash (running total, cheapest first),
      note (plain-language reason).

    Tiers:
      1  taxable at a loss / small gain, and taxable cash/money-market — ~$0 tax;
         plus the Roth CONTRIBUTION-BASIS tranche (comes out first, free, any age).
      2  taxable with a meaningful embedded gain — 15% LTCG + PA on the gain.
      3  pre-tax IRA/401k (10% penalty + ordinary income on the whole withdrawal),
         and the Roth EARNINGS tranche when the owner is under 59½ — locked.

    Roth IRA — basis-aware ordering, NOT pro-rata. The IRS Roth withdrawal order is
    contributions first (tax- and penalty-free at any age), then earnings (free only
    at 59½+ and a 5-year account). That is an ordering over CUMULATIVE dollars, not a
    per-holding split — which security you sell is irrelevant to the cost — so the
    whole Roth is collapsed to two synthetic, fungible tranche rows:
      free_tranche     = min(roth_contribution_basis, roth_total)   # underwater-safe
      earnings_tranche = max(0, roth_total − free_tranche)
    The basis tranche is Tier 1 (net = gross). The earnings tranche is Tier 1 (free)
    when ``roth_is_qualified_age`` else Tier 3 (10% penalty + ordinary). basis/age
    come from the personal profile (src/personal_profile); with a zero basis this
    reduces to one locked earnings row = the whole Roth, the prior behavior.

    Within-tier tie-break: rows are ordered by explicit cost first, but a $0-cost
    Roth row (basis, or qualified earnings) ranks AFTER every $0-cost taxable row,
    never ahead of one on account of being the larger dollar amount. A Roth
    withdrawal has a real cost the model doesn't price — it permanently forfeits
    that dollar's future tax-free space, which a same-cost taxable sale does not —
    so taxable Tier-1 capacity is meant to exhaust before Roth basis is tapped.
    Only the ORDER shifts; tier assignment (and therefore tier totals and
    Accessible-now) is unchanged.
    """
    ord_rate = ordinary_rate(tax_profile)   # e.g. 22% federal + 3.07% PA
    ltcg = ltcg_rate(tax_profile)           # e.g. 15% federal + 3.07% PA
    penord = EARLY_WITHDRAWAL_PENALTY + ord_rate
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
        if tt == "roth_ira":
            continue    # Roth is aggregated into basis/earnings tranches below (ordering, not per-holding)
        value = float(r["current_value"])
        egv = r["embedded_gain"]
        gain_pct = r["gain_pct"]

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
        else:
            # Pre-tax (Traditional IRA, 401(k)/workplace, HSA): LOCKED — the WHOLE
            # withdrawal is penalized + ordinary-taxed if taken before 59½.
            cost = value * penord
            tier = 3
            note = "Pre-tax — 10% penalty + ordinary income on the whole withdrawal if before 59½"

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

    # ── Roth IRA — contribution-basis ordering (two fungible tranches) ───────────
    roth = merged[merged["tax_treatment"] == "roth_ira"]
    if not roth.empty:
        roth_total = float(roth["current_value"].sum())
        roth_account = str(roth["display_name"].iloc[0] or "Roth IRA")
        free_tranche = min(max(0.0, float(roth_contribution_basis)), roth_total)   # underwater-safe cap
        earnings_tranche = max(0.0, roth_total - free_tranche)

        if free_tranche > 0:
            rows.append({
                "symbol": "Roth IRA — contribution basis (free)",
                "account": roth_account, "tax_treatment": "roth_ira",
                "value": round(free_tranche, 2), "embedded_gain": None,
                "tier": 1, "cost_to_cash": 0.0, "net_cash": round(free_tranche, 2),
                "note": "Roth contributions come out first — tax- and penalty-free at any age (basis you entered)",
            })
        if earnings_tranche > 0:
            if roth_is_qualified_age:
                e_sym = "Roth IRA — earnings (qualified, tax-free)"
                e_tier, e_cost = 1, 0.0
                e_note = "Roth earnings — tax-free: you are 59½+ (a 5-year account is assumed, not verified)"
            else:
                e_sym = "Roth IRA — earnings (locked until 59½)"
                e_tier, e_cost = 3, earnings_tranche * penord
                e_note = "Roth earnings — locked: 10% penalty + ordinary income if withdrawn before 59½"
            rows.append({
                "symbol": e_sym,
                "account": roth_account, "tax_treatment": "roth_ira",
                "value": round(earnings_tranche, 2), "embedded_gain": None,
                "tier": e_tier, "cost_to_cash": round(float(e_cost), 2),
                "net_cash": round(earnings_tranche - float(e_cost), 2),
                "note": e_note,
            })

    if not rows:
        return pd.DataFrame(columns=[
            "symbol", "account", "tax_treatment", "value", "embedded_gain",
            "tier", "cost_to_cash", "net_cash", "cumulative_net_cash", "note"])

    out = (
        pd.DataFrame(rows)
        .assign(_roth_last=lambda d: (d["tax_treatment"] == "roth_ira").astype(int))
        .sort_values(["tier", "cost_to_cash", "_roth_last", "value"],
                     ascending=[True, True, True, False])
        .drop(columns="_roth_last")
        .reset_index(drop=True)
    )
    out["cumulative_net_cash"] = out["net_cash"].cumsum().round(2)
    return out
