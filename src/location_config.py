"""Asset-location configuration for the personal-mode Asset Location page.

Everything here is USER-EDITABLE assumption, deliberately kept as data (dicts)
rather than scattered constants in functions, so the whole tax/location model
can be reviewed and changed in one place.

Nothing here is a return forecast. SLEEVE_LOCATION_PRIORITY is an ORDINAL
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


# ── Sleeve location priority (ORDINAL — not a return forecast) ──────────────────
# 1 = highest expected return, most deserving of scarce Roth (tax-free) space;
# larger = less deserving. A sleeve ABSENT from this dict resolves to None,
# which means "not a deploy target" — it is explicitly NOT treated as the lowest
# priority. Ordinal only; never render these numbers as returns.
SLEEVE_LOCATION_PRIORITY: dict[str, int] = {
    "us_small_core":            1,
    "emerging_markets":         2,
    "us_large_quality":         3,
    "us_large_value":           4,
    "us_large_core":            5,
    "intl_developed":           6,
    "intl_all_exus":            6,
    "real_assets_reit":         7,
    "real_assets_commodities":  7,
    "real_assets_gold":         7,
    "core_fi_treasury":         8,
    "core_fi_credit":           8,
    "tips":                     9,
    "hedged_equity":           10,   # capped upside — worst use of Roth space
    "cash":                    11,
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


def sleeve_location_priority(sleeve: str) -> int | None:
    """Ordinal deploy priority for a sleeve, or None if it is not a deploy target.

    None is a first-class value meaning 'not a candidate for new cash' — callers
    must NOT coerce it to a large number and sort it last.
    """
    return SLEEVE_LOCATION_PRIORITY.get(sleeve)


def account_shelter_priority(tax_treatment: str) -> int | None:
    """Shelter priority for an account tax_treatment, or None if unknown."""
    return ACCOUNT_SHELTER_PRIORITY.get(tax_treatment)


def ltcg_rate(tax_profile: dict[str, float]) -> float:
    """Combined long-term capital-gains rate (federal + state)."""
    return float(tax_profile.get("federal_ltcg", 0.0)) + float(tax_profile.get("state_ltcg", 0.0))


def ordinary_rate(tax_profile: dict[str, float]) -> float:
    """Combined ordinary marginal rate (federal + state)."""
    return float(tax_profile.get("federal_marginal", 0.0)) + float(tax_profile.get("state_marginal", 0.0))
