"""Seed the asset_classes table with the locked Phase 1 SAA taxonomy."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import get_connection, initialize_db

PARENTS = [
    {
        "name": "Equity",
        "target_weight": 0.72,
        "tolerance_band": 0.03,
        "rationale": "Core equity engine of the portfolio; primary driver of long-run real returns across US, international, and emerging market sleeves.",
        "benchmark_ticker": None,
    },
    {
        "name": "Income",
        "target_weight": 0.15,
        "tolerance_band": 0.02,
        "rationale": "Duration and inflation protection; ballast against equity drawdowns and silent real-return destruction.",
        "benchmark_ticker": None,
    },
    {
        "name": "Real Assets",
        "target_weight": 0.10,
        "tolerance_band": 0.02,
        "rationale": "Inflation-correlated diversifier with different risk drivers than equity or duration.",
        "benchmark_ticker": None,
    },
    {
        "name": "Cash",
        "target_weight": 0.03,
        "tolerance_band": 0.02,
        "rationale": "Operational liquidity for rebalancing friction and opportunistic deployment; not strategic dry powder.",
        "benchmark_ticker": None,
    },
]

SORT_ORDERS = {
    "US Large Core":           10,
    "US Large Quality":        20,
    "US Large Value":          30,
    "US Small Cap":            40,
    "International Developed": 50,
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
        "target_weight": 0.16,
        "tolerance_band": 0.03,
        "benchmark_ticker": "SPY",
        "rationale": (
            "Anchor exposure to US large-cap equity. Cap-weighted S&P 500 representing the most efficient, "
            "best-governed, highest-quality earnings stream in global markets. The 16% weight is deliberately "
            "not the largest US sleeve — Quality at 14% comes close — because most US large exposure should "
            "express a factor view rather than passive cap-weight. Core's job is to be the un-opinionated "
            "anchor: when factor tilts go through inevitable multi-year underperformance windows, Core ensures "
            "presence in the broad equity rally.\n\n"
            "**Would increase if** factor premia compress further or if conviction in active factor tilts erodes."
        ),
    },
    {
        "name": "US Large Quality",
        "parent_name": "Equity",
        "target_weight": 0.14,
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
        "target_weight": 0.08,
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
        "target_weight": 0.07,
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
            "**Would increase if** real rates normalize and small-cap quality screens produce attractive opportunities."
        ),
    },
    {
        "name": "International Developed",
        "parent_name": "Equity",
        "target_weight": 0.19,
        "tolerance_band": 0.03,
        "benchmark_ticker": "EFA",
        "rationale": (
            "Largest non-US sleeve, expressing two distinct views. First, valuation: developed international "
            "CAPE is materially below US levels (historically 30-50% discount), and starting valuation is the dominant driver of long-run returns "
            "over 10+ year windows. Second, regime diversification: the dollar has been a 15-year tailwind for "
            "US-domiciled investors, and historical mean reversion suggests that tailwind isn't permanent. 19% "
            "is meaningfully overweight typical US-investor home bias (10-15%) but underweight global market "
            "cap (~40%); it expresses a real view without being a contrarian moonshot.\n\n"
            "**Would increase if** the valuation gap widens or the dollar weakens materially.\n"
            "**Would reduce if** European or Japanese structural reform stalls further."
        ),
    },
    {
        "name": "Emerging Markets",
        "parent_name": "Equity",
        "target_weight": 0.08,
        "tolerance_band": 0.02,
        "benchmark_ticker": "EEM",
        "rationale": (
            "Higher-growth, higher-volatility diversifier. EM equities offer demographic tailwinds, structurally "
            "cheaper valuations, and exposure to growth profiles that don't exist in developed markets. The 8% "
            "weight reflects respect for the asymmetric risk — EM has had 50%+ drawdowns multiple times and "
            "includes meaningful country-specific governance risk (China especially). Modestly long EM at "
            "attractive valuations is preferable to chasing it after a rally.\n\n"
            "**Would increase if** EM ex-China valuations become exceptionally cheap.\n"
            "**Would reduce if** China governance risk materially worsens or if EM index construction concentrates further into a single country."
        ),
    },
    {
        "name": "Core Fixed Income",
        "parent_name": "Income",
        "target_weight": 0.09,
        "tolerance_band": 0.02,
        "benchmark_ticker": "IEF",
        "rationale": (
            "Duration as recession ballast, sized for an aggressive growth portfolio. Classical 60/40 doctrine "
            "assumed Treasuries reliably hedged equity drawdowns; 2022 disproved that under inflationary regimes. "
            "But in deflationary or recessionary drawdowns — which remain the more common equity tail risk — "
            "intermediate Treasuries still work. 9% in a 72% growth portfolio is intentionally thin: not relying "
            "on FI for return, relying on it for drawdown buffering and rebalancing-into-equity-weakness "
            "optionality.\n\n"
            "**Would increase if** real yields exceed 3% (making FI competitive on a return basis) or if horizon shortens.\n"
            "**Would reduce if** inflation regime persists and nominal duration stops hedging anything."
        ),
    },
    {
        "name": "TIPS",
        "parent_name": "Income",
        "target_weight": 0.06,
        "tolerance_band": 0.02,
        "benchmark_ticker": "TIP",
        "rationale": (
            "Inflation-hedged real-yield exposure, sized for a young investor's actual risk. The biggest FI risk "
            "at 27 isn't a market crash — it's having returns silently destroyed by an inflationary decade. TIPS "
            "hedge that risk directly via CPI linkage. 6% is 40% of the FI sleeve, heavier than typical "
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
        "target_weight": 0.10,
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
        "target_weight": 0.03,
        "tolerance_band": 0.02,
        "benchmark_ticker": "BIL",
        "rationale": (
            "Operational liquidity, not strategic dry powder. At 27 with a 30+ year horizon, holding meaningful "
            "cash is performance drag — every 1% of cash at long-run equity returns of ~7% real costs ~$2.4k in "
            "terminal wealth per $10k of base capital over 30 years. 3% handles rebalancing friction (funding "
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
