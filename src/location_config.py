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
    # Federal ordinary marginal bracket. Source: 2026 federal single brackets at
    # ~$89K 2026 ordinary income (post-severance) — the 22% bracket (~$48.5K–$103K).
    # Drives the income-tax benefit of moving ordinary-income assets into a shelter.
    "federal_marginal": 0.22,
    # Federal long-term capital-gains rate. Source: 15% LTCG bracket — 2026 income
    # (~$89K) is above the 0%-rate ceiling (~$48.35K), so the 0% bracket is out of
    # reach this year and realized gains are federally taxed at 15%.
    "federal_ltcg": 0.15,
    # State ordinary marginal rate. Source: Pennsylvania flat personal income
    # tax, 3.07%.
    "state_marginal": 0.0307,
    # State LTCG rate. Source: PA taxes capital gains as ordinary income — no
    # preferential rate — so this equals state_marginal.
    "state_ltcg": 0.0307,
}

# Assumed annual distribution YIELD per sleeve (income throw-off, not total
# return). This table is INCOME ONLY — it must never encode tax status. A
# federally-exempt sleeve still throws off real income; its exemption lives in
# FEDERALLY_EXEMPT_SLEEVES below, not as a fake zero here. Sizes the income-shelter
# value at stake in the Asset Location register (build_location_register, via
# household._assumed_yield). USER-EDITABLE.
SLEEVE_ASSUMED_YIELD: dict[str, float] = {
    "real_assets_reit":        0.040,
    "real_assets_commodities": 0.015,
    "real_assets_gold":        0.000,
    "high_yield_fi":           0.060,
    "high_yield_muni":         0.040,   # yield ESTIMATE (income throw-off); federal exemption is a separate fact — see FEDERALLY_EXEMPT_SLEEVES
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

# Sleeves whose income is EXEMPT FROM FEDERAL TAX (municipal bonds). This set is a
# MODELING CORRECTION, INDEPENDENT of the yield table above: the yield there is an
# ESTIMATE of income thrown off; this set is a fact about how that income is taxed.
# A future reader should be able to revise one without touching the other.
#
# build_location_register uses it two ways: (1) a federally-exempt sleeve is charged
# the STATE rate only (PA 3.07%), never the combined federal+state ordinary rate, so
# its drag magnitude carries no phantom federal saving; and (2) it generates NO
# case A/B relocation action at all — with no federal tax to save, moving a muni into
# a pre-tax shelter would convert exempt interest into ordinary income at withdrawal,
# which is categorically wrong regardless of the dollar size (so the rule is a
# category, not a threshold). PA taxes out-of-state muni interest, and a national
# HY-muni fund (e.g. GHYIX, sleeve high_yield_muni) is mostly out-of-state, so PA's
# 3.07% still applies: the exemption is federal, not total. Keyed on the SLEEVE,
# never a symbol list. USER-EDITABLE.
FEDERALLY_EXEMPT_SLEEVES: frozenset[str] = frozenset({"high_yield_muni"})

# Minimum annual income-shelter benefit for a register row to be SURFACED as an
# individual action. This is a PRESENTATION THRESHOLD, not a modeling claim: a row
# below it is real drag — it still counts in the aggregate drag KPI and in every
# group's own totals — but a sub-dollar "relocation" (e.g. a $0.43/yr row) is a
# rounding error, not a decision, so build_location_register tags it surfaced=False
# and the page omits it from the per-group action tables. Every dollar figure still
# sums every row, filtered or not. USER-EDITABLE.
MIN_ANNUAL_BENEFIT: float = 1.00

# 2026 ordinary income (post-severance) and the single-filer 0% long-term-cap-gains
# ceiling. The 0% LTCG bracket is a finite BUDGET, not a rate: realized long-term
# gains are federally untaxed up to (ceiling − ordinary income), then taxed at 15%
# (TAX_PROFILE['federal_ltcg']). USER-EDITABLE. With ~$89K income above the ~$48.35K
# ceiling, the remaining headroom is $0 — the 0% bracket is gone this year, so every
# realized gain is taxed at 15% federal + PA's flat 3.07%.
ORDINARY_INCOME_2026: float = 89_000.0
LTCG_0_BRACKET_CEILING_SINGLE_2026: float = 48_350.0
LTCG_HEADROOM_2026: float = max(0.0, LTCG_0_BRACKET_CEILING_SINGLE_2026 - ORDINARY_INCOME_2026)

# ── IRS 2026 figures — verify annually (tax year 2026) ──────────────────────────
# STATIC tax-law limits — contribution ceilings and income/eligibility phase-outs.
# These are NOT portfolio figures: they are named constants (never scattered dollar
# literals) so the Asset Location page's ideal-location reference table templates
# every amount from here. Re-verify against the IRS annual inflation-adjustment
# release each year and bump the _2026 suffix when the tax year rolls. Phase-outs
# are (start, end) ranges — the deduction/eligibility fades linearly across them.
IRA_CONTRIB_LIMIT_2026: float = 7_500.0            # combined Traditional + Roth (per person, not per account)
IRA_CATCHUP_50_2026: float = 1_100.0               # additional, age 50+
WORKPLACE_ELECTIVE_DEFERRAL_2026: float = 24_500.0  # 401(k)/403(b) employee elective deferral
WORKPLACE_CATCHUP_50_2026: float = 8_000.0         # additional, age 50+
WORKPLACE_CATCHUP_60_63_2026: float = 11_250.0     # additional, age 60-63 (SECURE 2.0 higher catch-up)
WORKPLACE_415C_TOTAL_2026: float = 72_000.0        # total employee + employer (IRC 415(c))
ROTH_MAGI_PHASEOUT_SINGLE_2026: tuple[float, float] = (153_000.0, 168_000.0)   # single / head of household
ROTH_MAGI_PHASEOUT_MFJ_2026: tuple[float, float] = (242_000.0, 252_000.0)      # married filing jointly
# Traditional IRA deduction phase-out applies only when the contributor is covered
# by a workplace plan; there is no income limit to CONTRIBUTE (only to deduct).
TRAD_IRA_DEDUCTION_PHASEOUT_SINGLE_2026: tuple[float, float] = (81_000.0, 91_000.0)   # single, covered by a plan
TRAD_IRA_DEDUCTION_PHASEOUT_MFJ_2026: tuple[float, float] = (129_000.0, 149_000.0)    # MFJ, contributor covered
HSA_CONTRIB_LIMIT_SELF_2026: float = 4_400.0       # self-only HDHP coverage
HSA_CONTRIB_LIMIT_FAMILY_2026: float = 8_750.0     # family HDHP coverage
HSA_CATCHUP_55_2026: float = 1_000.0               # additional, age 55+

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
    # thematic is deliberately ABSENT: never-taxed Roth space is too scarce to
    # spend deploying new cash into single-theme sector bets. A thematic sleeve
    # already held in the Roth is a case-C cleanup row, not a deploy target.
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
