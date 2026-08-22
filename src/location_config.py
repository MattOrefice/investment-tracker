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
    # Federal ordinary marginal bracket — the 2026 single 22% bracket
    # (~$48.5K–$103K). Drives the income-tax benefit of moving ordinary-income
    # assets into a shelter. Re-check this against your own income when it moves
    # across a bracket boundary; the income itself is not stored here (see
    # private/personal_profile.json).
    "federal_marginal": 0.22,
    # Federal long-term capital-gains rate: the 15% LTCG bracket, i.e. income sits
    # above the 0%-rate ceiling (LTCG_0_BRACKET_CEILING_SINGLE_2026), so the 0%
    # bracket is out of reach and realized gains are federally taxed at 15%.
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
# household._assumed_yield_with_source). USER-EDITABLE.
SLEEVE_ASSUMED_YIELD: dict[str, float] = {
    "real_assets_reit":        0.040,
    "real_assets_gold":        0.000,
    "high_yield_fi":           0.060,
    "high_yield_muni":         0.040,   # yield ESTIMATE (income throw-off); federal exemption is a separate fact — see FEDERALLY_EXEMPT_SLEEVES
    "floating_rate":           0.055,
    "multi_sector_fi":         0.040,
    "core_fi_credit":          0.035,
    "core_fi_treasury":        0.040,
    # CONSTRUCTED, not assumed — see SLEEVE_YIELD_CONSTRUCTION below, which a test
    # re-adds. 2.35% real yield + 2.26% breakeven inflation. It shipped at 0.025, which
    # read as a real yield because it essentially was one (within 15bp of DFII10) and so
    # carried only half of what a TIPS holder is taxed on.
    "tips":                    0.0461,
    "cash":                    0.045,
    "hedged_equity":           0.060,
    "single_stock":            0.020,
    "crypto":                  0.000,
    "liquid_alt":              0.020,
    # ── Equity sleeves, added #210 PR 2. Each value is a benchmark proxy's
    # trailing-twelve-month distribution yield as of 2026-08-11, rounded to 4dp.
    # The proxy, its spread against every other cached candidate, and how much of
    # the sleeve is measurable from holdings all live in SLEEVE_YIELD_PROXY below —
    # read them together, because the spread is the honest content of the value.
    "us_large_core":           0.0092,
    "us_large_growth":         0.0036,
    "us_large_value":          0.0136,
    "us_large_quality":        0.0084,
    "us_small_core":           0.0088,
    "us_small_value":          0.0123,
    "us_sector_tech":          0.0044,
    "us_sector_healthcare":    0.0158,
    "intl_developed":          0.0311,
    "emerging_markets":        0.0170,
    "intl_quality":            0.0231,
    "intl_large_value":        0.0448,
    "intl_small_value":        0.0309,
}

# Where each equity entry above came from. A value without a declared basis is what
# #191 closed, so this map is not optional metadata — a test fails if an entry exists
# without one, and fails again if its comment omits the spread or the coverage.
#
# BENCHMARK THROUGHOUT, deliberately, including where a held ticker looks more apt.
# intl_large_value takes EFV (0% held) rather than AVIV (the sleeve's SAA carrier),
# because departing from the framing on one row is held-weighted reasoning applied
# selectively: thirteen entries meaning "benchmark" and one meaning "the ticker I plan
# to buy" is an undeclared basis again. The +85% spread is that entry's honest content.
#
# "N% held" is how much of the sleeve's HELD value is measurable from the cache at all
# — the reason a benchmark stands in. Where it is low the held-weighted alternative is
# not a better measurement, just a thinner one.
SLEEVE_YIELD_PROXY: dict[str, str] = {
    "us_large_core":        "IWB",   # +13% vs VOO 1.04%, SPY 0.98%; quarterly; 82.8% held cached
    "us_large_growth":      "IWF",   # +19% vs QQQ 0.42%; quarterly; 60.1% held cached
    "us_large_value":       "IWD",   # +34% vs VTV 1.81%; quarterly; only 7.8% held cached
    "us_large_quality":     "QUAL",  # +30% vs SPHQ 1.09%; quarterly; 100.0% held cached
    "us_small_core":        "IWM",   # spread UNMEASURED, only cached candidate; quarterly; 0.0% held cached
    "us_small_value":       "AVUV",  # spread UNMEASURED, only cached candidate; quarterly; 100.0% held cached
    "us_sector_tech":       "XLK",   # spread UNMEASURED, only cached candidate; quarterly; 100.0% held cached
    "us_sector_healthcare": "XLV",   # spread UNMEASURED, only cached candidate; quarterly; 100.0% held cached
    "intl_developed":       "EFA",   # -25% vs VEA 2.49%; semi-annual so the TTM is 2 payments; only 7.2% held cached
    "emerging_markets":     "EEM",   # +32% vs IEMG 2.25%; semi-annual so the TTM is 2 payments; only 4.7% held cached
    "intl_quality":         "IQLT",  # +20% vs IDHQ 1.93%; semi-annual so the TTM is 2 payments; 0.0% held cached
    "intl_large_value":     "EFV",   # +85% vs AVIV 2.42% — the widest in the table; semi-annual so the TTM is 2 payments; 0.0% held cached
    "intl_small_value":     "SCZ",   # +16% vs ISVL 3.02%, AVDV 2.67%; semi-annual so the TTM is 2 payments; 0.0% held cached
}

# Entries whose value is CONSTRUCTED from published series rather than measured from a
# proxy's distributions. Parallel to SLEEVE_YIELD_PROXY above and disjoint from it: a
# proxy entry declares ticker, spread and coverage; a constructed entry declares its
# formula, its component series and its as-of date. Same obligation either way — the
# value does not travel without its basis.
#
# A DICT AND NOT A COMMENT, because only a structure can be re-added. A test sums the
# components and asserts they equal the shipped value, so the arithmetic cannot drift
# from the prose describing it; a comment can be wrong forever. That test is the
# constructed-entry analogue of recomputing a proxy's TTM from the price cache.
SLEEVE_YIELD_CONSTRUCTION: dict[str, dict] = {
    "tips": {
        "formula":    "real_yield + breakeven_inflation",
        "components": {"DFII10": 0.0235,    # 10y TIPS real yield
                       "T10YIE": 0.0226},   # 10y breakeven inflation, market-implied
        "as_of":      "2026-07-20",
        "note": (
            "WHY CONSTRUCTED. A TIPS holder is taxed on the real coupon AND on the "
            "inflation accrual that pays no cash until maturity. The old 0.025 carried "
            "only the first, so the register understated the very sleeve whose tax "
            "treatment is the strongest argument for sheltering it. Neither component "
            "is authored: T10YIE is market-implied forward expectation, not an estimate "
            "of ours. "
            "WHY DEFENSIBLE. The two sum to 4.61% against DGS10's 4.60% nominal 10-year "
            "on the same date — the Fisher identity reproduced to 1bp from a third, "
            "independent series. Arithmetic validation, not a judgement to defend. "
            "APPROXIMATION, DECLARED. This is the 10-year point; SCHP holds "
            "broad-maturity TIPS, so a duration-matched real yield and breakeven would "
            "be more exact. #231 tracks it, and the sizing says why it can wait: the "
            "whole 2.50%-9.88% range moves the drag KPI by $1.68. "
            "FROZEN AND DATED, NOT LIVE. src/macro.py caches both series and could "
            "fetch them, but the yield table has no network dependency and does not "
            "vary by day. That property is worth more than the vintage."
        ),
    },
}


# ── Annual review cadence for the constructed entries (#231) ──────────────────
# DELIBERATELY NOT SIZED LIKE asof.MARKET_DATA_STALE_DAYS_FACTORS / _VALUATION (70/45).
# Those two are sized so that firing means a REFRESH CYCLE was missed, and
# tools/refresh_market_data.py is that cycle — five committed CSVs whose diff is
# reviewed. FRED is not in it: these series come from macro_cache, a runtime 24-hour
# cache that is never committed. So there is no cycle here to miss and no publication
# lag to size against, and borrowing 45 or 70 would import a justification that does
# not hold. The nearest-looking precedent settles the PATTERN (render it, stay silent
# while fresh) and not the number.
#
# REVIEW HYGIENE, NOT A RISK CONTROL — the measurement is why. Over 5,890 cached
# observations of DFII10 + T10YIE (2003-01-02 .. 2026-07-20, read 2026-08-17), a full
# year of staleness moves the constructed value a median 31bp, p90 76bp, and 272bp in
# the worst 365-day window in 23 years. At the $90.68 SCHP position and the 25.07%
# ordinary rate that is $0.07 / $0.17 / $0.62 on the drag total — against $1.68 for the
# ENTIRE 2.50%-9.88% candidate range weighed at #213. Nothing here is justifiable by
# materiality. The cadence exists so the entry gets LOOKED AT.
#
# 365 because this file already commits to exactly that cadence for the IRS figures
# later on ("verify annually" — IRA_CONTRIB_LIMIT_2026 and friends). A yield review
# riding the tax-year pass that has to happen anyway beats a second calendar nobody
# keeps. NOT ENFORCED BY A TEST, deliberately: an assertion that reddens as real data
# ages fails with no commit, blocks unrelated PRs, and makes bumping the date the
# cheapest fix — see the header in tests/test_yield_construction.py. It is rendered to
# the reader instead, which is what every other staleness surface here does.
YIELD_CONSTRUCTION_REVIEW_DAYS = 365

# Sleeves with exactly ONE cached candidate, so their spread is UNKNOWN rather than
# zero. Named explicitly because silence would read as the most certain when it is
# merely the least checked — the same asymmetry #191 removed from the yield table as a
# whole. Adding a second cached candidate for any of these makes its spread measurable
# and should remove it from this set.
PROXY_SPREAD_UNMEASURED: frozenset[str] = frozenset({
    "us_small_core", "us_small_value", "us_sector_tech", "us_sector_healthcare",
})
# NO FALLBACK. EQUITY_DEFAULT_YIELD used to sit here at 0.018 and catch every sleeve
# with no entry. It measured 2-4x too high for US equity and 25-39% too low for
# international, and it was silent — a reader could not see it had been applied. #210
# removed it in three steps: look through blends (PR 1), give the proxy-backed equity
# sleeves declared bases (PR 2), and delete the fallback so an unlisted sleeve RAISES
# (PR 3). A sleeve now resolves through SLEEVE_ASSUMED_YIELD, BLEND_SLEEVES or
# NOT_MODELLED_SLEEVES, or the resolver raises.
#
# THE BOUNDARY, because the sentence above used to run "…or the location model refuses
# to build" and that is wider than the artifact. The raise fires on an unlisted sleeve
# ON A MISLOCATED HOLDING, not on any unlisted sleeve in the book: build_location_register
# selects a case FIRST and a row matching none of A/B/C/D hits `continue`
# (src/household.py:837-855) before yield resolution is reached. A high-tax-efficiency
# holding sitting in the account the priority map ranks first for it therefore never
# consults its sleeve's yield, and an unlisted sleeve there stays silent.
# tests/test_asset_location.py's case-D fixture demonstrates it with a ZZZ row whose
# sleeve is in no config set. Related boundary: #217 (a symbol absent from securities is
# skipped earlier still, at the same `continue`).

# Sleeves that mix asset classes inside one fund, so no single sleeve yield describes
# them. They are NOT given a yield entry: they are LOOKED THROUGH via
# fund_compositions, and the yield is the weight-weighted yield of the underlying
# sleeves. GAOSX (multi_asset) and RFUTX/31564E540 (target_date) all carry full
# compositions on record, so this resolves from data rather than from a judgement —
# and it replaces the equity default they used to take, which was wrong in KIND, not
# merely in magnitude: a global allocation fund holding 22% treasuries was modelled at
# a US-equity yield. A blend with NO composition on record refuses (not_modelled)
# rather than falling back, because a fallback here is what this fixes. USER-EDITABLE.
BLEND_SLEEVES: frozenset[str] = frozenset({"multi_asset", "target_date"})

# Sleeves the model DECLINES to size, rather than guessing. Not a data gap waiting to
# be filled by a better default — a statement that no defensible number exists:
#
#   thematic     twelve holdings spanning cybersecurity, biotech, robotics, clean
#                energy, space and EM internet. Homogeneous in KIND (all equity, all
#                growth-tilted) but ZERO of the twelve are in the price cache, and
#                there is no benchmark proxy for "thematic" to declare a basis
#                against. A number here would be invented, not derived.
#   us_mid_cap   held (IJH) but likewise with no proxy in the cache. IWB and IWM
#                bracket it rather than represent it.
#
#   real_assets_commodities  held (PDBC). Unlike the three below, the problem is not
#                a missing proxy — PDBC *is* the holding, and its distributions are in
#                the cache. The problem is that a commodity fund distributes REALIZED
#                GAINS, not income, so a trailing-twelve-month yield measures what it
#                happened to sell that year and has no steady-state value. Measured at
#                each year-end from the committed cache: 0.97%, 1.70%, 0.00%, 40.38%,
#                13.15%, 4.18%, 4.41%, 2.84%. The shipped 1.50% and the 2.84% #210
#                proposed are both single-year snapshots of that range. Correcting one
#                to the other is #210's own opening principle violated — an invented
#                number replaced by a differently-invented one. Compounded by #278: the
#                register taxes every non-exempt sleeve at the ordinary rate, and
#                commodity distributions are ordinary/LTCG with no qualified treatment,
#                so BOTH inputs are wrong in different ways. A sleeve the model cannot
#                size on either axis is exactly what this set is for.
#
#   intl_all_exus  held (IXUS + VXUS, $19,387 — the largest of the equity sleeves).
#                The only all-ex-US candidate in the cache is EFA, which is
#                DEVELOPED-ONLY and excludes emerging markets: wrong in KIND for this
#                sleeve, the same error declined for multi_asset. Using it would buy a
#                number at the cost of the thing this workstream is for. The
#                structurally right fix is a fund_compositions row set for IXUS/VXUS
#                so they are looked through like any other blend of sleeves — filed,
#                not folded in here.
#
# Every OTHER equity sleeve has a benchmark proxy already cached, and takes a
# declared-basis entry instead — see SLEEVE_YIELD_PROXY. USER-EDITABLE.
NOT_MODELLED_SLEEVES: frozenset[str] = frozenset({
    "thematic", "us_mid_cap", "intl_all_exus", "real_assets_commodities",
})

# ── Tax character (#278) ───────────────────────────────────────────────────────
#
# WHAT INCOME IS, not how much of it there is. A MODELING CORRECTION, INDEPENDENT of
# the yield table above: the yield there ESTIMATES income thrown off; this declares
# how that income is taxed. A future reader should be able to revise one without
# touching the other.
#
# REPLACES FEDERALLY_EXEMPT_SLEEVES, which was deleted rather than kept alongside.
# That set was a boolean, and `state_only if exempt else ordinary` has exactly two
# outcomes — so US Treasury interest, which is federally taxed and STATE-EXEMPT, had
# nowhere to go. It is not a missing value; it is a missing dimension, and no set can
# hold a third state. Keeping the old set beside this table would also leave two
# places saying "muni", which is the mechanism that produced four copies of one
# sentence in #228 and two contradicting ones in #284.
#
# THREE FACTS PER MEMBER, and `treasury` is why one value cannot do. Treasury
# INTEREST is exempt from PA tax; a capital gain on a Treasury fund is an ordinary
# capital gain and PA taxes it. Collapse the three into one and either the interest
# is over-taxed or the gain is under-taxed — the same failure the boolean had, one
# level down. The two axes are genuinely independent, which the repo already knew on
# the one sleeve it applied to: PA taxes out-of-state muni interest, so a national
# HY-muni fund is federally exempt and state-taxed. `muni_in_state` is DECLARED AND
# UNUSED for exactly that reason — nothing held is one, and without it `treasury`
# reads as a special case with nowhere for the next state-exemption question to go.
#
# NOT DECLARED, deliberately: `section_1256` (60/40). JHEQX writes index options, but
# 60/40 governs the FUND's internal gains, not the character of what it distributes —
# those reach a holder as ordinary income. It is the member someone will want to add,
# and adding it would encode a real tax rule at the wrong level.
#
# AUTHORED, NOT MEASURED — the same standing limitation as the yield table, and the
# page says so in the same words rather than implying a measurement.
#
#     member -> (federal income treatment, PA taxes the income?, gain treatment)
#
# USER-EDITABLE.
TAX_CHARACTER: dict[str, tuple[str, bool, str]] = {
    "ordinary":          ("ordinary", True,  "ltcg"),
    "qualified":         ("ltcg",     True,  "ltcg"),
    "qualified_199a":    ("199a",     True,  "ltcg"),
    "treasury":          ("ordinary", False, "ltcg"),
    "muni_out_of_state": ("exempt",   True,  "ltcg"),
    # DECLARED, UNUSED. The coordinate proving the federal and state axes are
    # independent rather than accidentally aligned. Costs one line.
    "muni_in_state":     ("exempt",   False, "ltcg"),
    "collectibles":      ("ordinary", True,  "collectibles"),
}

# §199A's 20% deduction on qualified REIT dividends. Its 2026 statutory status is NOT
# established in this repo — if it has lapsed, `qualified_199a` collapses into
# `ordinary` and becomes another declared-and-unused member. The vocabulary survives
# either way, which is part of the argument for having one.
SECTION_199A_DEDUCTION: float = 0.20

# Federal long-term rate on collectibles (physical-metal grantor trusts such as IAU).
# Lands on the REALIZATION term, never on income — gold throws off no income at all.
COLLECTIBLES_FEDERAL_RATE: float = 0.28

# Character per sleeve — the DEFAULT, complete by construction. There is no fallback:
# _tax_character RAISES on an unlisted sleeve rather than assuming `ordinary`, the
# same rule SLEEVE_ASSUMED_YIELD adopted in #210 (PR 3) and for the same reason — a
# silent default is indistinguishable from a decision. Maintenance is enforced by a
# raise, not by discipline, and a test pins these keys against the sleeve universe.
#
# BLENDS ARE A KNOWN SIMPLIFICATION. multi_asset and target_date look THROUGH to
# component sleeves for their yield, but declare a single character here. `ordinary`
# is what the model already applied to them, so declaring it changes no number — it
# makes the existing simplification visible instead of implicit. Character
# look-through for blends is filed, not folded in.
# USER-EDITABLE.
SLEEVE_TAX_CHARACTER: dict[str, str] = {
    # ── ordinary income ───────────────────────────────────────────────────────
    "cash":                    "ordinary",
    "core_fi_credit":          "ordinary",
    "high_yield_fi":           "ordinary",
    "floating_rate":           "ordinary",
    "multi_sector_fi":         "ordinary",
    "liquid_alt":              "ordinary",
    # Covered-call and ELN income is ordinary. The sleeve sits in EQUITY_SLEEVES for
    # EXPOSURE and is taxed as ordinary for CHARACTER — the two axes disagreeing here
    # is the reason #278 exists, and why a fix keyed on EQUITY_SLEEVES was wrong by 4x.
    "hedged_equity":           "ordinary",
    # Blends — see the simplification note above.
    "multi_asset":             "ordinary",
    "target_date":             "ordinary",
    # No income at all; declared so the GAIN path resolves. Its income character is
    # never consulted because NOT_MODELLED_SLEEVES refuses the sleeve first, which is
    # also why #284's dispute over PDBC's distributions does not bite here.
    "real_assets_commodities": "ordinary",
    "crypto":                  "ordinary",

    # ── qualified dividends ───────────────────────────────────────────────────
    "us_large_core":           "qualified",
    "us_large_growth":         "qualified",
    "us_large_quality":        "qualified",
    "us_large_value":          "qualified",
    "us_mid_cap":              "qualified",
    "us_small_core":           "qualified",
    "us_small_value":          "qualified",
    "us_sector_healthcare":    "qualified",
    "us_sector_tech":          "qualified",
    "single_stock":            "qualified",
    "thematic":                "qualified",
    "emerging_markets":        "qualified",
    "intl_all_exus":           "qualified",
    "intl_developed":          "qualified",
    "intl_quality":            "qualified",
    "intl_large_value":        "qualified",
    "intl_small_value":        "qualified",

    # ── everything else is its own character ──────────────────────────────────
    # REIT dividends are non-qualified ordinary but carry §199A's 20% deduction.
    "real_assets_reit":        "qualified_199a",
    # US Treasury obligations — interest exempt from PA tax, gains taxed normally.
    # THE CASE THAT FORCED THIS TABLE. pages/8_Research.py renders VGIT's rationale
    # naming this exemption as the reason the fund was chosen, while the register
    # charged it state tax anyway (#283).
    "core_fi_treasury":        "treasury",
    "tips":                    "treasury",
    # A national HY-muni fund is mostly out-of-state, so PA's 3.07% still applies:
    # the exemption is federal, not total.
    "high_yield_muni":         "muni_out_of_state",
    "real_assets_gold":        "collectibles",
}

# Per-SECURITY overrides — deliberately empty, and that is the finding rather than an
# omission. Character is sleeve-determined in 33 of 33 sleeves as the book stands.
# Kept because character is a property of a FUND (its holdings and structure) while a
# sleeve is a property of EXPOSURE, and the two can come apart: a fund can change
# structure without changing sleeve. Config rather than a `securities` column so the
# fact keeps its reasoning beside it and shows up in a diff. USER-EDITABLE.
SECURITY_TAX_CHARACTER: dict[str, str] = {}

# Sleeves a relocation must NEVER recommend, whatever the dollars. SEPARATED FROM
# CHARACTER ON PURPOSE — this was the second behaviour bundled into
# FEDERALLY_EXEMPT_SLEEVES, and the mirror needed the rate without it: a Treasury in
# taxable still has federal tax to save by relocating, so a state-exempt sleeve must
# NOT be suppressed.
#
# The reasons here are not properties of the income's character. A muni is wrong to
# shelter because of what the DESTINATION WRAPPER does to it — a pre-tax shelter
# converts exempt interest into ordinary income at withdrawal, strictly worse. An MLP
# would be wrong for an unrelated reason (UBTI inside an IRA). Deriving this from
# character would force the next entry to pretend to be one.
#
# A category, not a threshold: a dollar cutoff could resurrect a categorically wrong
# recommendation for a large holding. USER-EDITABLE.
RELOCATION_IS_CATEGORICALLY_WRONG: frozenset[str] = frozenset({"high_yield_muni"})

# The annual income-shelter benefit below which a "relocation" is a rounding error
# rather than a decision (e.g. a $0.43/yr row). A PRESENTATION THRESHOLD, never a
# modeling claim — a row below it is real drag and always counted in the aggregate
# drag KPI and in every group's total.
#
# NO CODE READS THIS TODAY; it documents a judgement, not a behaviour. The register
# used to carry a `surfaced` flag computed from it and the page filtered on that
# flag, which produced the "count 4, table empty" contradiction: a group header
# claiming four rows above a table showing none. That was fixed by rendering EVERY
# backing row with a Value column, so a small drag reads as small instead of as a
# missing row (see the expander comment in pages/14_Asset_Location.py, and the
# regression test in tests/test_asset_location.py). The flag then had no consumer
# and was removed. Kept as the stated threshold so the judgement survives its
# mechanism, and because any future surfacing rule should start from this number
# rather than reinventing one. USER-EDITABLE.
MIN_ANNUAL_BENEFIT: float = 1.00

# The single-filer 0% long-term-cap-gains ceiling — TAX LAW, so it belongs here
# alongside the other IRS figures. The 0% LTCG bracket is a finite BUDGET, not a
# rate: realized long-term gains are federally untaxed up to
# (ceiling − ordinary income), then taxed at 15% (TAX_PROFILE['federal_ltcg']).
LTCG_0_BRACKET_CEILING_SINGLE_2026: float = 48_350.0

# Ordinary income is NOT here. It is the owner's real salary, this file is tracked,
# and the repo is public — so it lives in private/personal_profile.json (gitignored)
# and is read at runtime via personal_profile.load_income_profile(). See that
# module for why an absent profile yields UNKNOWN rather than zero.


def ltcg_headroom(ordinary_income: float | None) -> float | None:
    """Remaining 0%-bracket budget: (ceiling − income), floored at zero.

    Returns None when income is unknown — the caller must decide how to present
    that. Explicitly NOT `max(0, ceiling - 0)`: treating unknown income as zero
    would report the entire ceiling as free headroom, which is the one wrong
    answer here with a real cost (realizing gains that are actually taxed).
    """
    if ordinary_income is None:
        return None
    return max(0.0, LTCG_0_BRACKET_CEILING_SINGLE_2026 - float(ordinary_income))

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
    "acct_01",           # self-directed taxable — the trade-ledger base account
    "acct_roth_01",      # Roth IRA
    "acct_trad_ira_01",  # Traditional IRA
})


def is_directable(pseudonym: str) -> bool:
    """True if the account can be traded jointly today (vs. needs coordination)."""
    return pseudonym in DIRECTABLE_PSEUDONYMS


# The specific rollable 401(k) that the rollover_401k action describes, identified
# by pseudonym so {workplace_plan_value} is a definition, not an argmax over
# balances. USER-EDITABLE.
ROLLOVER_SOURCE_PSEUDONYM: str = "acct_wkpl_02"  # MissionSquare former-employer 401(k) — holds RFUTX, fully vested, rollable now; Moody's PPP (acct_wkpl_01) is $0-vested forfeitable money, not worth rolling, deliberately excluded from this figure


# ── Sleeve deploy priority, keyed BY ACCOUNT TYPE (ORDINAL — not a forecast) ─────
# Where new cash should go depends on the wrapper, so the ranking is per account
# type. 1 = most deserving of that account's space. A sleeve ABSENT from an
# account type's map is NOT a deploy target for that account (distinct from
# "ranked last") — it simply never appears. This shape replaces the old flat dict
# and makes both the deploy alias and a global exclusion list unnecessary.
#
# Roth / HSA — rank by expected return (highest-growth deserves never-taxed space).
#
# INTERNATIONAL IS NOT UNIFORMLY ABSENT, AND THE FTC IS NOT THE WHOLE RULE. This
# comment read "International is absent: a Roth forfeits the foreign tax credit"
# from b6b8774 until #221. That was too coarse in both directions: it described a
# blanket exclusion the map below does not implement, and it implied that the one
# international sleeve which IS here somehow escapes the credit loss. It does not.
#
# EXCLUDED — intl_developed, intl_all_exus, and the three intl tilt sleeves. The
#   forgone FTC is only part of the reason. Pre-migration these render as off-SAA,
#   target-$0 rows, so a Roth buy is movement AWAY from the SAA on the page's own
#   terms; the phase-46 split gate counts carriers in acct_01 only, so a Roth trade
#   cannot open it; and Roth space is the scarcest shelter, with the $76,147
#   rollover inbound. Any one of those excludes them without the FTC.
#
# KEPT — emerging_markets, rank 2. A targeted SAA sleeve (9.18%, currently ~4.5pp
#   underweight), not off-SAA, so none of those three arguments reaches it. The FTC
#   cost is REAL AND ACCEPTED here, not avoided: EM withholding runs ~20-25%
#   against developed's ~15%, so the credit forfeited is LARGER than the ~45bp
#   _INTL_FTC_DRAG prices for the developed tilts (src/location_actions.py, built
#   as ~3% yield x ~15% withholding — a developed-market calibration that
#   understates EM). The judgement is that Roth-trapped, never-taxed growth on the
#   highest-premium equity sleeve outweighs a credit that cannot be claimed in a
#   Roth at all. A deliberate trade, not an exemption.
#
# DATED: the off-SAA half of the exclusion expires when the 9->12 split lands. The
#   tilts become targeted sleeves then (Intl Quality 6.25 / Large Value 3.75 /
#   Small Value 3.33), at which point they stop differing from EM on that axis and
#   the exclusion has to rest on scarcity alone — or be revisited. Filed.
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
    # The international tilt sleeves belong in taxable for the same reason broad
    # international does — a taxable holder can generally credit the foreign tax
    # withheld, which a shelter cannot. Deliberately NOT added to _PRETAX_PRIORITY:
    # deploying them into a shelter forfeits that credit, a cost the Asset Location
    # page states explicitly in the fund_intl_tilts action group rather than
    # silently allowing here.
    "intl_quality":     1,
    "intl_large_value": 1,
    "intl_small_value": 1,
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


def _require_rate(tax_profile: dict[str, float], key: str) -> float:
    """One component of a combined rate, or raise. Deliberately NO default.

    A missing key previously read as 0.0, silently DROPPING that component: an
    absent ``state_ltcg`` turned a 15%+3.07% LTCG rate into a bare 15%, understating
    every tax-drag and payback figure downstream with nothing visibly wrong. The
    zero was indistinguishable from a real 0% rate. An incomplete tax_profile is a
    configuration error to fix at the source, not a rate to quietly halve."""
    if key not in tax_profile:
        raise KeyError(
            f"tax_profile is missing {key!r}; a combined rate must not silently drop "
            f"a component to 0 (it would understate tax drag). Present: "
            f"{sorted(tax_profile)}."
        )
    return float(tax_profile[key])


def ltcg_rate(tax_profile: dict[str, float]) -> float:
    """Combined long-term capital-gains rate (federal + state). Raises on a missing
    component (see _require_rate) rather than understating the rate."""
    return _require_rate(tax_profile, "federal_ltcg") + _require_rate(tax_profile, "state_ltcg")


def ordinary_rate(tax_profile: dict[str, float]) -> float:
    """Combined ordinary marginal rate (federal + state). Raises on a missing
    component (see _require_rate) rather than understating the rate."""
    return _require_rate(tax_profile, "federal_marginal") + _require_rate(tax_profile, "state_marginal")
