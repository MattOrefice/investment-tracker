"""Seed the securities table with Phase 2 ETF holdings and benchmarks."""
import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import get_connection, initialize_db

# Expense ratios are hardcoded WITH provenance: no price/data source the app
# already fetches carries them (the chart price API returns no fee field; the
# free fund-fee endpoints require a per-request auth handshake the app does not
# perform), and ETF ERs change ~annually — a daily fetch for an annual value is
# the wrong trade. So each ER records its issuer source and the date it was
# verified, INLINE next to the value. Re-verify against the issuer fund page and
# bump er_as_of when an issuer changes a fee. (See CHANGELOG 2026-07-23.)
_ER_AS_OF = "2026-07-23"

HOLDINGS = [
    {
        "ticker": "VOO",
        "name": "Vanguard S&P 500 ETF",
        "asset_class": "US Large Core",
        "security_type": "ETF",
        "expense_ratio": 0.0003,
        "er_source": 'Vanguard fund page', "er_as_of": _ER_AS_OF,
        "holding_rationale": (
            "VOO delivers S&P 500 exposure at 0.03% with the best tax efficiency of any large-cap passive "
            "fund. The benchmark is SPY — used for attribution because it's the institutional standard — but "
            "SPY's unit investment trust structure prevents internal dividend reinvestment, creating cash drag "
            "and incremental tax events that accumulate over decades. Vanguard's ETF share class structure "
            "historically produces fewer capital gains distributions. The difference between VOO and SPY "
            "compounds quietly; at a 30-year horizon I'm not willing to pay three times the fee for intraday "
            "liquidity I don't need.\n\n"
            "**Would revisit if** the SEC grants ETF share-class relief to competing issuers and a materially "
            "cheaper or more tax-efficient S&P 500 vehicle results. Vanguard's patent expired in May 2023, but "
            "no competitor has launched under the structure because no relief has been granted — the edge is "
            "intact until that changes, and it is a regulatory question rather than a permanent moat."
        ),
    },
    {
        "ticker": "SPHQ",
        "name": "Invesco S&P 500 Quality ETF",
        "asset_class": "US Large Quality",
        "security_type": "ETF",
        "expense_ratio": 0.0015,
        "er_source": 'Invesco fund page', "er_as_of": _ER_AS_OF,
        "holding_rationale": (
            "SPHQ over QUAL was a deliberate methodology choice rooted in accounting quality. QUAL screens on "
            "ROE, earnings variability, and debt-to-equity. SPHQ adds an accruals ratio screen, which filters "
            "out companies with aggressive accounting — high accruals relative to assets predict earnings "
            "reversals, as documented in Sloan (1996) and widely replicated since. CFA training reinforced "
            "this: accruals manipulation is real and persistent, and a quality screen that ignores it is "
            "incomplete. The tradeoff is smaller AUM ($6B vs QUAL's $40B), acceptable given SPHQ isn't at "
            "closure-risk scale. Benchmarking against QUAL creates a natural attribution question over time: "
            "did the accruals screen add or cost value?\n\n"
            "**Would revisit if** SPHQ's accruals screen "
            "fails to demonstrate additive return versus QUAL over a full market cycle (5+ years), "
            "directly testing whether the Sloan (1996) accounting quality premium persists in a "
            "live portfolio."
        ),
    },
    {
        "ticker": "VTV",
        "name": "Vanguard Value ETF",
        "asset_class": "US Large Value",
        "security_type": "ETF",
        "expense_ratio": 0.0004,
        "er_source": 'Vanguard fund page', "er_as_of": _ER_AS_OF,
        "holding_rationale": (
            "VTV captures the value factor through CRSP's multi-metric methodology — P/B, P/E, P/S, P/CF, "
            "and dividend yield — rather than Russell's P/B-heavy single metric used in IWD (the benchmark). "
            "Multi-metric is academically superior: relying solely on P/B overweights asset-heavy industries "
            "and underweights intangible-rich businesses where book value poorly proxies intrinsic value. "
            "VTV also costs 0.04% vs IWD's 0.19%. The holding deliberately differs from the benchmark; the "
            "methodology difference is intentional and defensible.\n\n"
            "**Would revisit if** the multi-metric value definition stopped distinguishing itself from "
            "single-metric value over a full cycle, or if profitability-integrated construction proved "
            "materially better — in which case this sleeve moves to AVLV, the domestic half of the same "
            "asymmetry AVIV's rationale names."
        ),
    },
    {
        "ticker": "AVUV",
        "name": "Avantis US Small Cap Value ETF",
        "asset_class": "US Small Cap",
        "security_type": "ETF",
        "expense_ratio": 0.0025,
        "er_source": 'Avantis fund page', "er_as_of": _ER_AS_OF,
        "holding_rationale": (
            "AVUV is the highest-conviction factor pick in the US book. Avantis (founded by former DFA "
            "researchers) targets the size, value, and profitability factors simultaneously — empirically, "
            "the size premium is concentrated almost entirely in profitable small-value firms; unprofitable "
            "small-caps drag index returns and explain why naive small-cap exposure has looked weak for 15 "
            "years. The 0.25% ER is a real cost but buys genuine factor exposure rather than index replication. Current "
            "large-cap valuations structurally improve the relative opportunity in cheap, profitable small "
            "companies. Attribution is tracked against IWM to test whether the factor tilt earns its fee "
            "over time.\n\n"
            "**Would revisit if** size + value + profitability factor returns underperform "
            "large-cap blend on a rolling 5-year basis, suggesting factor premium decay rather "
            "than temporary cyclical lag."
        ),
    },
    {
        "ticker": "VEA",
        "name": "Vanguard FTSE Developed Markets ETF",
        "asset_class": "International Core",
        "security_type": "ETF",
        "expense_ratio": 0.0003,
        "er_source": 'Vanguard fund page', "er_as_of": _ER_AS_OF,
        "holding_rationale": (
            "VEA delivers developed-ex-US exposure at 0.03% versus EFA's 0.32% — ten times more expensive "
            "for substantially the same asset class. VEA tracks FTSE Developed All Cap ex US, which includes "
            "Canada (MSCI EAFE excludes it), giving modestly broader coverage. The valuation thesis behind "
            "this sleeve's weight holds regardless of the Canada distinction; the fee difference is "
            "indefensible for passive exposure. Vanguard's structure also produces fewer capital gains "
            "distributions historically.\n\n"
            "**Would revisit if** a cheaper or more tax-efficient developed ex-US core appeared. At 3 bps "
            "against a cap-weighted benchmark there is little else to falsify — the position is a cost choice, "
            "not a view, and the only thing that unseats it is a better instrument for the same exposure."
        ),
    },
    # The three international tilt holdings (added Phase 39). Holding-level
    # rationales were authored after the fact; a fresh seed inserts them directly
    # from the fields below, and the existing demo.db / tracker.db pick them up via
    # tools/migrate_{demo,personal}_intl_rationales.py (pages/8_Research.py guards
    # on truthiness, so an un-migrated DB simply omits the expander).
    {
        "ticker": "IDHQ",
        "name": "Invesco S&P International Developed Quality ETF",
        "asset_class": "International Quality",
        "security_type": "ETF",
        "expense_ratio": 0.0029,
        "er_source": 'Invesco fund page', "er_as_of": _ER_AS_OF,
        "holding_rationale": (
            "IDHQ tracks S&P's quality screen of developed ex-US large and mid caps: the same issuer as "
            "SPHQ, the same index provider, the same three fundamental measures — return on equity, "
            "accruals, and leverage. That symmetry is the point. The US quality sleeve and the "
            "international one are the same screen applied to different regions, so a divergence in their "
            "results is a fact about the regions rather than about two vendors' definitions of quality.\n\n"
            "The benchmark is IQLT, iShares' MSCI quality index abroad, which reproduces the SPHQ-to-QUAL "
            "relationship exactly. Cost is a wash — 0.29% against IQLT's 0.30% — so this is not a fee "
            "decision; it is a methodology one, and it is the same methodology decision already made "
            "domestically.\n\n"
            "The known asymmetry: S&P classifies South Korea as developed where MSCI does not, so IDHQ "
            "holds Korea and IQLT doesn't. That is a real universe difference, disclosed on the Factor "
            "Profile page, and it moves with each semi-annual reconstitution rather than sitting at a "
            "fixed weight.\n\n"
            "**Would revisit if** S&P's quality screen diverged materially from MSCI's over a full cycle, "
            "which would mean the two are measuring different things and the SPHQ-IDHQ symmetry is "
            "cosmetic."
        ),
    },
    {
        "ticker": "AVIV",
        "name": "Avantis International Large Cap Value ETF",
        "asset_class": "International Large Value",
        "security_type": "ETF",
        "expense_ratio": 0.0025,
        "er_source": 'Avantis fund page', "er_as_of": _ER_AS_OF,
        "holding_rationale": (
            "Value abroad, from the same shop that runs the US small-value sleeve. Avantis integrates "
            "profitability into its value screen rather than sorting on price alone — the same "
            "construction as AVUV, applied to international large caps.\n\n"
            "That is worth naming as an asymmetry rather than hiding: VTV, the US large-value holding, "
            "tracks a plain cap-weighted value index and does not integrate profitability. So the "
            "international sleeve holds the more deliberate instrument. If that asymmetry matters, the "
            "resolution is AVLV in the US rather than EFV abroad — the domestic sleeve is the less "
            "considered one, not this one.\n\n"
            "The benchmark is EFV, MSCI's EAFE value index. Holding and benchmark come from different "
            "index families, exactly as VTV and IWD do, so selection effect measures implementation "
            "rather than the value premium itself. At 0.25% against EFV's 0.33%, the profitability "
            "integration costs nothing relative to the passive alternative.\n\n"
            "**Would revisit if** the profitability integration proved to be doing the work rather than "
            "the value screen — in which case the US sleeve moves to AVLV rather than this one moving to "
            "EFV."
        ),
    },
    {
        "ticker": "AVDV",
        "name": "Avantis International Small Cap Value ETF",
        "asset_class": "International Small Value",
        "security_type": "ETF",
        "expense_ratio": 0.0036,
        "er_source": 'Avantis fund page', "er_as_of": _ER_AS_OF,
        "holding_rationale": (
            "The small-value interaction abroad, held for the reason AVUV is held at home: the size "
            "premium concentrates almost entirely in profitable small-value firms, and unprofitable small "
            "caps are what made naive small-cap exposure look weak for fifteen years.\n\n"
            "The benchmark comparison here is weaker than the other two, deliberately. SCZ is iShares' "
            "EAFE small-cap index — small blend, not small value — because no passive international "
            "small-value index fund exists at acceptable cost. So the benchmark captures the size "
            "dimension and not the value tilt, and selection effect for this sleeve carries the value "
            "premium itself rather than measuring implementation. This is the same compromise the US "
            "sleeve already makes against IWM, with the same consequence: read the factor exhibit, not "
            "attribution, for whether the tilt is being paid.\n\n"
            "At 0.36% it is among the portfolio's costliest positions, and its highest-conviction one — "
            "the fee buys genuine factor exposure rather than index replication. AVDV holds no South "
            "Korea — verified at the position level — so the universe mismatch that inflates the "
            "developed-core residual doesn't apply here.\n\n"
            "**Would revisit if** the small-value interaction proved materially weaker in developed ex-US "
            "than domestically, or if a passive international small-value fund appeared at materially "
            "lower cost."
        ),
    },
    {
        "ticker": "IEMG",
        "name": "iShares Core MSCI Emerging Markets ETF",
        "asset_class": "Emerging Markets",
        "security_type": "ETF",
        "expense_ratio": 0.0009,
        "er_source": 'iShares fund page', "er_as_of": _ER_AS_OF,
        "holding_rationale": (
            "IEMG is the holding because EEM costs 0.70% for identical exposure — seven times more expensive, "
            "and the largest same-index fee gap in the portfolio at 61 bps. IEMG at 0.09% tracks the same "
            "MSCI Emerging Markets index with the same ~27% China weight. China inclusion was deliberate: the "
            "SAA rationale flags governance risk as a watch item but not yet a reason to exit — China trades "
            "at ~10x P/E and the EM valuation thesis meaningfully includes Chinese equities. Excluding China "
            "via EMXC would be a larger active bet than appropriate at current prices.\n\n"
            "**Would revisit if** ADR "
            "delisting risk materializes legislatively or if another sector-level government intervention "
            "occurs."
        ),
    },
    {
        "ticker": "VGIT",
        "name": "Vanguard Intermediate-Term Treasury ETF",
        "asset_class": "Core Fixed Income",
        "security_type": "ETF",
        "expense_ratio": 0.0004,
        "er_source": 'Vanguard fund page', "er_as_of": _ER_AS_OF,
        "holding_rationale": (
            "VGIT delivers intermediate-term US Treasury exposure at 0.04% versus IEF's 0.15%. Duration is "
            "modestly shorter (~5.5 years vs IEF's ~7.5 years), appropriate for a 6% sleeve inside a 78% "
            "growth portfolio. The shorter duration means VGIT underperforms longer-dated Treasuries in a "
            "flight-to-quality rally but limits drawdown in a rate-selloff — appropriate given the "
            "portfolio's 78% growth allocation. Critically, Treasury interest income is exempt from state "
            "and local taxes — a "
            "real after-tax advantage in a high-income-tax jurisdiction like DC over investment-grade corporate "
            "bond funds with similar yield. Cost minimization is the dominant selection criterion for a sleeve "
            "that exists for drawdown buffering and rebalancing optionality, not return.\n\n"
            "**Would revisit if** nominal Treasuries stopped hedging equity drawdowns across successive "
            "inflationary episodes. 2022 was one such failure; this sleeve's case rests on it being the "
            "exception rather than the regime.\n\n"
            "**Would also revisit if** real yields rose enough to make longer duration compensated for its "
            "additional volatility."
        ),
    },
    {
        "ticker": "SCHP",
        "name": "Schwab US TIPS ETF",
        "asset_class": "TIPS",
        "security_type": "ETF",
        "expense_ratio": 0.0003,
        "er_source": 'Schwab fund page', "er_as_of": _ER_AS_OF,
        "holding_rationale": (
            "SCHP delivers broad TIPS exposure at 0.03% versus TIP's 0.19% for identical exposure. The "
            "choice of broad TIPS over short-term alternatives (VTIP, STIP) reflects the long-horizon "
            "inflation thesis: at 27, the risk being hedged is not near-term inflation volatility but a "
            "decade of sustained real return erosion. Short-term TIPS protect against current inflation but "
            "have less sensitivity to unexpected long-run inflation regimes. Broad TIPS held for decades are "
            "the more appropriate vehicle. SCHP at 0.03% makes this hedge essentially free to carry.\n\n"
            "**Would revisit if** 5-year TIPS breakeven inflation falls persistently below 1.5%, indicating "
            "a deflationary regime shift that reduces the case for holding real return protection."
        ),
    },
    {
        "ticker": "VNQ",
        "name": "Vanguard Real Estate ETF",
        "asset_class": "Real Assets",
        "security_type": "ETF",
        "expense_ratio": 0.0012,
        "er_source": 'Vanguard fund page', "er_as_of": _ER_AS_OF,
        "holding_rationale": (
            "VNQ is the standard for US REIT exposure: $35B AUM, 0.12% ER, broad diversification across "
            "property types. Primary risk accepted knowingly: REIT distributions are predominantly "
            "non-qualified income taxed at ordinary rates. In a perfect construction this sleeve would sit "
            "in a tax-advantaged account; working with a taxable account only, the drag is accepted with "
            "eyes open. The real assets diversification benefit justifies carrying the tax friction.\n\n"
            "**Would revisit if** tax-advantaged space becomes available."
        ),
    },
    {
        "ticker": "PDBC",
        "name": "Invesco Optimum Yield Diversified Commodity Strategy No K-1 ETF",
        "asset_class": "Real Assets",
        "security_type": "ETF",
        "expense_ratio": 0.0059,
        "er_source": 'Invesco fund page', "er_as_of": _ER_AS_OF,
        "holding_rationale": (
            "Among the few broad commodity ETFs that avoid a K-1 (COMT, BCI), PDBC is the one I hold for "
            "its liquidity and track record. Nearly all commodity futures funds are organized as partnerships and "
            "issue K-1s annually — complicating filing, often arriving late, and potentially triggering "
            "estimated tax requirements. PDBC uses a C-corporation structure instead, eliminating K-1 at "
            "the cost of a higher ER (0.59%). For a 5% position in a taxable account, tax simplicity is "
            "worth materially more than the fee difference versus K-1-issuing alternatives. DJP (the "
            "benchmark) is an exchange-traded note with counterparty risk — used for attribution only, "
            "never as a holding.\n\n"
            "**Would revisit if** a broad no-K-1 commodity fund appeared at materially lower cost or with "
            "better liquidity. The 0.59% is paid for tax structure rather than strategy, so a cheaper "
            "equivalent would make this holding indefensible on its own terms."
        ),
    },
]

# VNQ is already inserted as a holding; skip it in the benchmarks list
BENCHMARKS = [
    {"ticker": "SPY",  "name": "SPDR S&P 500 ETF Trust",                           "asset_class": "US Large Core",
     "expense_ratio": 0.000945, "er_source": "SSGA SPY fund page",  "er_as_of": _ER_AS_OF},
    {"ticker": "QUAL", "name": "iShares MSCI USA Quality Factor ETF",               "asset_class": "US Large Quality",
     "expense_ratio": 0.0015,   "er_source": "iShares QUAL fund page", "er_as_of": _ER_AS_OF},
    {"ticker": "IWD",  "name": "iShares Russell 1000 Value ETF",                    "asset_class": "US Large Value",
     "expense_ratio": 0.0019,   "er_source": "iShares IWD fund page",  "er_as_of": _ER_AS_OF},
    {"ticker": "IWM",  "name": "iShares Russell 2000 ETF",                          "asset_class": "US Small Cap",
     "expense_ratio": 0.0019,   "er_source": "iShares IWM fund page",  "er_as_of": _ER_AS_OF},
    {"ticker": "EFA",  "name": "iShares MSCI EAFE ETF",                             "asset_class": "International Core",
     "expense_ratio": 0.0032,   "er_source": "iShares EFA fund page (net ER)", "er_as_of": _ER_AS_OF},
    {"ticker": "IQLT", "name": "iShares MSCI Intl Quality Factor ETF",              "asset_class": "International Quality",
     "expense_ratio": 0.003, "er_source": "iShares IQLT fund page", "er_as_of": _ER_AS_OF},
    {"ticker": "EFV",  "name": "iShares MSCI EAFE Value ETF",                       "asset_class": "International Large Value",
     "expense_ratio": 0.0033, "er_source": "iShares EFV fund page", "er_as_of": _ER_AS_OF},
    {"ticker": "SCZ",  "name": "iShares MSCI EAFE Small-Cap ETF",                   "asset_class": "International Small Value",
     "expense_ratio": 0.004, "er_source": "iShares SCZ fund page", "er_as_of": _ER_AS_OF},
    {"ticker": "EEM",  "name": "iShares MSCI Emerging Markets ETF",                 "asset_class": "Emerging Markets",
     "expense_ratio": 0.007,    "er_source": "iShares EEM fund page (net ER)", "er_as_of": _ER_AS_OF},
    {"ticker": "IEF",  "name": "iShares 7-10 Year Treasury Bond ETF",               "asset_class": "Core Fixed Income",
     "expense_ratio": 0.0015,   "er_source": "iShares IEF fund page",  "er_as_of": _ER_AS_OF},
    {"ticker": "TIP",  "name": "iShares TIPS Bond ETF",                             "asset_class": "TIPS",
     "expense_ratio": 0.0019,   "er_source": "iShares TIP fund page",  "er_as_of": _ER_AS_OF},
    # DJP (iPath Bloomberg Commodity ETN) delisted May 2020; the Real Assets
    # benchmark is DBC (see src/benchmarks.py). Source + reseed now agree on DBC.
    {"ticker": "DBC",  "name": "Invesco DB Commodity Index Tracking Fund",          "asset_class": "Real Assets",
     "expense_ratio": 0.0085,   "er_source": "Invesco DBC fund page (0.85% mgmt fee; total incl. futures brokerage ~0.87-0.89%)", "er_as_of": _ER_AS_OF},
    {"ticker": "BIL",  "name": "SPDR Bloomberg 1-3 Month T-Bill ETF",               "asset_class": "Cash / SPAXX",
     "expense_ratio": 0.001356, "er_source": "SSGA BIL fund page",     "er_as_of": _ER_AS_OF},
]


def seed():
    initialize_db()
    with get_connection() as conn:
        # Migrate: add holding_rationale column if not present
        cols = {row[1] for row in conn.execute("PRAGMA table_info(securities)").fetchall()}
        if "holding_rationale" not in cols:
            conn.execute("ALTER TABLE securities ADD COLUMN holding_rationale TEXT")

        # Build asset class name → id map
        ac_map = {
            r["name"]: r["asset_class_id"]
            for r in conn.execute("SELECT asset_class_id, name FROM asset_classes").fetchall()
        }

        holdings_seeded = 0
        for h in HOLDINGS:
            ac_id = ac_map.get(h["asset_class"])
            if ac_id is None:
                print(f"  WARNING: asset class '{h['asset_class']}' not found — skipping {h['ticker']}")
                continue
            existing = conn.execute(
                "SELECT ticker FROM securities WHERE ticker = ?", (h["ticker"],)
            ).fetchone()
            if existing is None:
                conn.execute(
                    """INSERT INTO securities
                       (ticker, name, asset_class_id, security_type, expense_ratio, holding_rationale)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (h["ticker"], h["name"], ac_id, h["security_type"],
                     h["expense_ratio"], h["holding_rationale"]),
                )
                holdings_seeded += 1

        benchmarks_seeded = 0
        for b in BENCHMARKS:
            ac_id = ac_map.get(b["asset_class"])
            if ac_id is None:
                print(f"  WARNING: asset class '{b['asset_class']}' not found — skipping {b['ticker']}")
                continue
            existing = conn.execute(
                "SELECT ticker FROM securities WHERE ticker = ?", (b["ticker"],)
            ).fetchone()
            if existing is None:
                conn.execute(
                    """INSERT INTO securities
                       (ticker, name, asset_class_id, security_type, expense_ratio)
                       VALUES (?, ?, ?, ?, ?)""",
                    (b["ticker"], b["name"], ac_id, "benchmark", b.get("expense_ratio")),
                )
                benchmarks_seeded += 1

        if holdings_seeded == 0 and benchmarks_seeded == 0:
            print("Securities already seeded, skipping.")
        else:
            print(f"Seeded {holdings_seeded} holdings, {benchmarks_seeded} benchmarks.")


if __name__ == "__main__":
    seed()
