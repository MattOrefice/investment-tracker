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
            "Core's job is to be the un-opinionated anchor: when factor tilts go through inevitable "
            "multi-year underperformance windows, Core ensures presence in the broad equity rally. "
            "Anchor exposure to the cap-weighted S&P 500 — the most efficient, best-governed, "
            "highest-quality earnings stream in global markets. The 16% weight is deliberately not the "
            "largest US sleeve — Quality at 14% comes close — because most US large exposure should "
            "express a factor view rather than passive cap-weight.\n\n"
            "**Would increase if** factor premia compress further or if conviction in active factor tilts erodes."
        ),
    },
    {
        "name": "US Large Quality",
        "parent_name": "Equity",
        "target_weight": 0.15 / _EXCASH_NORM,
        "tolerance_band": 0.03,
        "benchmark_ticker": "QUAL",
        "rationale": (
            "Largest factor tilt in the portfolio, deliberately so. Quality (high ROIC, low leverage, stable "
            "earnings) is the only factor that has strengthened post-academic-publication, because it's not "
            "statistical arbitrage — it's a structural preference for better businesses that doesn't get "
            "arbitraged away. Empirically, quality has delivered equity-like returns with materially lower "
            "drawdowns, which matters in a 30+ year compounding window where avoiding deep drawdowns dominates "
            "terminal wealth. 14% reflects high conviction without being so concentrated that a factor regime "
            "change would severely damage the portfolio.\n\n"
            "**Would reduce if** quality screens become dominated by a single sector to the point of losing diversification."
        ),
    },
    {
        "name": "US Large Value",
        "parent_name": "Equity",
        "target_weight": 0.09 / _EXCASH_NORM,
        "tolerance_band": 0.02,
        "benchmark_ticker": "IWD",
        "rationale": (
            "Smaller, contextual factor bet on growth-vs-value mean reversion. The Russell 1000 Value vs. "
            "Growth spread is at the deepest valuation gap since 2000. Mean-reversion case is real; the "
            "historical base rate over 10-year windows favors value at these spreads. But value has had "
            "multiple \"this time it'll work\" moments since 2010 that didn't deliver, so the position is "
            "sized to express the view without betting the portfolio on it. 8% out of 38% total US Large "
            "equals 21% of US large-cap exposure — a tilt, not a thesis.\n\n"
            "**Would increase if** the spread widens further or if real rates normalize.\n"
            "**Would reduce if** growth's earnings advantage compounds another 5+ years."
        ),
    },
    {
        "name": "US Small Cap",
        "parent_name": "Equity",
        "target_weight": 0.08 / _EXCASH_NORM,
        "tolerance_band": 0.02,
        "benchmark_ticker": "IWM",
        "rationale": (
            "Size factor exposure, sized modestly because the evidence is the weakest. Small-cap historically "
            "delivered a ~1-2% premium over large, but the premium has been weak post-publication and arguably "
            "absent for the last 15 years. Small-caps offer genuine diversification: more domestic-economy-"
            "leveraged, more interest-rate-sensitive, and less correlated with mega-cap tech concentration. "
            "7% is enough to matter if the size premium reasserts (especially with valuation discounts vs. "
            "large-cap at multi-decade lows) without anchoring the portfolio to a factor with shaky empirical "
            "support.\n\n"
            "**Would increase if** the rolling 5-year small-cap-vs-large-cap return spread turns positive on a "
            "sustained basis — historically the clearest signal of size-premium reassertion.\n"
            "**Would reduce if** the valuation discount vs. large-cap closes to historical mean or if "
            "small-cap credit quality deteriorates (rising default rates signaling the quality screen "
            "is insufficient protection)."
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
            "it is the region absent a view. Every tilt in this book is a deviation from a market "
            "portfolio, and a deviation is only meaningful if the thing deviated from is also owned.\n\n"
            "Core is 34.7% of international equity here — the same share it holds in the US book, 17 "
            "of 49. That proportion is not a separate decision. The international sleeves apply the US "
            "structure to a 20% region, so the weights follow from choices already made. If the US "
            "core weight changes, this one changes with it.\n\n"
            "VEA at 3 bps is the cheapest instrument for the exposure. IEFA is held as a substitute in "
            "the same sleeve, tracking the same developed universe."
        ),
    },
    {
        "name": "International Quality",
        "parent_name": "Equity",
        "target_weight": 0.20 * 15 / 49 / _EXCASH_NORM,
        "tolerance_band": 0.02,
        "benchmark_ticker": "IQLT",
        "rationale": (
            "Quality is the largest tilt in the US book at 15 of 49, and it is the largest tilt here "
            "for the same reason.\n\n"
            "Nothing in the case for holding SPHQ is US-specific. The screen sorts on return on equity, "
            "accruals, and leverage; those relationships are documented in international data on the "
            "same terms. Expressing the view only at home would be a claim that profitability pays in "
            "Chicago and not in Osaka, which is not the view — it is home bias with a fee schedule "
            "attached.\n\n"
            "IDHQ tracks S&P's quality screen of developed ex-US large and mid caps: same issuer as "
            "SPHQ, same index provider, same three fundamental measures. It costs 26 bps over VEA, less "
            "than the 33 paid for international small value — the same ordering that holds in the US, "
            "where quality costs 12 bps over VOO and small value costs 22. The benchmark is IQLT, "
            "iShares' MSCI quality index abroad, which reproduces the SPHQ-to-QUAL relationship exactly. "
            "Selection effect therefore measures what it measures at home: the gap between two quality "
            "methodologies, not the premium itself."
        ),
    },
    {
        "name": "International Large Value",
        "parent_name": "Equity",
        "target_weight": 0.20 * 9 / 49 / _EXCASH_NORM,
        "tolerance_band": 0.02,
        "benchmark_ticker": "EFV",
        "rationale": (
            "Value abroad, on the same terms VTV expresses it at home.\n\n"
            "One difference is worth naming rather than hiding. AVIV integrates profitability into its "
            "value screen; VTV tracks a plain cap-weighted value index and does not. So this sleeve "
            "holds a more deliberate instrument than its US counterpart. If that asymmetry matters, the "
            "resolution is AVLV in the US rather than EFV abroad — the US sleeve is the one that is "
            "less considered, not this one.\n\n"
            "The benchmark is EFV, MSCI's EAFE value index. Holding and benchmark come from different "
            "index families, exactly as VTV and IWD do, so selection measures implementation rather "
            "than the premium."
        ),
    },
    {
        "name": "International Small Value",
        "parent_name": "Equity",
        "target_weight": 0.20 * 8 / 49 / _EXCASH_NORM,
        "tolerance_band": 0.02,
        "benchmark_ticker": "SCZ",
        "rationale": (
            "The small-value interaction, held abroad for the reason AVUV is held at home.\n\n"
            "This is the smallest sleeve in the book. It is small because international is 20% of the "
            "portfolio and small value is 8 of 49 in the US structure — arithmetic, not diminished "
            "conviction. Sizing it larger than the mirror produces would be a claim that the premium is "
            "stronger abroad than at home, and that claim is not being made.\n\n"
            "The benchmark is SCZ, iShares' EAFE small-cap index — small blend, not small value, "
            "because no passive international small-value index fund exists. This is the same compromise "
            "the US sleeve already makes against IWM, with the same consequence: selection effect here "
            "carries the value premium itself rather than measuring implementation. The factor exhibit "
            "is where that premium is priced. Attribution will show it as selection, and it should not "
            "be read as skill."
        ),
    },
    {
        "name": "Emerging Markets",
        "parent_name": "Equity",
        "target_weight": 0.09 / _EXCASH_NORM,
        "tolerance_band": 0.02,
        "benchmark_ticker": "EEM",
        "rationale": (
            "Higher-growth, higher-volatility diversifier. EM equities offer demographic tailwinds, structurally "
            "cheaper valuations, and exposure to growth profiles that don't exist in developed markets. The 8% "
            "weight reflects respect for the asymmetric risk — EM has had 50%+ drawdowns multiple times and "
            "includes meaningful country-specific governance risk (China especially). Modestly long EM at "
            "attractive valuations is preferable to chasing it after a rally.\n\n"
            "**Would increase if** EM ex-China valuations become exceptionally cheap.\n"
            "**Would reduce if** China governance risk materially worsens or if EM index construction concentrates further into a single country.\n\n"
            "Emerging markets is the one equity region held at cap weight. The developed book tilts "
            "toward quality, value, and small value because those convictions are not US-specific; the "
            "same logic would extend here, and it is not extended, for two reasons.\n\n"
            "Verifiability first. Every other tilt in this book loads on factors — size, value, "
            "profitability — that Ken French's developed ex-US series spans back to 1990, so their "
            "exposures can be measured against a real benchmark. No equivalent daily series exists "
            "for emerging markets, and the monthly history is too short to regress against this "
            "portfolio's inception. A tilt here could be asserted but not shown — and an unpriceable "
            "factor position is indistinguishable, on this page, from a hunch.\n\n"
            "Materiality second. Mirroring the developed structure into a 9.18% region yields sleeves "
            "of roughly 3.2%, 2.8%, 1.7%, and 1.5% — enough to add four rows to every exhibit, not "
            "enough to move a return.\n\n"
            "This is the position most likely to change. If a daily emerging-markets factor series "
            "becomes available, or the region's weight grows enough to make sub-sleeves material, the "
            "case for cap weight weakens."
        ),
    },
    {
        "name": "Core Fixed Income",
        "parent_name": "Income",
        "target_weight": 0.06 / _EXCASH_NORM,
        "tolerance_band": 0.02,
        "benchmark_ticker": "IEF",
        "rationale": (
            "Duration as recession ballast, sized for an aggressive growth portfolio. Classical 60/40 doctrine "
            "assumed Treasuries reliably hedged equity drawdowns; 2022 disproved that under inflationary regimes. "
            "But in deflationary or recessionary drawdowns — which remain the more common equity tail risk — "
            "intermediate Treasuries still work. 6% in a 78% growth portfolio is intentionally thin: not relying "
            "on FI for return, relying on it for drawdown buffering and rebalancing-into-equity-weakness "
            "optionality.\n\n"
            "**Would increase if** real yields exceed 3% (making FI competitive on a return basis) or if horizon shortens.\n"
            "**Would reduce if** inflation regime persists and nominal duration stops hedging anything."
        ),
    },
    {
        "name": "TIPS",
        "parent_name": "Income",
        "target_weight": 0.04 / _EXCASH_NORM,
        "tolerance_band": 0.02,
        "benchmark_ticker": "TIP",
        "rationale": (
            "Inflation-hedged real-yield exposure, sized for a long-horizon investor's actual risk. The biggest "
            "FI risk over a multi-decade horizon isn't a market crash — it's having returns silently destroyed "
            "by an inflationary decade. TIPS "
            "hedge that risk directly via CPI linkage. 4% is 40% of the FI sleeve, heavier than typical "
            "institutional allocations (usually 20-30%), reflecting that the horizon is long enough that "
            "real-return preservation dominates nominal. Post-2022 also reinforced that nominal Treasuries don't "
            "always hedge stocks the way 60/40 doctrine claimed — TIPS at least hedge inflation reliably.\n\n"
            "**Would increase if** real yields rise above 2.5%.\n"
            "**Would reduce if** horizon shortens or if confidence in disinflation persisting grows."
        ),
    },
    {
        "name": "Real Assets",
        "parent_name": "Real Assets",
        "target_weight": 0.10 / _EXCASH_NORM,
        "tolerance_band": 0.02,
        "benchmark_ticker": "VNQ (60%) + DBC (40%)",
        "rationale": (
            "Inflation-correlated diversifier with different risk drivers than equity or duration. Public REITs "
            "and commodities aren't perfect substitutes for the private real estate and natural resource exposure "
            "endowments hold, but at retail account sizes they're the only honest implementation. 10% is large enough to actually "
            "move the portfolio's behavior in inflationary regimes (which 2-3% wouldn't) without being so large "
            "that REIT/commodity volatility — both can have 30%+ drawdowns — overwhelms the equity sleeves.\n\n"
            "**Would increase if** access to private real estate opens or if commodities enter sustained backwardation.\n"
            "**Would reduce if** deflationary regime persists and these assets stop earning their diversification benefit."
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
            "occasional opportunistic deployment. SPAXX yields ~4-5% currently, so the drag is muted.\n\n"
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

        if existing > 0:
            print("Asset classes already seeded, skipping.")
            return

        # Insert parents first so we can look up their IDs for sub-classes
        for p in PARENTS:
            conn.execute(
                """
                INSERT OR IGNORE INTO asset_classes
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
                INSERT OR IGNORE INTO asset_classes
                    (name, parent_id, target_weight, tolerance_band, sort_order, rationale, benchmark_ticker)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (sc["name"], parent_id, sc["target_weight"], sc["tolerance_band"],
                 sort_order, sc["rationale"], sc["benchmark_ticker"]),
            )

        print(f"Seeded {len(PARENTS)} parent categories and {len(SUB_CLASSES)} sub-classes.")


if __name__ == "__main__":
    seed()
