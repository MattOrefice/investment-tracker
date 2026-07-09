"""Asset-location configuration for the personal-mode Asset Location page.

Everything here is USER-EDITABLE assumption, deliberately kept as data (dicts)
rather than scattered constants in functions, so the whole tax/location model
can be reviewed and changed in one place.

Nothing here is a return forecast. SLEEVE_PRIORITY_BY_ACCOUNT_TYPE is an ORDINAL
ranking of how deserving a sleeve is of scarce tax-free (Roth) space — it must
never be presented in the UI as an expected return.
"""
from __future__ import annotations

# ── Tax profile ────────────────────────────────────────────────────────────────
# USER-EDITABLE. Marginal + long-term-capital-gains rates used to size the
# annual benefit and the cost-to-realize of each suggested move. Rates are
# fractions (0.12 == 12%). Sources noted per line; update when your situation
# or the law changes.
TAX_PROFILE: dict[str, float] = {
    # Federal ordinary marginal bracket. Source: 2026 federal brackets for the
    # user's taxable income (12% bracket). Drives income-tax benefit of moving
    # ordinary-income assets into a shelter.
    "federal_marginal": 0.12,
    # Federal long-term capital-gains rate. Source: 0% LTCG bracket (taxable
    # income under the 0%-rate threshold). Makes taxable realizations cheap.
    "federal_ltcg": 0.0,
    # State ordinary marginal rate. Source: Pennsylvania flat personal income
    # tax, 3.07%.
    "state_marginal": 0.0307,
    # State LTCG rate. Source: PA taxes capital gains as ordinary income — no
    # preferential rate — so this equals state_marginal.
    "state_ltcg": 0.0307,
}

# Assumed annual distribution YIELD per sleeve (income throw-off, not total
# return). Sizes the income-shelter value at stake in the Asset Location register
# (build_location_register, via household._assumed_yield). USER-EDITABLE.
SLEEVE_ASSUMED_YIELD: dict[str, float] = {
    "real_assets_reit":        0.040,
    "real_assets_commodities": 0.015,
    "real_assets_gold":        0.000,
    "high_yield_fi":           0.060,
    "high_yield_muni":         0.045,
    "floating_rate":           0.055,
    "multi_sector_fi":         0.040,
    "core_fi_credit":          0.035,
    "core_fi_treasury":        0.040,
    "tips":                    0.025,
    "cash":                    0.045,
    "hedged_equity":           0.060,
    "single_stock":            0.020,
    "crypto":                  0.000,
    "liquid_alt":              0.020,
}
# Fallback yield for sleeves absent from the table (broad equity).
EQUITY_DEFAULT_YIELD: float = 0.018

# 0% federal long-term capital-gains bracket for 2026, modeled as a finite
# BUDGET rather than a rate: realized long-term gains are federally untaxed up to
# this remaining headroom, then taxed at 15%. USER-EDITABLE; shrinks with other
# taxable income and evaporates once wage income lifts past the 0%-rate ceiling.
LTCG_HEADROOM_2026: float = 2650.0

# Equity sleeves, ENUMERATED EXPLICITLY — never inferred from a substring match on
# the sleeve name. Used to size an account's absorbable-equity capacity. Excludes
# fixed income, real assets, cash, multi-asset/target-date blends, liquid alts,
# and crypto. USER-EDITABLE.
EQUITY_SLEEVES: frozenset[str] = frozenset({
    "us_large_core", "us_large_quality", "us_large_value", "us_large_growth",
    "us_small_core", "us_small_value", "us_mid_cap",
    "us_sector_tech", "us_sector_healthcare",
    "intl_developed", "intl_all_exus", "emerging_markets",
    "hedged_equity", "single_stock", "thematic",
})


# ── Account directability ──────────────────────────────────────────────────────
# Which household accounts can actually be traded (jointly) today, keyed by
# pseudonym. Directability does NOT follow tax_treatment (both taxable accounts
# differ) or managed_by (the IRAs are externally managed yet directable), so it
# is enumerated explicitly. USER-EDITABLE.
DIRECTABLE_PSEUDONYMS: frozenset[str] = frozenset({
    "acct_taxable_01",   # self-directed taxable
    "acct_roth_01",      # Roth IRA
    "acct_trad_ira_01",  # Traditional IRA
})


def is_directable(pseudonym: str) -> bool:
    """True if the account can be traded jointly today (vs. needs coordination)."""
    return pseudonym in DIRECTABLE_PSEUDONYMS


# The specific rollable 401(k) that the rollover_401k action describes, identified
# by pseudonym so {workplace_plan_value} is a definition, not an argmax over
# balances. USER-EDITABLE.
ROLLOVER_SOURCE_PSEUDONYM: str = "acct_wkpl_02"  # RFUTX 401k; Moody's PPP (acct_wkpl_01) is a separate former-employer plan, independently rollable, deliberately excluded from this figure


# ── Sleeve deploy priority, keyed BY ACCOUNT TYPE (ORDINAL — not a forecast) ─────
# Where new cash should go depends on the wrapper, so the ranking is per account
# type. 1 = most deserving of that account's space. A sleeve ABSENT from an
# account type's map is NOT a deploy target for that account (distinct from
# "ranked last") — it simply never appears. This shape replaces the old flat dict
# and makes both the deploy alias and a global exclusion list unnecessary.
#
# Roth / HSA — rank by expected return (highest-growth deserves never-taxed space).
# International is absent: a Roth forfeits the foreign tax credit.
_ROTH_PRIORITY: dict[str, int] = {
    "us_small_value":   1,   # AVUV — the SAA small-cap slot (small VALUE, not core)
    "emerging_markets": 2,
    "us_large_quality": 3,
    "us_large_value":   4,
    "us_large_core":    5,
    "us_small_core":    6,
    "thematic":         7,
}
# Traditional / workplace — rank by ordinary-income intensity (shelter the most
# ordinary-taxed income). high_yield_muni is absent (already exempt).
_PRETAX_PRIORITY: dict[str, int] = {
    "core_fi_credit":          1,
    "multi_sector_fi":         1,
    "high_yield_fi":           1,
    "floating_rate":           1,
    "core_fi_treasury":        2,
    "tips":                    2,
    "real_assets_reit":        3,
    "hedged_equity":           4,
    "real_assets_commodities": 5,
    "real_assets_gold":        5,
}
# Taxable — rank by tax efficiency (the most tax-efficient stays exposed to tax).
_TAXABLE_PRIORITY: dict[str, int] = {
    "us_large_core":    1,
    "intl_all_exus":    1,
    "intl_developed":   1,
    "us_large_quality": 2,
    "us_large_value":   2,
    "us_small_value":   2,
    "us_small_core":    2,
    "emerging_markets": 3,
    "high_yield_muni":  3,
    "single_stock":     4,
    "thematic":         5,
}
SLEEVE_PRIORITY_BY_ACCOUNT_TYPE: dict[str, dict[str, int]] = {
    "roth_ira":        _ROTH_PRIORITY,
    "hsa":             _ROTH_PRIORITY,
    "traditional_ira": _PRETAX_PRIORITY,
    "workplace_plan":  _PRETAX_PRIORITY,
    "taxable":         _TAXABLE_PRIORITY,
}


# ── Account shelter priority (derived from tax_treatment) ───────────────────────
# 1 = most valuable shelter (tax-free growth, no RMD). Used to decide the
# destination of a relocation and to spot premium-space waste.
ACCOUNT_SHELTER_PRIORITY: dict[str, int] = {
    "roth_ira":        1,   # never taxed again, no RMD
    "hsa":             1,   # triple-tax-advantaged
    "traditional_ira": 2,
    "workplace_plan":  2,
    "taxable":         3,
}

# Account types whose sales are NOT a taxable event (in-shelter moves are free).
TAX_ADVANTAGED_TREATMENTS = frozenset(
    {"roth_ira", "hsa", "traditional_ira", "workplace_plan"}
)


def priority_map_for(account_type: str) -> dict[str, int]:
    """The sleeve→rank map for an account type ({} if the type is unknown)."""
    return SLEEVE_PRIORITY_BY_ACCOUNT_TYPE.get(account_type, {})


def sleeve_priority(account_type: str, sleeve: str) -> int | None:
    """Deploy rank of a sleeve in an account type, or None if it is not a deploy
    target there. None is first-class ('not a candidate for new cash') — callers
    must NOT coerce it to a large number and sort it last.
    """
    return SLEEVE_PRIORITY_BY_ACCOUNT_TYPE.get(account_type, {}).get(sleeve)


def account_shelter_priority(tax_treatment: str) -> int | None:
    """Shelter priority for an account tax_treatment, or None if unknown."""
    return ACCOUNT_SHELTER_PRIORITY.get(tax_treatment)


def ltcg_rate(tax_profile: dict[str, float]) -> float:
    """Combined long-term capital-gains rate (federal + state)."""
    return float(tax_profile.get("federal_ltcg", 0.0)) + float(tax_profile.get("state_ltcg", 0.0))


def ordinary_rate(tax_profile: dict[str, float]) -> float:
    """Combined ordinary marginal rate (federal + state)."""
    return float(tax_profile.get("federal_marginal", 0.0)) + float(tax_profile.get("state_marginal", 0.0))
