"""Seed the asset_classes table with the locked Phase 1 SAA taxonomy."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import get_connection, initialize_db

# Phase 38a — cash is operational float, not a strategic allocation. The 9
# non-cash sleeves (and their 3 parents) are rescaled to sum to 1.0 (each prior
# target ÷ 0.98, the prior non-cash total). The Cash parent and Cash / SPAXX
# sub-class rows are RETAINED with target_weight = 0 (untargeted) so the SPAXX
# security mapping and operational-cash plumbing keep working; everything
# "strategic" filters on target_weight > 0.
_EXCASH_NORM = 0.98  # prior non-cash target total

PARENTS = [
    {
        "name": "Equity",
        "target_weight": 0.78 / _EXCASH_NORM,
        "tolerance_band": 0.03,
        "rationale": "Core equity engine of the portfolio; primary driver of long-run real returns across US, international, and emerging market sleeves.",
        "benchmark_ticker": None,
    },
    {
        "name": "Income",
        "target_weight": 0.10 / _EXCASH_NORM,
        "tolerance_band": 0.02,
        "rationale": "Duration and inflation protection; ballast against equity drawdowns and silent real-return destruction.",
        "benchmark_ticker": None,
    },
    {
        "name": "Real Assets",
        "target_weight": 0.10 / _EXCASH_NORM,
        "tolerance_band": 0.02,
        "rationale": "Inflation-correlated diversifier with different risk drivers than equity or duration.",
        "benchmark_ticker": None,
    },
    {
        "name": "Cash",
        "target_weight": 0.0,
        "tolerance_band": 0.02,
        "rationale": "Operational liquidity for rebalancing friction and opportunistic deployment; not a strategic allocation — held as residual SPAXX float, measured outside the ex-cash SAA.",
        "benchmark_ticker": None,
    },
]

SORT_ORDERS = {
    "US Large Core":           10,
    "US Large Quality":        20,
    "US Large Value":          30,
    "US Small Cap":            40,
    "International Core":          50,
    "International Quality":       52,
    "International Large Value":   54,
    "International Small Value":   56,
    "Emerging Markets":        60,
    "Core Fixed Income":       70,
    "TIPS":                    80,
    "Real Assets":             90,
    "Cash / SPAXX":           100,
}

SUB_CLASSES = [
    {
        "name": "US Large Core",
        "parent_name": "Equity",
        "target_weight": 0.17 / _EXCASH_NORM,
        "tolerance_band": 0.03,
        "benchmark_ticker": "SPY",
        "rationale": (
            "Core is the un-opinionated anchor: when the factor tilts go through their inevitable "
            "multi-year stretches of underperformance, Core keeps the portfolio in the broad equity "
            "rally. It holds the cap-weighted S&P 500, the most efficient, best-governed, and "
            "highest-quality earnings stream in global markets. The {{w:US Large Core}} weight is deliberately "
            "not the largest US sleeve (Quality at {{w:US Large Quality}} comes close), because most US large-cap exposure "
            "should express a factor view rather than passive cap weight.\n"
            "\n"
            "**Would increase if** factor premia compress further or if conviction in the active "
            "factor tilts erodes."
        ),
    },
    {
        "name": "US Large Quality",
        "parent_name": "Equity",
        "target_weight": 0.15 / _EXCASH_NORM,
        "tolerance_band": 0.03,
        "benchmark_ticker": "QUAL",
        "rationale": (
            "The largest factor tilt in the portfolio, deliberately. Quality (high ROIC, low "
            "leverage, stable earnings) is the only factor that has strengthened since academic "
            "publication. It is a structural preference for better businesses, not statistical "
            "arbitrage, so it is not arbitraged away. Empirically, quality has delivered equity-like "
            "returns with materially lower drawdowns. That matters over a 30+ year compounding "
            "window, where avoiding deep drawdowns dominates terminal wealth. {{w:US Large Quality}} expresses high "
            "conviction without enough concentration for a factor regime change to severely damage "
            "the portfolio.\n"
            "\n"
            "**Would reduce if** quality screens become dominated by a single sector to the point of "
            "losing diversification."
        ),
    },
    {
        "name": "US Large Value",
        "parent_name": "Equity",
        "target_weight": 0.09 / _EXCASH_NORM,
        "tolerance_band": 0.02,
        "benchmark_ticker": "IWD",
        "rationale": (
            "A smaller, contextual bet on growth-versus-value mean reversion. The Russell 1000 Value "
            "versus Growth spread is at its deepest valuation gap since 2000. The mean-reversion case"
            " is real: over 10-year windows, the historical base rate favors value at these spreads. "
            "But value has had several \"this time it'll work\" moments since 2010 that did not "
            "deliver, so the position is sized to express the view without betting the portfolio on "
            "it. {{w:US Large Value}} out of the {{sum:US Large Core+US Large Quality+US Large Value}} total in US large caps is "
            "{{share:US Large Value|US Large Core+US Large Quality+US Large Value}} of US large-cap exposure.\n"
            "\n"
            "**Would increase if** the spread widens further or if real rates normalize.\n"
            "**Would reduce if** growth's earnings advantage compounds for another 5+ years."
        ),
    },
    {
        "name": "US Small Cap",
        "parent_name": "Equity",
        "target_weight": 0.08 / _EXCASH_NORM,
        "tolerance_band": 0.02,
        "benchmark_ticker": "IWM",
        "rationale": (
            "Size factor exposure, sized modestly because the evidence is the weakest. Small caps "
            "have historically earned a ~1-2% premium over large caps, but the premium has been weak "
            "since publication and arguably absent for the last 15 years. Small caps offer genuine diversification: "
            "they are more domestic-economy-leveraged, more interest-rate-sensitive, and less "
            "correlated with mega-cap tech concentration. {{w:US Small Cap}} is enough to matter if the size premium "
            "reasserts, especially with valuation discounts to large caps at multi-decade lows, "
            "without anchoring the portfolio to a factor with shaky empirical support.\n"
            "\n"
            "**Would increase if** the rolling 5-year small-cap-vs-large-cap return spread turns "
            "positive on a sustained basis, historically the clearest signal of size-premium "
            "reassertion.\n"
            "**Would reduce if** the valuation discount to large caps closes to its historical mean, "
            "or if small-cap credit quality deteriorates (rising default rates would show the quality"
            " screen is insufficient protection)."
        ),
    },
    # Phase 39 — the single cap-weighted "International Developed" sleeve is split into
    # four, mirroring the US structure (17/15/9/8 of 49) across the same 20% region. The
    # split is weight-neutral: the four targets sum to 0.20 / _EXCASH_NORM exactly.
    # Bands are set EXPLICITLY to 0.02 — every sleeve here is under 10%, and the column
    # default is also 0.02, so an omitted band would pass by luck rather than by intent.
    {
        "name": "International Core",
        "parent_name": "Equity",
        "target_weight": 0.20 * 17 / 49 / _EXCASH_NORM,
        "tolerance_band": 0.02,
        "benchmark_ticker": "EFA",
        "rationale": (
            "The cap-weighted developed ex-US market, held for the reason any core position is held: "
            "it is the region without a view. Every tilt in this book is a deviation from a market "
            "portfolio, and a deviation is only meaningful if the portfolio it deviates from is "
            "also owned.\n"
            "\n"
            "Core is {{share:International Core|International Core+International Quality+International Large Value+International Small Value}} of international equity here, the same share it holds in the US book (17 "
            "of 49). That proportion is not a separate decision: the international sleeves apply the "
            "US structure to a {{sum:International Core+International Quality+International Large Value+International Small Value}} region, so the weights follow from choices already made. If the US"
            " core weight changes, this one changes with it.\n"
            "\n"
            "VEA, at 3 bps, is the cheapest instrument for the exposure. IEFA is held as a substitute"
            " in the same sleeve, tracking the same developed universe.\n"
            "\n"
            "**Would revisit if** the US core weight changes, since this sleeve is defined as the US "
            "core's international mirror rather than sized on its own."
        ),
    },
    {
        "name": "International Quality",
        "parent_name": "Equity",
        "target_weight": 0.20 * 15 / 49 / _EXCASH_NORM,
        "tolerance_band": 0.02,
        "benchmark_ticker": "IQLT",
        "rationale": (
            "Quality is the largest tilt in the US book, at 15 of 49, and it is the largest tilt here"
            " for the same reason.\n"
            "\n"
            "Nothing in the case for holding SPHQ is US-specific. The screen sorts on return on "
            "equity, accruals, and leverage, and those relationships are documented in international "
            "data on the same terms. Holding the tilt only in the US would imply that the "
            "profitability premium exists at home but not abroad, which is home bias.\n"
            "\n"
            "IDHQ tracks S&P's quality screen of developed ex-US large and mid caps: the same issuer "
            "as SPHQ, the same index provider, and the same three fundamental measures. It costs 26 "
            "bps over VEA, less than the 33 paid for international small value. The US has the same "
            "ordering, where quality costs 12 bps over VOO and small value costs 22. The benchmark is"
            " IQLT, iShares' MSCI quality index abroad, which reproduces the SPHQ-to-QUAL "
            "relationship exactly. Selection effect therefore measures what it measures at home: the "
            "gap between two quality methodologies, not the premium itself.\n"
            "\n"
            "**Would revisit if** the international quality premium diverged materially from the"
            " domestic one over a full cycle. That would mean the factor is not the region-"
            "independent phenomenon this position assumes."
        ),
    },
    {
        "name": "International Large Value",
        "parent_name": "Equity",
        "target_weight": 0.20 * 9 / 49 / _EXCASH_NORM,
        "tolerance_band": 0.02,
        "benchmark_ticker": "EFV",
        "rationale": (
            "Value abroad, on the same terms VTV expresses it at home.\n"
            "\n"
            "AVIV integrates profitability into its value screen; VTV tracks a plain cap-weighted "
            "value index and does not. This sleeve therefore holds a more deliberate instrument than "
            "its US counterpart. If the asymmetry matters, the resolution is AVLV in the US rather "
            "than EFV abroad: the US sleeve is the less considered one.\n"
            "\n"
            "The benchmark is EFV, MSCI's EAFE value index. Holding and benchmark come from different"
            " index families, as VTV and IWD do, so selection measures implementation rather than the"
            " premium.\n"
            "\n"
            "**Would revisit if** AVIV's profitability integration proved to be doing the work rather"
            " than the value screen. The US sleeve would then move to AVLV, rather than this sleeve "
            "moving to EFV."
        ),
    },
    {
        "name": "International Small Value",
        "parent_name": "Equity",
        "target_weight": 0.20 * 8 / 49 / _EXCASH_NORM,
        "tolerance_band": 0.02,
        "benchmark_ticker": "SCZ",
        "rationale": (
            "The small-value interaction, held abroad for the reason AVUV is held at home.\n"
            "\n"
            "This is the smallest sleeve in the book. It is small because international is {{sum:International Core+International Quality+International Large Value+International Small Value}} of the"
            " portfolio and small value is 8 of 49 in the US structure: the size is arithmetic, not "
            "diminished conviction. Sizing it above what the mirror produces would claim a stronger "
            "premium abroad than at home, and no such claim is made.\n"
            "\n"
            "The benchmark is SCZ, iShares' EAFE small-cap index. It is small blend rather than small"
            " value, because no passive international small-value index fund exists. The US sleeve "
            "makes the same compromise against IWM, with the same consequence: selection effect here "
            "carries the value premium itself rather than measuring implementation. The factor "
            "exhibit is where that premium is priced. Attribution will show that premium as "
            "selection. It comes from the factor tilt, not manager skill.\n"
            "\n"
            "**Would revisit if** the small-value interaction proved materially weaker in developed ex-US "
            "than domestically. Sized at the US mirror, it assumes parity."
        ),
    },
    {
        "name": "Emerging Markets",
        "parent_name": "Equity",
        "target_weight": 0.09 / _EXCASH_NORM,
        "tolerance_band": 0.02,
        "benchmark_ticker": "EEM",
        "rationale": (
            "A higher-growth, higher-volatility diversifier. EM equities offer demographic tailwinds,"
            " structurally cheaper valuations, and growth profiles that developed markets do not "
            "have. The {{w:Emerging Markets}} weight respects the asymmetric risk: EM has had 50%+ drawdowns several "
            "times and includes meaningful country-specific governance risk, China especially. A "
            "modest position at attractive valuations is preferred to buying after a rally.\n"
            "\n"
            "**Would increase if** EM ex-China valuations become exceptionally cheap.\n"
            "**Would reduce if** China governance risk materially worsens or if EM index "
            "construction concentrates further into a single country.\n"
            "\n"
            "Emerging markets is the one equity region held at cap weight. The developed book tilts "
            "toward quality, value, and small value because those convictions are not US-specific. "
            "The same logic would extend here, and it is not extended, for two reasons.\n"
            "\n"
            "The first is verifiability. Every other tilt in this book loads on factors (size, value,"
            " profitability) that Ken French's developed ex-US series spans back to 1990, so their "
            "exposures can be measured against a real benchmark. No equivalent daily series exists "
            "for emerging markets, and the monthly history is too short to regress against this "
            "portfolio's inception. A tilt here could be asserted but not shown: with no factor "
            "series to price it, the page could not measure its exposure.\n"
            "\n"
            "The second is materiality. Mirroring the developed structure into a {{w:Emerging Markets}} region "
            "yields sleeves of roughly "
            "{{scaled:Emerging Markets|International Core|International Core+International Quality+International Large Value+International Small Value}}, "
            "{{scaled:Emerging Markets|International Quality|International Core+International Quality+International Large Value+International Small Value}}, "
            "{{scaled:Emerging Markets|International Large Value|International Core+International Quality+International Large Value+International Small Value}}, and "
            "{{scaled:Emerging Markets|International Small Value|International Core+International Quality+International Large Value+International Small Value}}. Together they would add four rows to "
            "every exhibit, and none would be large enough to move the portfolio's return.\n"
            "\n"
            "This is the position most likely to change. If a daily emerging-markets factor series "
            "becomes available, or the region's weight grows enough to make sub-sleeves material, the"
            " case for cap weight weakens."
        ),
    },
    {
        "name": "Core Fixed Income",
        "parent_name": "Income",
        "target_weight": 0.06 / _EXCASH_NORM,
        "tolerance_band": 0.02,
        "benchmark_ticker": "IEF",
        "rationale": (
            "Duration as recession ballast, sized for an aggressive growth portfolio. Classical 60/40"
            " doctrine assumed Treasuries reliably hedged equity drawdowns; 2022 disproved that under"
            " an inflationary regime. In deflationary or recessionary drawdowns, still the more "
            "common equity tail risk, intermediate Treasuries work. {{w:Core Fixed Income}} in a {{w:Equity}} growth "
            "portfolio is "
            "intentionally thin: fixed income is held for drawdown buffering and the option to "
            "rebalance into equity weakness rather than for return.\n"
            "\n"
            "**Would increase if** real yields exceed 3% (making FI competitive on a return basis) or"
            " if the horizon shortens.\n"
            "**Would reduce if** the inflation regime persists and nominal duration stops hedging "
            "anything."
        ),
    },
    {
        "name": "TIPS",
        "parent_name": "Income",
        "target_weight": 0.04 / _EXCASH_NORM,
        "tolerance_band": 0.02,
        "benchmark_ticker": "TIP",
        "rationale": (
            "Inflation-hedged real-yield exposure, sized for a long-horizon investor's actual risk. "
            "Over a multi-decade horizon, the biggest fixed-income risk is not a market crash but an "
            "inflationary decade that silently destroys returns. TIPS hedge that risk directly "
            "through their CPI linkage. {{w:TIPS}} is {{share:TIPS|Income}} of the fixed-income sleeve, heavier than typical "
            "institutional allocations (usually 20-30%), because the horizon is long enough for real-"
            "return preservation to dominate nominal. Post-2022 experience also showed that nominal "
            "Treasuries do not always hedge stocks the way 60/40 doctrine claimed; TIPS at least "
            "hedge inflation reliably.\n"
            "\n"
            "**Would increase if** real yields rise above 2.5%.\n"
            "**Would reduce if** the horizon shortens or confidence grows that disinflation will "
            "persist."
        ),
    },
    {
        "name": "Real Assets",
        "parent_name": "Real Assets",
        "target_weight": 0.10 / _EXCASH_NORM,
        "tolerance_band": 0.02,
        "benchmark_ticker": "VNQ (60%) + DBC (40%)",
        "rationale": (
            "An inflation-correlated diversifier, with risk drivers unlike equity's or duration's. "
            "Public REITs and commodities are not perfect substitutes for the private real estate and"
            " natural resource exposure endowments hold, but at retail account sizes they are the "
            "only honest implementation. {{w:Real Assets}} is large enough to move the portfolio's behavior in "
            "inflationary regimes, which 2-3% would not, without being so large that REIT and "
            "commodity volatility (both can have 30%+ drawdowns) overwhelms the equity sleeves.\n"
            "\n"
            "**Would increase if** access to private real estate opens or if commodities enter "
            "sustained backwardation.\n"
            "**Would reduce if** a deflationary regime persists and these assets stop earning their "
            "diversification benefit."
        ),
    },
    {
        "name": "Cash / SPAXX",
        "parent_name": "Cash",
        "target_weight": 0.0,
        "tolerance_band": 0.02,
        "benchmark_ticker": "BIL",
        "rationale": (
            "Operational liquidity, not strategic dry powder. At 27 with a 30+ year horizon, holding meaningful "
            "cash is performance drag — 1% of cash held over 30 years costs roughly $2.4k of terminal wealth "
            "per $10k of base capital at 7% real equity returns. 2% handles rebalancing friction (funding "
            "tax-inefficient sleeves without forced sales), small drawdowns without selling at the bottom, and "
            "occasional opportunistic deployment. SPAXX earns roughly the Treasury bill rate less its "
            "fee, so the drag is muted; the Macro page shows the live rate.\n\n"
            "**Would increase** closer to retirement or with shorter-duration liabilities.\n"
            "**Would reduce** toward 1-2% if cash yields collapse below 2%."
        ),
    },
]


def seed():
    initialize_db()
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT COUNT(*) FROM asset_classes"
        ).fetchone()[0]

        # THE SOLE GUARD AGAINST DUPLICATION (#347). This runs from bootstrap on every
        # personal-mode start, and asset_classes has no unique constraint beyond its
        # primary key, which these inserts do not supply. So nothing in the SCHEMA
        # stops a second run from inserting every parent and sleeve again. `INSERT OR
        # IGNORE` would not help: with no constraint to violate, it ignores nothing. It
        # was used here once, read like deduplication, and provided none. Remove this
        # check and the next start silently doubles the taxonomy.
        # tests/test_seed_saa_idempotent.py pins it.
        if existing > 0:
            print("Asset classes already seeded, skipping.")
            return

        # Insert parents first so we can look up their IDs for sub-classes
        for p in PARENTS:
            conn.execute(
                """
                INSERT INTO asset_classes
                    (name, target_weight, tolerance_band, rationale, benchmark_ticker)
                VALUES (?, ?, ?, ?, ?)
                """,
                (p["name"], p["target_weight"], p["tolerance_band"],
                 p["rationale"], p["benchmark_ticker"]),
            )

        # Build name → id map for parent lookup
        rows = conn.execute(
            "SELECT asset_class_id, name FROM asset_classes WHERE parent_id IS NULL"
        ).fetchall()
        parent_id_map = {r["name"]: r["asset_class_id"] for r in rows}

        for sc in SUB_CLASSES:
            parent_id  = parent_id_map[sc["parent_name"]]
            sort_order = SORT_ORDERS.get(sc["name"])
            conn.execute(
                """
                INSERT INTO asset_classes
                    (name, parent_id, target_weight, tolerance_band, sort_order, rationale, benchmark_ticker)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (sc["name"], parent_id, sc["target_weight"], sc["tolerance_band"],
                 sort_order, sc["rationale"], sc["benchmark_ticker"]),
            )

        print(f"Seeded {len(PARENTS)} parent categories and {len(SUB_CLASSES)} sub-classes.")


if __name__ == "__main__":
    seed()
