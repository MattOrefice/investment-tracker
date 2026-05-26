"""Seed the securities table with Phase 2 ETF holdings and benchmarks."""
import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import get_connection, initialize_db

HOLDINGS = [
    {
        "ticker": "VOO",
        "name": "Vanguard S&P 500 ETF",
        "asset_class": "US Large Core",
        "security_type": "ETF",
        "expense_ratio": 0.0003,
        "holding_rationale": (
            "VOO delivers S&P 500 exposure at 0.03% with the best tax efficiency of any large-cap passive "
            "fund. The benchmark is SPY — used for attribution because it's the institutional standard — but "
            "SPY's unit investment trust structure prevents internal dividend reinvestment, creating cash drag "
            "and incremental tax events that accumulate over decades. Vanguard's ETF share class structure "
            "historically produces fewer capital gains distributions. The difference between VOO and SPY "
            "compounds quietly; at a 30-year horizon I'm not willing to pay three times the fee for intraday "
            "liquidity I don't need."
        ),
    },
    {
        "ticker": "SPHQ",
        "name": "Invesco S&P 500 Quality ETF",
        "asset_class": "US Large Quality",
        "security_type": "ETF",
        "expense_ratio": 0.0015,
        "holding_rationale": (
            "SPHQ over QUAL was a deliberate methodology choice rooted in accounting quality. QUAL screens on "
            "ROE, earnings variability, and debt-to-equity. SPHQ adds an accruals ratio screen, which filters "
            "out companies with aggressive accounting — high accruals relative to assets predict earnings "
            "reversals, as documented in Sloan (1996) and widely replicated since. CFA training reinforced "
            "this: accruals manipulation is real and persistent, and a quality screen that ignores it is "
            "incomplete. The tradeoff is smaller AUM ($6B vs QUAL's $40B), acceptable given SPHQ isn't at "
            "closure-risk scale. Benchmarking against QUAL creates a natural attribution question over time: "
            "did the accruals screen add or cost value? Would revisit if SPHQ's accruals screen "
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
        "holding_rationale": (
            "VTV captures the value factor through CRSP's multi-metric methodology — P/B, P/E, P/S, P/CF, "
            "and dividend yield — rather than Russell's P/B-heavy single metric used in IWD (the benchmark). "
            "Multi-metric is academically superior: relying solely on P/B overweights asset-heavy industries "
            "and underweights intangible-rich businesses where book value poorly proxies intrinsic value. "
            "VTV also costs 0.04% vs IWD's 0.19%. The holding deliberately differs from the benchmark; the "
            "methodology difference is intentional and defensible."
        ),
    },
    {
        "ticker": "AVUV",
        "name": "Avantis US Small Cap Value ETF",
        "asset_class": "US Small Cap",
        "security_type": "ETF",
        "expense_ratio": 0.0025,
        "holding_rationale": (
            "AVUV is the highest-conviction factor pick in the portfolio. Avantis (founded by former DFA "
            "researchers) targets the size, value, and profitability factors simultaneously — empirically, "
            "the size premium is concentrated almost entirely in profitable small-value firms; unprofitable "
            "small-caps drag index returns and explain why naive small-cap exposure has looked weak for 15 "
            "years. The 0.25% ER is the highest in the portfolio but buys genuine factor exposure. Current "
            "large-cap valuations structurally improve the relative opportunity in cheap, profitable small "
            "companies. Attribution is tracked against IWM to test whether the factor tilt earns its fee "
            "over time. Would revisit if size + value + profitability factor returns underperform "
            "large-cap blend on a rolling 5-year basis, suggesting factor premium decay rather "
            "than temporary cyclical lag."
        ),
    },
    {
        "ticker": "VEA",
        "name": "Vanguard FTSE Developed Markets ETF",
        "asset_class": "International Developed",
        "security_type": "ETF",
        "expense_ratio": 0.0003,
        "holding_rationale": (
            "VEA delivers developed-ex-US exposure at 0.03% versus EFA's 0.32% — ten times more expensive "
            "for substantially the same asset class. VEA tracks FTSE Developed All Cap ex US, which includes "
            "Canada (MSCI EAFE excludes it), giving modestly broader coverage. The valuation thesis driving "
            "this sleeve's 19% weight holds regardless of the Canada distinction; the fee difference is "
            "indefensible for passive exposure. Vanguard's structure also produces fewer capital gains "
            "distributions historically."
        ),
    },
    {
        "ticker": "IEMG",
        "name": "iShares Core MSCI Emerging Markets ETF",
        "asset_class": "Emerging Markets",
        "security_type": "ETF",
        "expense_ratio": 0.0009,
        "holding_rationale": (
            "IEMG is the holding because EEM costs 0.70% for identical exposure — seven times more expensive "
            "and the most egregious benchmark-vs-holding gap in the portfolio. IEMG at 0.09% tracks the same "
            "MSCI Emerging Markets index with the same ~27% China weight. China inclusion was deliberate: the "
            "SAA rationale flags governance risk as a watch item but not yet a reason to exit — China trades "
            "at ~10x P/E and the EM valuation thesis meaningfully includes Chinese equities. Excluding China "
            "via EMXC would be a larger active bet than appropriate at current prices; would revisit if ADR "
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
        "holding_rationale": (
            "VGIT delivers intermediate-term US Treasury exposure at 0.04% versus IEF's 0.15%. Duration is "
            "modestly shorter (~5.5 years vs IEF's ~7.5 years), appropriate for a 9% sleeve inside a 72% "
            "growth portfolio. The shorter duration means VGIT underperforms longer-dated Treasuries in a "
            "flight-to-quality rally but limits drawdown in a rate-selloff — appropriate given the "
            "portfolio's 72% growth allocation. Critically, Treasury interest income is exempt from state "
            "and local taxes — a "
            "real after-tax advantage in a high-income-tax jurisdiction like DC over investment-grade corporate "
            "bond funds with similar yield. Cost minimization is the dominant selection criterion for a sleeve "
            "that exists for drawdown buffering and rebalancing optionality, not return."
        ),
    },
    {
        "ticker": "SCHP",
        "name": "Schwab US TIPS ETF",
        "asset_class": "TIPS",
        "security_type": "ETF",
        "expense_ratio": 0.0003,
        "holding_rationale": (
            "SCHP delivers broad TIPS exposure at 0.03% versus TIP's 0.19% for identical exposure. The "
            "choice of broad TIPS over short-term alternatives (VTIP, STIP) reflects the long-horizon "
            "inflation thesis: at 27, the risk being hedged is not near-term inflation volatility but a "
            "decade of sustained real return erosion. Short-term TIPS protect against current inflation but "
            "have less sensitivity to unexpected long-run inflation regimes. Broad TIPS held for decades are "
            "the more appropriate vehicle. SCHP at 0.03% makes this hedge essentially free to carry. "
            "Would revisit if 5-year TIPS breakeven inflation falls persistently below 1.5%, indicating "
            "a deflationary regime shift that reduces the case for holding real return protection."
        ),
    },
    {
        "ticker": "VNQ",
        "name": "Vanguard Real Estate ETF",
        "asset_class": "Real Assets",
        "security_type": "ETF",
        "expense_ratio": 0.0012,
        "holding_rationale": (
            "VNQ is the standard for US REIT exposure: $35B AUM, 0.12% ER, broad diversification across "
            "property types. Primary risk accepted knowingly: REIT distributions are predominantly "
            "non-qualified income taxed at ordinary rates. In a perfect construction this sleeve would sit "
            "in a tax-advantaged account; working with a taxable account only, the drag is accepted with "
            "eyes open. The real assets diversification benefit justifies carrying the tax friction. Would "
            "revisit if tax-advantaged space becomes available."
        ),
    },
    {
        "ticker": "PDBC",
        "name": "Invesco Optimum Yield Diversified Commodity Strategy No K-1 ETF",
        "asset_class": "Real Assets",
        "security_type": "ETF",
        "expense_ratio": 0.0059,
        "holding_rationale": (
            "PDBC is the only broad commodity ETF worth owning in a taxable account because it avoids "
            "issuing a K-1 tax form. Nearly all commodity futures funds are organized as partnerships and "
            "issue K-1s annually — complicating filing, often arriving late, and potentially triggering "
            "estimated tax requirements. PDBC uses a C-corporation structure instead, eliminating K-1 at "
            "the cost of a higher ER (0.59%). For a 5% position in a taxable account, tax simplicity is "
            "worth materially more than the fee difference versus K-1-issuing alternatives. DJP (the "
            "benchmark) is an exchange-traded note with counterparty risk — used for attribution only, "
            "never as a holding."
        ),
    },
]

# VNQ is already inserted as a holding; skip it in the benchmarks list
BENCHMARKS = [
    {"ticker": "SPY",  "name": "SPDR S&P 500 ETF Trust",                           "asset_class": "US Large Core"},
    {"ticker": "QUAL", "name": "iShares MSCI USA Quality Factor ETF",               "asset_class": "US Large Quality"},
    {"ticker": "IWD",  "name": "iShares Russell 1000 Value ETF",                    "asset_class": "US Large Value"},
    {"ticker": "IWM",  "name": "iShares Russell 2000 ETF",                          "asset_class": "US Small Cap"},
    {"ticker": "EFA",  "name": "iShares MSCI EAFE ETF",                             "asset_class": "International Developed"},
    {"ticker": "EEM",  "name": "iShares MSCI Emerging Markets ETF",                 "asset_class": "Emerging Markets"},
    {"ticker": "IEF",  "name": "iShares 7-10 Year Treasury Bond ETF",               "asset_class": "Core Fixed Income"},
    {"ticker": "TIP",  "name": "iShares TIPS Bond ETF",                             "asset_class": "TIPS"},
    {"ticker": "DJP",  "name": "iPath Bloomberg Commodity Index Total Return ETN",  "asset_class": "Real Assets"},
    {"ticker": "BIL",  "name": "SPDR Bloomberg 1-3 Month T-Bill ETF",               "asset_class": "Cash / SPAXX"},
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
                    (b["ticker"], b["name"], ac_id, "benchmark", None),
                )
                benchmarks_seeded += 1

        if holdings_seeded == 0 and benchmarks_seeded == 0:
            print("Securities already seeded, skipping.")
        else:
            print(f"Seeded {holdings_seeded} holdings, {benchmarks_seeded} benchmarks.")


if __name__ == "__main__":
    seed()
