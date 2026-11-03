"""Liquidity hierarchy for the personal-mode 'If you need cash' page.

The question this answers: to raise cash for a house or car in the next 5–10 years,
what do you sell first? That ranking is nearly the INVERSE of asset location. Money
in tax-advantaged accounts (Roth, both IRAs, 401k) is effectively LOCKED before 59½ —
a withdrawal triggers a 10% early-withdrawal penalty plus ordinary income tax — while
taxable brokerage is fully liquid, taxed only on the embedded gain. So holdings are
ranked by 'how cheaply can I turn this into spendable cash', cheapest first.

Reuses the existing model — compute_embedded_gain(positions) for per-holding gain +
cost basis (cash rows return NaN gain and are treated as immediately spendable), the
account tax_treatment for the wrapper, and the tax profile's rates. No new tax
derivation.

THE TAX PROFILE THIS PAGE PRICES WITH is ``residence_tax_profile``: the federal rates
from TAX_PROFILE, and state and local rates from the owner's tax residence, which
lives in the gitignored private/personal_profile.json (personal_profile.
load_tax_residence). The committed tree names no home state for this page. TAX_PROFILE's
own state keys are not read here.
"""
from __future__ import annotations

import pandas as pd

from src.household import compute_embedded_gain
from src.ingestion.fidelity import PENDING_ACTIVITY_LABEL, is_pending_activity
from src.location_config import TAX_PROFILE, ordinary_rate, ltcg_rate

# Early-withdrawal penalty on tax-advantaged accounts before age 59½.
EARLY_WITHDRAWAL_PENALTY: float = 0.10
# Embedded-gain % below which a taxable lot is "small gain" (Tier 1, minimal tax).
SMALL_GAIN_PCT: float = 10.0

TIER_LABEL: dict[int, str] = {
    1: "Tier 1 · free / cheap",
    2: f"Tier 2 · moderate ({TAX_PROFILE['federal_ltcg']:.0%} LTCG)",   # federal, read
    3: "Tier 3 · locked (penalty + tax)",
}


def residence_tax_profile(residence: dict,
                          federal: "dict[str, float] | None" = None) -> dict[str, float]:
    """The rates this page prices with: federal from ``federal`` (TAX_PROFILE by
    default), state and local summed over the residence's jurisdictions. With no
    jurisdictions the state and local rates are 0.0, and ``gain_tax_note`` says that no
    state or local tax is included rather than letting the 0 pass for a rate."""
    federal = TAX_PROFILE if federal is None else federal
    juris = residence.get("jurisdictions") or []
    return {
        "federal_marginal": float(federal["federal_marginal"]),
        "federal_ltcg": float(federal["federal_ltcg"]),
        "state_marginal": round(sum(float(j["ordinary_rate"]) for j in juris), 6),
        "state_ltcg": round(sum(float(j["gains_rate"]) for j in juris), 6),
    }


def residence_names(residence: dict) -> str:
    """'A', 'A and B', 'A, B and C' — the jurisdictions the residence names, or ''."""
    names = [str(j["name"]) for j in (residence.get("jurisdictions") or [])]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def gain_tax_note(tax_profile: dict[str, float], tax_location: str) -> str:
    """The tax a taxable gain is priced at, in words read from the profile: the combined
    rate, then its parts. ``tax_location`` names the state and local jurisdictions; ''
    with a 0 state rate says that none is included."""
    fed = float(tax_profile["federal_ltcg"])
    total = ltcg_rate(tax_profile)
    if tax_location:
        return f"{total:.2%} on the gain: {fed:.0%} federal + {tax_location}"
    if float(tax_profile["state_ltcg"]) == 0.0:
        return (f"{total:.2%} on the gain: {fed:.0%} federal only; no state or local tax "
                f"is included")
    return f"{total:.2%} on the gain: {fed:.0%} federal + {tax_profile['state_ltcg']:.2%} state"


def build_liquidity_ladder(
    positions_df: pd.DataFrame,
    accounts_df: pd.DataFrame,
    tax_profile: dict[str, float],
    roth_contribution_basis: float = 0.0,
    roth_is_qualified_age: bool = False,
    tax_location: str = "",
) -> pd.DataFrame:
    """Rank every household holding by cost to convert to spendable cash, cheapest
    first. Returns a DataFrame with columns:
      symbol, account, tax_treatment, value, embedded_gain, tier, cost_to_cash,
      net_cash (value − cost − pending_netted), cumulative_net_cash (running total,
      cheapest first), pending_netted, note (plain-language reason).

    PENDING ACTIVITY (#705). The export's pending-activity line is cash in its account.
    A positive amount is a cash rung of its own. A negative amount is cash already
    spoken for: it nets against the account's cash rungs first (rows with no embedded
    gain), then against the account's other rungs in ladder order, cheapest first. A
    rung it reaches carries the amount in ``pending_netted``, and its value and net
    cash fall by it, never below the rung's cost: so no rung or tier is negative, the
    running total never falls, and the account's rungs sum to its total in the export.
    A Roth's line is in the Roth total its tranches split. What no rung of the account
    can cover is in ``attrs["pending_uncovered"]`` ({account: dollars}).

    Tiers:
      1  taxable at a loss / small gain, and taxable cash/money-market — ~$0 tax;
         plus the Roth CONTRIBUTION-BASIS tranche (comes out first, free, any age).
      2  taxable with a meaningful embedded gain — the profile's long-term rate on the
         gain, its note naming the parts (gain_tax_note; ``tax_location`` names the
         state and local jurisdictions).
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
    ord_rate = ordinary_rate(tax_profile)   # federal + state + local ordinary
    ltcg = ltcg_rate(tax_profile)           # federal LTCG + state + local on gains
    penord = EARLY_WITHDRAWAL_PENALTY + ord_rate
    gain_note = gain_tax_note(tax_profile, tax_location)
    eg, _ = compute_embedded_gain(positions_df)

    merged = (
        positions_df
        .merge(accounts_df[["pseudonym", "tax_treatment", "display_name"]], on="pseudonym", how="left")
        .merge(eg[["pseudonym", "symbol", "embedded_gain", "cost_basis", "gain_pct"]],
               on=["pseudonym", "symbol"], how="left")
    )

    rows: list[dict] = []
    pending: dict[str, float] = {}      # account -> its negative pending, to net below
    for _, r in merged.iterrows():
        tt = r.get("tax_treatment")
        if tt == "roth_ira":
            continue    # Roth is aggregated into basis/earnings tranches below (ordering, not per-holding)
        value = float(r["current_value"])
        if is_pending_activity(r["symbol"]):
            account = r.get("display_name") or ""
            if value < 0:
                pending[account] = pending.get(account, 0.0) + value
                continue
            if value == 0:
                continue
            if tt == "taxable":
                tier, cost, note = 1, 0.0, "Pending activity — cash in this account, no tax"
            else:
                tier, cost = 3, value * penord
                note = ("Pending activity — cash in a pre-tax account: 10% penalty + "
                        "ordinary income on the whole withdrawal if before 59½")
            rows.append({
                "symbol": PENDING_ACTIVITY_LABEL, "account": account, "tax_treatment": tt,
                "value": round(value, 2), "embedded_gain": None, "tier": tier,
                "cost_to_cash": round(float(cost), 2), "net_cash": round(value - float(cost), 2),
                "note": note, "_cash": True,
            })
            continue
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
                    tier, note = 2, f"Taxable with a meaningful gain — {gain_note}"
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
            "_cash": pd.isna(egv),
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
        empty = pd.DataFrame(columns=[
            "symbol", "account", "tax_treatment", "value", "embedded_gain",
            "tier", "cost_to_cash", "net_cash", "cumulative_net_cash", "pending_netted",
            "note"])
        empty.attrs["pending_uncovered"] = {a: round(-v, 2) for a, v in pending.items()}
        return empty

    out = (
        pd.DataFrame(rows)
        .assign(_roth_last=lambda d: (d["tax_treatment"] == "roth_ira").astype(int),
                _cash=lambda d: d["_cash"].fillna(False).astype(bool))
        .sort_values(["tier", "cost_to_cash", "_roth_last", "value"],
                     ascending=[True, True, True, False])
        .drop(columns="_roth_last")
        .reset_index(drop=True)
    )
    out["pending_netted"] = 0.0
    uncovered: dict[str, float] = {}
    for account, amount in sorted(pending.items()):
        left = -amount
        mine = out[out["account"] == account]
        # The account's cash first, then its other rungs in ladder order.
        for i in list(mine.index[mine["_cash"]]) + list(mine.index[~mine["_cash"]]):
            if left <= 0.005:
                break
            room = float(out.at[i, "net_cash"])
            take = min(room, left)
            if take <= 0:
                continue
            out.at[i, "pending_netted"] = round(take, 2)
            out.at[i, "value"] = round(float(out.at[i, "value"]) - take, 2)
            out.at[i, "net_cash"] = round(room - take, 2)
            out.at[i, "note"] = (f"{out.at[i, 'note']} · {take:,.2f} of it covers this "
                                 "account's pending activity")
            left -= take
        if left > 0.005:
            uncovered[account] = round(left, 2)
    out = out.drop(columns="_cash")
    out["cumulative_net_cash"] = out["net_cash"].cumsum().round(2)
    out.attrs["pending_uncovered"] = uncovered
    return out
