"""Tax-loss harvest candidate identification and replacement suggestions.

Pure computation module — no Streamlit imports, no DB access.
Current-price lookups for replacement tickers are handled in the page layer.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

# ── Thresholds and rates ──────────────────────────────────────────────────────

HARVEST_ACTION_THRESHOLD = 100.0   # minimum per-lot loss ($) for action recommendation
HARVEST_PCT_THRESHOLD    = 0.05    # minimum per-lot loss (%) for action recommendation

# No rate is typed here (#727). The estimate prices with the federal profile
# (personal_profile.load_federal_tax) and the tax residence's state and local rates
# (personal_profile.load_tax_residence): the owner's in personal mode, the synthetic
# demo profiles in demo. harvest_rates reads them.

WASH_SALE_WINDOW_DAYS = 30


def harvest_rates(federal: dict, residence: dict) -> dict:
    """The rates a harvested loss is priced at, from load_federal_tax's and
    load_tax_residence's results, through liquidity.residence_tax_profile, the profile
    Liquidity and Asset Location price with:
        {st_rate, lt_rate, tax_year, niit, state_st, state_lt, where,
         residence_problem, problem}

    A short-term loss offsets gains taxed at the ordinary rate, a long-term loss gains
    taxed at the long-term gains rate. The net investment income tax reaches every gain
    (#724), so where it applies it is added to both, and so are the residence's state
    and local rates, summed over its jurisdictions: ``where`` names them. With no
    jurisdictions they are 0.0 and ``residence_problem`` says why none is included.
    With no federal profile the rates are None and ``problem`` says why: the estimate is
    then not priced, rather than priced at a typed rate.
    """
    if federal.get("problem") or federal.get("federal_marginal") is None:
        return {"st_rate": None, "lt_rate": None, "tax_year": None, "niit": None,
                "state_st": None, "state_lt": None, "where": "",
                "residence_problem": residence.get("problem"),
                "problem": federal.get("problem") or "no federal rates"}
    from src.liquidity import residence_names, residence_tax_profile
    tax = residence_tax_profile(residence, federal)
    return {"st_rate": tax["federal_marginal"] + tax["niit"] + tax["state_marginal"],
            "lt_rate": tax["federal_ltcg"] + tax["niit"] + tax["state_ltcg"],
            "tax_year": tax["tax_year"], "niit": tax["niit"],
            "state_st": tax["state_marginal"], "state_lt": tax["state_ltcg"],
            "where": residence_names(residence),
            "residence_problem": residence.get("problem"), "problem": None}

# ── Replacement map ───────────────────────────────────────────────────────────
# ticker → (replacement_ticker | None, rationale, wash_sale_risk_level)
# risk_level ∈ {"clean", "moderate", "high"}
# Entries with risk_level == "high" are never surfaced as actionable swaps.

REPLACEMENT_MAP: dict[str, tuple[str | None, str, str]] = {
    "VOO": (
        "IVV",
        "S&P 500 exposure via a different issuer (iShares). Tracks the same index under "
        "a different fund structure, which standard tax practice does not treat as "
        "substantially identical.",
        "clean",
    ),
    "VTV": (
        "IUSV",
        "Large-cap value via iShares Core S&P U.S. Value. Different index provider and "
        "construction methodology from VTV's CRSP multi-metric screen.",
        "clean",
    ),
    "SPHQ": (
        "DGRO",
        "Quality-tilt via iShares Core Dividend Growth. Different index methodology "
        "(dividend growth + quality screen vs. SPHQ's accruals-adjusted quality screen). "
        "Factor overlap is partial; monitor exposure drift.",
        "moderate",
    ),
    "AVUV": (
        "DFSV",
        "Small-cap value + profitability factor via Dimensional (DFSV). Same factor "
        "targets, different fund family and portfolio construction.",
        "clean",
    ),
    "VEA": (
        "IEFA",
        "Developed ex-US via iShares MSCI EAFE IMI. Different index (MSCI EAFE IMI vs "
        "FTSE Developed All Cap ex-US); comparable country and factor exposure.",
        "clean",
    ),
    "IDHQ": (
        "IQLT",
        "International quality via iShares MSCI Intl Quality Factor. Different index "
        "provider (MSCI vs S&P) and screen construction: MSCI sorts on ROE, earnings "
        "variability, and leverage without SPHQ's accruals adjustment. Factor overlap is "
        "partial, the same relationship SPHQ and QUAL have domestically.",
        "moderate",
    ),
    "AVIV": (
        "DFIV",
        "International large value via Dimensional. Same value-plus-profitability "
        "targets, different fund family and portfolio construction: the AVUV/DFSV "
        "relationship applied abroad.",
        "clean",
    ),
    "AVDV": (
        "DISV",
        "International small value via Dimensional. Same size-value-profitability "
        "targets, different fund family and portfolio construction.",
        "clean",
    ),
    "IEMG": (
        "VWO",
        "Emerging markets via Vanguard FTSE EM ETF. Different index provider (FTSE vs "
        "MSCI); modestly different country weights and China exposure.",
        "clean",
    ),
    "VGIT": (
        "IEI",
        # The durations are no longer typed (#462): the points, a comparable duration here
        # and a shorter one for VTIP, stand without figures no table holds.
        "Intermediate US Treasury via iShares 3-7 Year Treasury. Different issuer; "
        "comparable duration.",
        "clean",
    ),
    "SCHP": (
        "VTIP",
        "TIPS via Vanguard Short-Term Inflation-Protected Securities. Different issuer; "
        "shorter duration, a modest duration shift accepted "
        "for a {{w:TIPS}} sleeve.",
        "moderate",
    ),
    "VNQ": (
        "SCHH",
        "US REIT exposure via Schwab US REIT ETF. Different issuer; similar broad "
        "REIT index construction.",
        "clean",
    ),
    "PDBC": (
        None,
        "Commodity ETF replacement carries elevated wash-sale risk. PDBC and DBC track "
        "the same DBIQ index family; the IRS has not ruled definitively on whether "
        "these are substantially identical. Consult a tax advisor before harvesting "
        "this lot.",
        "high",
    ),
    "SPAXX": (
        None,
        "Cash equivalent money market. No harvest case applies: the position carries no "
        "unrealized loss risk.",
        "clean",
    ),
}


# ── Core computation ──────────────────────────────────────────────────────────

def compute_harvest_candidates(
    lots: pd.DataFrame,
    action_threshold: float = HARVEST_ACTION_THRESHOLD,
    pct_threshold: float = HARVEST_PCT_THRESHOLD,
    *,
    st_tax_rate: float | None,
    lt_tax_rate: float | None,
    as_of: date | None = None,
) -> list[dict]:
    """
    Return harvest candidate lots sorted descending by estimated_tax_benefit, or by
    unrealized loss when the benefit is not priced.

    A lot qualifies only when ALL three conditions hold:
      - unrealized_gl < 0 (is a loss)
      - abs(unrealized_gl) >= action_threshold
      - abs(unrealized_gl_pct) >= pct_threshold

    Tax benefit uses the lot's tax_status:
      ST loss → benefit = abs(loss) × st_tax_rate
      LT loss → benefit = abs(loss) × lt_tax_rate
    The rates have no default (#727): the caller passes harvest_rates' figures. A rate
    of None leaves that lot's estimated_tax_benefit None — not priced, never $0.

    Entries in REPLACEMENT_MAP with risk_level == "high" are not surfaced as
    actionable swaps; replacement_ticker is set to None with an explanatory
    rationale already embedded in the map entry.

    Args:
        lots: DataFrame from get_lot_inventory().
        action_threshold: minimum dollar loss magnitude (default 100.0).
        pct_threshold: minimum fractional loss magnitude (default 0.05 = 5%).
        st_tax_rate: rate for short-term losses (harvest_rates' st_rate), or None.
        lt_tax_rate: rate for long-term losses (harvest_rates' lt_rate), or None.
        as_of: reference date for wash-sale window (defaults to today).

    Returns:
        List of dicts, one per qualifying lot, sorted by estimated_tax_benefit desc.
        Each dict keys: lot_id, account_id, ticker, sleeve, shares, tax_status,
        cost_basis_per_share, current_price, market_value, unrealized_loss,
        unrealized_loss_pct, estimated_tax_benefit, replacement_ticker,
        replacement_rationale, wash_sale_start_date, wash_sale_end_date.
    """
    if lots.empty:
        return []

    from src.asof import today_et   # the wash-sale window renders: New York's date (#443)
    sale_date  = as_of or today_et()
    wash_start = sale_date - timedelta(days=WASH_SALE_WINDOW_DAYS)
    wash_end   = sale_date + timedelta(days=WASH_SALE_WINDOW_DAYS)

    candidates: list[dict] = []

    for _, lot in lots.iterrows():
        gl     = float(lot["unrealized_gl"])
        gl_pct = float(lot["unrealized_gl_pct"])

        # A lot that is not valued has no gain or loss to harvest (get_lot_inventory).
        if gl != gl or gl >= 0:
            continue
        if abs(gl) < action_threshold:
            continue
        if abs(gl_pct) < pct_threshold:
            continue

        ticker     = str(lot["ticker"])
        tax_status = str(lot["tax_status"])
        tax_rate   = st_tax_rate if tax_status == "ST" else lt_tax_rate
        benefit    = None if tax_rate is None else abs(gl) * tax_rate

        entry = REPLACEMENT_MAP.get(ticker)
        if entry is None:
            repl_ticker    = None
            repl_rationale = (
                "No replacement mapping defined for this ticker. "
                "Consult a tax advisor before executing a harvest."
            )
        else:
            repl_ticker, repl_rationale, repl_risk = entry
            if repl_risk == "high":
                repl_ticker = None  # rationale already contains the caution language

        candidates.append(
            {
                "lot_id":                int(lot["trade_id"]),
                # The account the lot sits in: a harvest is a sale in ONE account, and
                # the page can show several taxable accounts (#362).
                "account_id":            lot.get("account_id"),
                "ticker":                ticker,
                "sleeve":                str(lot["sleeve"]),
                "shares":                float(lot["shares"]),
                "tax_status":            tax_status,
                "cost_basis_per_share":  float(lot["cost_basis_per_share"]),
                "current_price":         float(lot["current_price"]),
                "market_value":          float(lot["market_value"]),
                "unrealized_loss":       abs(gl),
                "unrealized_loss_pct":   abs(gl_pct),
                "estimated_tax_benefit": benefit,
                "replacement_ticker":    repl_ticker,
                "replacement_rationale": repl_rationale,
                "wash_sale_start_date":  wash_start,
                "wash_sale_end_date":    wash_end,
            }
        )

    priced = all(c["estimated_tax_benefit"] is not None for c in candidates)
    key = "estimated_tax_benefit" if priced else "unrealized_loss"
    candidates.sort(key=lambda c: c[key], reverse=True)
    return candidates
