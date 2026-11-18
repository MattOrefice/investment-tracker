"""Where every dollar figure in the committed tree comes from (#721).

No figure from the personal book goes into committed text: no position value, total,
gain, drag or balance read off data/tracker.db or a positions export, and no rate derived
from private/personal_profile.json. Those are reported in a session, never committed.
A dollar figure the tree does type comes from one of three places, and FIGURES below says
which, for every figure in every file:

  demo book   data/demo.db, the frozen test book built from it
              (tests/fixtures/frozen_book.db), or the paper book's seed. Both books are
              public.
  synthetic   an example made up for the code or test it sits in.
  public      a published source, cited with its date.

A zero or a one-dollar unit ($0, $0.00, $1, $1.00: an empty amount, a money-market NAV,
a growth-of-$1 base) states no amount from any book, and needs no entry.

WHAT COUNTS AS A DOLLAR FIGURE. A "$" followed by a digit, with the number's grouping,
decimals and a K, M or B suffix: "$19.4B", "$1,000.00", "\\$153k". A sign before the "$"
is not part of the figure. A format placeholder ("${x:,.0f}", "$%.0f") has no digit after
its "$" and is not a figure.

tests/test_dollar_figures.py reads every file `git ls-files` lists that decodes as UTF-8
and fails on a figure with no entry here, and on an entry whose figure its file no longer
holds.
"""
from __future__ import annotations

import re

DEMO = "demo book"
SYNTHETIC = "synthetic"
PUBLIC = "public"
CATEGORIES = (DEMO, SYNTHETIC, PUBLIC)

# "$" + a number (grouped by commas or not, decimals optional) + an optional K/M/B suffix.
# The suffix must end a word: "$75 billion" is "$75", "$100 bps" is "$100".
_FIGURE = re.compile(
    r"\$ ?((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?: ?([KkMmBb])(?![A-Za-z0-9]))?")


def figures_in(text: str) -> "list[tuple[int, str]]":
    """Every dollar figure in the text, as (line, figure), the figure spelled "$" + its
    number + its suffix, without a space: "$ 5k" is "$5k"."""
    out = []
    for m in _FIGURE.finditer(text):
        out.append((text.count("\n", 0, m.start()) + 1, f"${m.group(1)}{m.group(2) or ''}"))
    return out


def is_unit_or_zero(figure: str) -> bool:
    """$0, $0.00, $1, $1.00 and the like: no amount from any book."""
    body = figure[1:]
    if body[-1:].isalpha():
        return False
    return float(body.replace(",", "")) in (0.0, 1.0)


# Whole files whose figures are generated from a public book and held to it by a test.
SOURCED_FILES: dict[str, str] = {
    "tests/fixtures/locked_figures_golden.json": (
        f"{DEMO}: every figure the committed books' quarter locks print, written by "
        "tools/update_locked_figures_golden.py from data/demo.db and the frozen book only, "
        "and rendered again from them by tests/test_locked_figures_golden.py"),
}

# Sources several files share.
_SEED = f"{DEMO}: the paper book's inception seed (src/seed_paper_trades.py)"
_NPORT = (f"{PUBLIC}: SPHQ's and QUAL's net assets on July 31, 2026, from each trust's "
          "SEC Form N-PORT (accessions 0001209466-26-001344 and 0001004726-26-008857), "
          "read September 28, 2026 (#456)")
_VANGUARD_VNQ = (f"{PUBLIC}: Vanguard's net assets for VNQ's ETF share class and for the "
                 "fund, August 31, 2026, read for #456")
_OLD_THESIS_AUM = f"{DEMO}: an AUM the demo book's thesis prose typed before #456 dropped it"
_IRS_2026 = (f"{PUBLIC}: the IRS's 2026 retirement-plan limits, IRS Notice 2025-67 "
             "(November 2025), held in src/location_config.py's _2026 constants")
_CLOUD_BUG = (f"{DEMO}: the demo book's current value under the Phase 8p duplicate "
              "price-index bug")
_BOOK_FREE = f"{SYNTHETIC}: a made-up amount in a constructed fixture or example"

FIGURES: dict[str, dict[str, str]] = {
    "CHANGELOG.md": {
        "$1,000": _SEED,
        "$1,299": f"{DEMO}: the paper book's value before a fix",
        "$1,329": f"{DEMO}: the paper book's value after the same fix",
        "$30": _CLOUD_BUG,
        "$10k": f"{DEMO}: the size of the paper trades' first seed",
    },
    "docs/architecture.md": {
        "$5k": f"{SYNTHETIC}: a plausibility bound a test sets on the demo book's outputs",
        "$500k": f"{SYNTHETIC}: a plausibility bound a test sets on the demo book's outputs",
    },
    "docs/phase_11_diagnostic.md": {
        "$976.24": f"{DEMO}: the paper book's inception value on adjusted closes",
        "$1,297.29": f"{DEMO}: the paper book's value on the day of the diagnosis",
        "$1,000.00": _SEED,
        "$1,000.0001": f"{DEMO}: the paper book's seed after fractional-share rounding",
    },
    "pages/11_Capital_Deployment.py": {
        "$744": f"{SYNTHETIC}: the worst of 3,000 synthetic configurations of suggest_buys "
                "(274a0b8)",
        "$4,810": f"{SYNTHETIC}: the same synthetic configuration's shortfall (274a0b8)",
    },
    "pages/12_Tax_Lots.py": {
        "$0.01": f"{SYNTHETIC}: the page's reconciliation tolerance",
        "$3,000": (f"{PUBLIC}: the yearly limit on capital losses deducted against "
                   "ordinary income, 26 U.S.C. § 1211(b)(1) (U.S. Code, 2024 edition)"),
    },
    "pages/14_Asset_Location.py": {
        "$7,500": _IRS_2026,
        "$153k": _IRS_2026,
        "$168k": _IRS_2026,
    },
    "pages/15_Liquidity.py": {
        "$10,000": f"{SYNTHETIC}: an example of text Markdown misreads as LaTeX",
        "$20,000": f"{SYNTHETIC}: an example of text Markdown misreads as LaTeX",
    },
    "pages/16_Policy_Backtest.py": {
        "$5,000": f"{SYNTHETIC}: the growth chart's lowest tick, a multiple of its base",
        "$100,000": f"{SYNTHETIC}: the growth chart's highest tick, a multiple of its base",
    },
    "pages/2_Performance.py": {
        "$130.61": f"{DEMO}: the VEA shares the demo book sold in the Phase 39 restructure",
    },
    "pages/7_Risk.py": {
        "$1,250": f"{SYNTHETIC}: a format example in a docstring",
        "$40": f"{SYNTHETIC}: a format example in a docstring",
    },
    "pages/8_Research.py": {
        "$250k": f"{SYNTHETIC}: the example portfolio the expense-ratio saving is stated at",
        "$436": f"{SYNTHETIC}: the saving that example used to hardcode",
    },
    "src/benchmarks.py": {
        "$0.60": f"{SYNTHETIC}: the 60/40 benchmark's weights on its one-dollar base",
        "$0.40": f"{SYNTHETIC}: the 60/40 benchmark's weights on its one-dollar base",
    },
    "src/household.py": {
        "$5,000": f"{SYNTHETIC}: a docstring's example of the summary line",
    },
    "src/location_actions.py": {
        "$450": f"{SYNTHETIC}: two balances the deploy sentence is calibrated across",
        "$45,000": f"{SYNTHETIC}: two balances the deploy sentence is calibrated across",
        "$0.40": f"{SYNTHETIC}: an example of a sub-dollar buy leg",
    },
    "src/policy_backtest.py": {
        "$53,171": f"{SYNTHETIC}: the formatter's docstring example",
    },
    "src/seed_paper_trades.py": {
        "$1,000": _SEED,
        "$1k": _SEED,
    },
    "src/seed_securities.py": {
        "$19,446,477,532.34": _NPORT,
        "$46,531,547,421.38": _NPORT,
        "$19.4B": _NPORT,
        "$46.5B": _NPORT,
        "$6B": _OLD_THESIS_AUM,
        "$40B": _OLD_THESIS_AUM,
        "$35B": _OLD_THESIS_AUM,
        "$38.1B": _VANGUARD_VNQ,
        "$70.8B": _VANGUARD_VNQ,
    },
    "tests/render/test_cost_basis_is_open_lots.py": {
        "$52.65": f"{DEMO}: the price of the VEA shares the demo book sold in Phase 39",
        "$1,157": f"{DEMO}: the demo book's cost-basis caption before #357",
        "$1,026.32": f"{DEMO}: the demo book's Tax Lots cost basis",
        "$6,973": f"{SYNTHETIC}: the test's own Roth buy, 10 VOO at 697.30",
    },
    "tests/render/test_performance_render.py": {"$30": _CLOUD_BUG},
    "tests/render/test_policy_backtest_page.py": {
        f: f"{SYNTHETIC}: the growth chart's ticks"
        for f in ("$5,000", "$10,000", "$20,000", "$50,000", "$100,000")},
    "tests/render/test_research_render.py": {
        "$436": f"{SYNTHETIC}: the Research page's example saving, hardcoded and computed",
        "$425": f"{SYNTHETIC}: the Research page's example saving, hardcoded and computed",
    },
    "tests/render/test_spaxx_alone_weights_stand_660.py": {
        "$20.00": f"{DEMO}: the frozen book's SPAXX funding float",
    },
    "tests/render/test_stage2_check_conditioned.py": {"$1k": _SEED},
    "tests/render/test_stage2_is_cash_flow_neutral.py": {
        "$300": f"{DEMO}: the frozen book's flows in the window the test names",
        "$1.4k": f"{DEMO}: the frozen book's value in the same window",
    },
    "tests/render/test_tax_lots_accounts.py": {"$100": _BOOK_FREE},
    "tests/render/test_unfunded_warning_662.py": {
        "$1,000": f"{SYNTHETIC}: the amount the test enters to deploy",
    },
    "tests/test_asset_location.py": {f: _BOOK_FREE for f in ("$80,000", "$60,000", "$10")},
    "tests/test_asset_location_residence.py": {f: _BOOK_FREE for f in ("$1,234", "$4,000")},
    "tests/test_attribution.py": {
        "$0.60": f"{SYNTHETIC}: the 60/40 benchmark's weights on its one-dollar base",
        "$0.40": f"{SYNTHETIC}: the 60/40 benchmark's weights on its one-dollar base",
    },
    "tests/test_backtest_history.py": {
        "$1.543": f"{DEMO}: VOO's March 2024 dividend as data/demo.db stores it",
    },
    "tests/test_dollar_figures.py": {
        f: f"{SYNTHETIC}: the census's own examples"
        for f in ("$12.34", "$5,678", "$9.9B", "$2k", "$75", "$0.40", "$10", "$1,000",
                  "$1k")},
    "tests/test_drip.py": {
        f: _BOOK_FREE for f in ("$100", "$110", "$4.90", "$98", "$20", "$200")},
    "tests/test_external_cashflows.py": {
        "$500": _BOOK_FREE,
        "$1,100": _BOOK_FREE,
        "$1k": _SEED,
        "$1,000": _SEED,
    },
    "tests/test_flow_in_value_units.py": {
        "$1.823": f"{DEMO}: VOO's September 2026 dividend as data/demo.db stores it",
    },
    "tests/test_harvest.py": {f: _BOOK_FREE for f in ("$200", "$100", "$50", "$500")},
    "tests/test_household_aggregation.py": {
        f: _BOOK_FREE for f in ("$6,000", "$1,000", "$3,000", "$10,000")},
    "tests/test_household_view.py": {
        f: _BOOK_FREE for f in ("$100,000", "$5,000", "$40,000", "$60,000")},
    "tests/test_liquidity.py": {
        f: _BOOK_FREE for f in ("$2,500", "$1,500", "$1,000", "$20k")},
    "tests/test_location_actions.py": {
        f: _BOOK_FREE for f in (
            "$100", "$1,000", "$7,500", "$6,000", "$1,500", "$4,750", "$6k", "$1.5k",
            "$0.5k", "$0.75k", "$47.50", "$37", "$12,345", "$6,420", "$48", "$123",
            "$50,000", "$8,000", "$58,000", "$100,000", "$2,000", "$200", "$3,000",
            "$4,000", "$6.42", "$0.40", "$1.13", "$0.01", "$0.20", "$6", "$448")},
    "tests/test_locked_figures_golden.py": {
        "$1,358": f"{SYNTHETIC}: an example of the figure parser's input",
    },
    "tests/test_operations_pages_421.py": {
        "$100": f"{SYNTHETIC}: the harvest action threshold the code sets",
    },
    "tests/test_pending_activity.py": {
        f: _BOOK_FREE for f in (
            "$40.00", "$50.00", "$500.00", "$100.00", "$400.00", "$90.00", "$20.00",
            "$10.00", "$110.00", "$25.00", "$75.00", "$5.00", "$70.00", "$250.00",
            "$1,000.00", "$1,250.00")},
    "tests/test_policy_backtest_reads.py": {
        f: _BOOK_FREE for f in ("$10,000", "$53,171", "$86,827", "$49,839", "$12,000")},
    "tests/test_prices.py": {"$30": _CLOUD_BUG},
    "tests/test_quarter_frontier_cap.py": {
        "$21": f"{DEMO}: the demo book's positions a stale lookback dropped",
        "$1,304": f"{DEMO}: the demo book's value then",
    },
    "tests/test_rebalance.py": {
        f: _BOOK_FREE for f in (
            "$600", "$500", "$1100", "$400", "$300", "$1000", "$2000", "$800", "$200",
            "$0.01", "$100", "$50.89", "$0.05", "$0.15", "$5000", "$3900")},
    "tests/test_reports.py": {f: _BOOK_FREE for f in ("$200", "$1,000", "$1,200", "$1,234")},
    "tests/test_returns.py": {f: _BOOK_FREE for f in ("$100", "$110", "$50", "$160", "$200")},
    "tests/test_seed.py": {"$1000": _SEED},
    "tests/test_sell_plan.py": {
        f: _BOOK_FREE for f in (
            "$500", "$200", "$100", "$400", "$50,000", "$4,000.00", "$190.00",
            "$18,000.00", "$82,000.00", "$100,000.00")},
    "tests/test_style_box_withdrawn.py": {
        "$250B": f"{SYNTHETIC}: the withdrawn style-box method's authored size anchor",
    },
    "tests/test_tax_lots.py": {
        f: _BOOK_FREE for f in ("$50", "$60", "$1,000", "$40", "$5,000", "$10", "$25")},
    "tests/test_theses_read_their_figures.py": {
        "$19.4B": _NPORT,
        "$46.5B": _NPORT,
        "$2.4k": f"{DEMO}: a figure the demo book's thesis 12 typed before #447 dropped it",
        "$35B": _OLD_THESIS_AUM,
        "$6B": _OLD_THESIS_AUM,
    },
    "tests/test_thesis_figures_sourced.py": {
        "$35B": _OLD_THESIS_AUM,
        "$38.1B": _VANGUARD_VNQ,
        "$70.8B": _VANGUARD_VNQ,
        "$10k": f"{DEMO}: a figure the demo book's thesis 11 typed before #456 dropped it",
        "$6B": _OLD_THESIS_AUM,
        "$40B": _OLD_THESIS_AUM,
        "$19.4B": _NPORT,
        "$46.5B": _NPORT,
    },
    "tests/test_trade_writer.py": {f: _BOOK_FREE for f in ("$0.10", "$100")},
    "tests/test_unmapped_holdings_notice.py": {
        f: _BOOK_FREE for f in ("$1000.00", "$900.00", "$100.00", "$10.00", "$9.00")},
    "tools/migrate_operations_copy.py": {"$19.4B": _NPORT, "$46.5B": _NPORT},
}
