"""
Fama-French 5-Factor regional decomposition — data ingestion and regression engine.

Architecture (v1.1 — replaces single full-portfolio regression from v1.0)
--------------------------------------------------------------------------
Three equity sleeves are regressed separately against region-appropriate
factor sets.  A single full-portfolio regression conflated returns from
international and real-asset sleeves — which the US-only FF5 model cannot
span — into the alpha estimate, producing a methodologically misleading
+826 bps significant alpha.  The decomposed approach measures factor
exposure within the universe each factor set spans.

Data sources: Ken French Data Library (Dartmouth)
  US daily:           F-F_Research_Data_5_Factors_2x3_daily_CSV.zip
  Developed ex-US:    Developed_ex_US_5_Factors_Daily_CSV.zip
  EM (daily):         NOT PUBLISHED by Ken French.  Monthly EM factors
                      would yield T≈12 over a 1-year window — below any
                      threshold for stable inference.  EM sleeve excluded
                      from regression; included in qualitative disclosure.

Factor returns in source files are in percent (0.50 = 0.50%); converted
to decimal on load.  Missing-data sentinel is -99.99; replaced with NaN.

Standard errors: Newey-West HAC, L = floor(4 * (T/100)^(2/9)) per regression.
"""
from __future__ import annotations

import io
import sys
import time
import warnings
import zipfile
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests
from statsmodels.regression.linear_model import OLS
from statsmodels.tools import add_constant

from src.benchmarks import get_custom_blended_series
from src.holdings import get_holdings_on_date, get_portfolio_account_id, get_portfolio_value_series
from src.prices import gap_fetch_cause, get_prices, total_return_series
from src.prose_helpers import label, significance_label, significance_level

_ROOT = Path(__file__).resolve().parent.parent

# ── Factor configuration ───────────────────────────────────────────────────────

_BASE_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"

_CACHE_DIR = _ROOT / "data" / "cache"

# The tracked factor caches are COMMITTED INPUTS: loaders below read them and
# never fetch or write; tools/refresh_market_data.py is the only writer.
# ("global" was removed with its file: Ken French ceased daily Global 5-factor
# publication in June 2019, before this portfolio's inception, so the gated
# regression that once used it could never return data for this book — the
# entry survived only as a dormant fetch-and-write path.)
_FACTOR_CONFIG: dict[str, dict] = {
    "us": {
        "url":   _BASE_URL + "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip",
        "cache": _CACHE_DIR / "ff_factors_us.csv",
    },
    "developed_exus": {
        "url":   _BASE_URL + "Developed_ex_US_5_Factors_Daily_CSV.zip",
        "cache": _CACHE_DIR / "ff_factors_developed_exus.csv",
    },
}

_UMD_URL   = _BASE_URL + "F-F_Momentum_Factor_daily_CSV.zip"
_UMD_CACHE = _CACHE_DIR / "ff_umd_us.csv"

# What one refresh of the momentum file measured: the evidence the vintage note cites
# for the ORDER of its two causes (#170, #172). The August 2026 refresh brought both a
# longer sample and a source revision of most of the daily history, and a same-window
# comparison of the two vintages showed which moved the rendered loadings. These are
# dated measurements of that refresh, not figures any code computes, so they are held
# as one record with its date and the commit that made and described the measurement,
# and the note reads them from here (#526). A later refresh does not update this: it
# would be a new measurement, with its own date and commit.
UMD_REFRESH_EVIDENCE = {
    "refresh": "2026-08",             # the refresh measured, as the note names it
    "measured_on": "2026-08-12",      # the day of the measurement
    "measured_in": "59caf70",         # the commit that made it and says how
    "extension_moved": "0.02–0.07",   # the added quarter moved the Mom loadings by
    "history_revised": "70%",         # share of daily history the source revised
    "history_from": "1926",           # how far back the revision reached
    "revision_moved_under": "0.001",  # and it moved the loadings by less than
}

# Ken French NYSE book-to-market (BE/ME) breakpoints — annual, formed each June,
# "every 5th percentile" (5, 10, …, 100). Used for the value-spread valuation
# signal (Phase 30). Separate ingestion path from the 5-factor daily files.
_BEME_URL          = _BASE_URL + "BE-ME_Breakpoints_CSV.zip"
_BEME_CACHE        = _CACHE_DIR / "ff_beme_breakpoints.csv"
_BEME_PCTILES      = list(range(5, 101, 5))  # 20 columns: p5, p10, …, p100
_BEME_REFRESH_DAYS = 30  # annual data; refetch monthly at most

_CACHE_PATH = _FACTOR_CONFIG["us"]["cache"]  # backward-compatible alias

_FF_RETRY_DELAYS = (1, 3, 9)  # seconds between Ken French download retry attempts (tool-only)

_FF5_FACTORS     = ["Mkt-RF", "SMB", "HML", "RMW", "CMA"]
_FF5_MOM_FACTORS = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom"]
_BENCH_FACTORS   = ["Bench-RF", "HML", "SMB", "RMW"]
_FI_FACTORS      = ["TERM", "CREDIT"]

# ── The regressions' sleeve weights: the SAA targets (#525) ──────────────────
#
# The US equity sleeve's return series is its four funds' returns, and the
# fixed-income sleeve's its two funds', each fund weighted by its own sleeve's SAA
# target, normalized over the funds. The weights were fixed in code (_SAA_US 17/15/9/8,
# _FI_WEIGHTS 60/40), equal to the demo book's targets and held equal by two tests;
# they read the targets now, through src/policy.py: the lock's inside a quarter lock
# that holds the book's policy, so a target changed after a quarter locked does not
# move its loadings, and the book's own outside one.
_US_FUNDS = ("VOO", "SPHQ", "VTV", "AVUV")
_FI_FUNDS = ("VGIT", "SCHP")


def target_weights(funds) -> "dict[str, float]":
    """{fund: weight} for ``funds``, in that order: each fund's sleeve's SAA target over
    the sum of their sleeves' targets.

    Raises ValueError when a fund has no sleeve with a positive target, or two funds
    share a sleeve, whose one target would then have no rule to split it."""
    from src import policy
    sleeve_of = policy.sleeve_of_ticker()
    targets = {r["name"]: r["target_weight"] for r in policy.sleeves()}
    sleeves = []
    for fund in funds:
        sleeve = sleeve_of.get(fund)
        if sleeve not in targets or not targets[sleeve] > 0:
            raise ValueError(f"{fund} has no sleeve with a positive SAA target ({sleeve!r}), "
                             f"so its regression weight cannot be read from the targets")
        sleeves.append(sleeve)
    if len(set(sleeves)) != len(sleeves):
        raise ValueError(f"two of {list(funds)} share a sleeve ({sleeves}): its target has "
                         f"no rule to split it between them")
    total = sum(targets[s] for s in sleeves)
    return {fund: targets[s] / total for fund, s in zip(funds, sleeves)}


def us_weights() -> "dict[str, float]":
    """The US equity sleeve regression's weights: VOO, SPHQ, VTV and AVUV at their
    sleeves' targets."""
    return target_weights(_US_FUNDS)


def fi_weights() -> "dict[str, float]":
    """The fixed-income sleeve regression's weights: VGIT and SCHP at their sleeves'
    targets (Core Fixed Income and TIPS)."""
    return target_weights(_FI_FUNDS)


# ── Equity sleeve definitions ──────────────────────────────────────────────────


def _intl_core_label() -> str:
    """Name of the cap-weighted developed-international (VEA) sleeve in this book.

    'International Developed' on the personal 9-sleeve book, 'International Core'
    on the demo 12-sleeve book — this VEA-only regression is exactly that sleeve
    either way. Derived from the DB so the factor-exhibit labels stay truthful in
    both modes instead of hardcoding one taxonomy's name.
    """
    from src.sleeve_config import international_sleeves
    intl = international_sleeves()
    for name in intl:
        if name in ("International Core", "International Developed"):
            return name
    return intl[0] if intl else "International Developed"


_SLEEVES = {
    "us": {
        "label":   "US Equity Sleeve",
        "tickers": ["VOO", "VTV", "SPHQ", "AVUV"],
        "region":  "us",
        # The funds are weighted by their sleeves' SAA targets, read when the
        # regression runs (us_weights, #525). A target change reaches the weights with
        # no code change; a fund added to or removed from the sleeve is a change to
        # _US_FUNDS. Assumption: buy-and-hold from a single-day initialization keeps
        # actual weights close to the targets for the early years, and the regression
        # measures daily return structure, not cumulative wealth attribution.
        "funds": _US_FUNDS,
    },
    "developed_exus": {
        # Phase 39: this regression has always been VEA-only, which is the
        # cap-weighted developed-international sleeve — International Developed on
        # the personal book, International Core on the demo book. No static label:
        # the name is DB-derived and resolved at CALL time via _sleeve_label(), so
        # importing this module never opens the database and a warm process picks
        # up the phase-46 rename on its next call instead of freezing the old name.
        "tickers": ["VEA"],
        "region":  "developed_exus",
        # Single-ticker sleeve: no weights needed.
    },
}


def _sleeve_label(spec: dict) -> str:
    """A sleeve's display label, resolved at call time.

    The developed_exus entry deliberately carries no static label — its sleeve
    name comes from the DB ('International Developed' on the personal 9-sleeve
    book, 'International Core' post-split) and must never be frozen at import:
    a module-level constant computed before the phase-46 migration applies
    would keep labelling exhibits with the pre-split name for the life of the
    process."""
    return spec.get("label") or f"{_intl_core_label()} Sleeve"

# ── International tilt-sleeve regressions (Phase 40) ────────────────────────────
#
# The tilted (demo) book splits developed-international into four sleeves. VEA
# (International Core) is regressed above via _SLEEVES; this adds the three TILT
# sleeves — IDHQ (Quality), AVIV (Large Value), AVDV (Small Value) — each against
# the SAME Developed ex-US FF5 factor set, exactly like VEA, and each paired with
# a passive CONTROL fund so the residual can be read against an investable
# yardstick instead of the (un-investable) academic factors.
#
# CONTROL ROSTER — Canada-matched, deliberately NOT the SAA benchmarks.
# The Large/Small Value SAA benchmarks (EFV, SCZ) both track MSCI EAFE, which
# excludes BOTH Canada and Korea. The Ken French Developed ex-US factor universe
# excludes Korea but INCLUDES Canada, and the tilt funds hold Canada (~11-13%).
# Over the 2025-05 -> 2026-05 window Canada (EWC, +37.8%/yr) beat developed-ex-US
# (+27.7%/yr) by ~10 pts, so a Canada-holed control's residual is understated by
# ~90-250 bps (measured directly: EFV residual +23 bps vs the Canada-matched IVLU
# +267 bps, same large-value style, same window). Presenting EFV/SCZ as clean
# gauges would smuggle the missing-Canada return into the fund-vs-control gap.
# IQLT / IVLU / ISVL all match the factor universe on BOTH counts — Canada in,
# Korea out — so a fund-minus-control residual is Canada- and Korea-neutral by
# construction:
#   IDHQ (Quality)     <- IQLT  MSCI World ex USA Sector-Neutral Quality
#   AVIV (Large Value) <- IVLU  MSCI World ex USA Enhanced Value
#   AVDV (Small Value) <- ISVL  FTSE Developed ex US ex Korea Small Cap Focused Value
# ISVL + IQLT alone do NOT cover the roster: they match Quality and Small Value
# but leave AVIV (Large Value) with no style-matched Canada-matched comparator,
# which is why IVLU is added. Controls are NOT held securities and intentionally
# carry no `securities` row (one would make sleeve_holdings() treat them as
# holdings); they are priced on demand via get_prices, exactly like VEA.
#
# N-PORT as-of: the affirmative "no Korea position" clearance for the two Avantis
# funds is an OBSERVED snapshot from a public holdings filing, not a mandate
# constraint. The text says it is the latest filing READ here, not the latest filed:
# it called this "the most recent" after the June 30, 2026 filings were out (audit
# item 9). Refresh the date when a newer Avantis N-PORT has been read.
_NPORT_ASOF = "March 31, 2026"

# The two classification claims the developed sleeve's methodology note makes, each as
# its owner's own page gave it when read on October 4, 2026 (#478 I02, #481): FTSE's
# list classes South Korea and Israel as Developed, and French's country table for the
# Developed and Developed ex US factors lists neither. Held here so the note cites
# what was read, and a later reading updates one place. build_factor_prose makes the
# Korea half of the claim, and its comment records the same two pages.
_FTSE_CLASSIFICATION = ('"Markets classified under the FTSE Equity Country '
                        'Classification Scheme", as of April 7, 2026')
_FRENCH_COUNTRY_TABLE = ('the country table in his "Description of Fama/French 5 Factors '
                         'for Developed Markets", read October 4, 2026')

# The South Korea classification claims IDHQ's note makes, each cited to its owner's own
# dated document (#614, decided by the owner on October 16, 2026), as read that day:
#   * S&P's Quality Indices methodology (March 2025) draws the S&P Quality Developed
#     Ex-U.S. LargeMidCap Index, IDHQ's, from the S&P Developed Ex-U.S. LargeMidCap Index,
#     and takes the top quintile by quality score, with no rule per country;
#   * S&P DJI's 2026 consultation (July 14, 2026) lists South Korea under "S&P DJI
#     Developed" in Appendix B, with country weights as of May 15, 2026;
#   * French's emerging-markets page lists South Korea among the emerging markets;
#   * MSCI's factsheets as of September 30, 2026: the Emerging Markets Index's lists Korea
#     among its 24 EM countries, and the IQLT control's index's lists its 22 DM countries,
#     Canada among them and Korea not.
# The note gave the S&P screen's Korea weight as near 9.5% in the first half of 2026 and
# roughly 2% after the June reconstitution. No S&P document for those figures was found,
# so they went, and the note no longer says how much of the residual Korea explains.
_SP_QUALITY_METHODOLOGY = 'S&P\'s "S&P Quality Indices Methodology", March 2025'
_SP_CLASSIFICATION = ('Appendix B of S&P Dow Jones Indices\' "2026 Equity Country '
                      'Classification Consultation – Extended", July 14, 2026, with country '
                      'weights as of May 15, 2026')
_FRENCH_EMERGING_LIST = ('his "Description of Fama/French 5 Factors for Emerging Markets", '
                         'read October 16, 2026')
_MSCI_EM_FACTSHEET = 'MSCI\'s factsheet for the MSCI Emerging Markets Index, as of September 30, 2026'
_MSCI_CONTROL_FACTSHEET = 'MSCI\'s factsheet for that index, as of September 30, 2026'

# The universe claims the controls note and the residual-reading note make (#623, decided by
# the owner on October 16, 2026), each read that day from its owner's dated document:
#   * MSCI's EAFE factsheet (as of September 30, 2026): 21 Developed Markets countries,
#     "excluding the US and Canada";
#   * MSCI's factsheet for IVLU's index, MSCI World ex USA Enhanced Value (as of September
#     30, 2026): 22 DM countries, Canada among them and Korea not, as for IQLT's index;
#   * FTSE's Focused Value ground rules (v2.4, August 2026) draw ISVL's index from the FTSE
#     Developed ex US ex Korea Small Cap Index, and FTSE's classification as at April 7, 2026
#     lists Canada as Developed;
#   * French's developed-markets country table lists Canada, and neither Korea nor Israel.
# The controls note gave the tilt funds' Canada weight as ~11-13%. No fund factsheet giving
# it was found, so it went, and with it the claim that the tilt funds hold Canada.
_MSCI_EAFE_FACTSHEET = "MSCI's factsheet for the MSCI EAFE Index, as of September 30, 2026"
_MSCI_CONTROLS_FACTSHEETS = ("MSCI's factsheets for the MSCI World ex USA Sector Neutral "
                             "Quality and MSCI World ex USA Enhanced Value indexes, both as of "
                             "September 30, 2026")
_FTSE_FOCUSED_VALUE_RULES = ('FTSE Russell\'s "FTSE Focused Value Index Series" ground rules, '
                             'v2.4, August 2026')

_INTL_TILT_SLEEVES = [
    {
        "sleeve":        "International Quality",
        "fund":          "IDHQ",
        "fund_name":     "Invesco S&P International Developed Quality ETF",
        "control":       "IQLT",
        "control_name":  "iShares MSCI Intl Quality Factor ETF",
        "control_index": "MSCI World ex USA Sector-Neutral Quality",
        "disclosure":    "idhq",
    },
    {
        "sleeve":        "International Large Value",
        "fund":          "AVIV",
        "fund_name":     "Avantis International Large Cap Value ETF",
        "control":       "IVLU",
        "control_name":  "iShares MSCI Intl Value Factor ETF",
        "control_index": "MSCI World ex USA Enhanced Value",
        "disclosure":    "avantis",
        "israel_pct":    "1",
    },
    {
        "sleeve":        "International Small Value",
        "fund":          "AVDV",
        "fund_name":     "Avantis International Small Cap Value ETF",
        "control":       "ISVL",
        "control_name":  "iShares International Developed Small Cap Value Factor ETF",
        "control_index": "FTSE Developed ex US ex Korea Small Cap Focused Value",
        "disclosure":    "avantis",
        "israel_pct":    "4.5",
    },
]


# Qualitative disclosure for the EM sleeve (no daily FF5 data available)
def em_disclosure() -> str:
    """EM-sleeve disclosure for the book this process is pointed at.

    Personal (single cap-weighted developed sleeve): the original framing —
    IEMG's composition plus the no-daily-EM-factor-data point.
    Demo (tilted international book): the SAA page's cap-weight-exception
    framing — the developed tilts are verifiable against Ken French's daily
    developed ex-US series, EM has no equivalent series, so the sleeve stays
    cap-weighted and carries no per-sleeve regression by design.
    Resolved at CALL time (never at import — importing this module must not
    touch the database, and a frozen value would go stale across the phase-46
    split); falls back to the untilted framing if the DB is unavailable (it is
    factually safe in either book).
    """
    try:
        from src.sleeve_config import international_sleeves
        tilted = len(international_sleeves()) > 1
    except Exception:
        tilted = False
    if not tilted:
        # No typed figures (#478 I05). This named "~27% China weight", "approximately
        # 12 observations over the current 1-year window" and "3+ years of monthly
        # data": an undated index weight, and two counts nothing in the code computes.
        # The only threshold here is the daily regressions' 30-observation minimum,
        # which is not a monthly one, so the counts are dropped, not re-derived.
        return (
            "Ken French does not publish daily EM factor data, and the monthly history "
            "since this portfolio's inception is too short for stable inference. "
            "IEMG provides passive cap-weighted broad EM exposure. Factor decomposition "
            "for this sleeve will be added when the portfolio has accumulated enough "
            "monthly history."
        )
    return (
        "This is the one equity region held at cap weight, and the only equity sleeve "
        "with no per-sleeve factor regression, by design. The developed "
        "book tilts toward quality, value, and small value because those exposures can "
        "be verified against Ken French's daily developed ex-US factor series. No "
        "equivalent daily series exists for emerging markets, and the monthly history "
        "is too short against this portfolio's inception for stable inference. A tilt "
        "here could be asserted but not shown, so the sleeve stays passive: IEMG "
        "provides cap-weighted broad EM exposure. The full cap-weight-exception "
        "argument is in the SAA sleeve rationale."
    )


def committed_factor_files() -> "dict[str, str]":
    """{what the file holds: its path from the repository root} for the three committed
    Ken French files, read from the paths the loaders read (#527)."""
    def rel(path: Path) -> str:
        try:
            return path.relative_to(_ROOT).as_posix()
        except ValueError:      # a path outside the repository is named whole
            return path.as_posix()

    return {"US factors": rel(_FACTOR_CONFIG["us"]["cache"]),
            "Developed ex-US": rel(_FACTOR_CONFIG["developed_exus"]["cache"]),
            "Momentum (UMD)": rel(_UMD_CACHE)}


def factor_data_caption(demo: "bool | None" = None) -> str:
    """The Factor Profile page's data caption: the three committed files where the
    loaders read them, that they have no TTL, and their only writer (#527). It named
    data/ff_factors_us.csv and two more paths under data/ that hold no committed file;
    the committed files are under data/cache/.

    The writer is named by its path outside the public demo only. A visitor there can
    do nothing with a maintainer's tool, and the demo's pages print no such path
    (asof.staleness_note; tests/render/test_market_data_wording.py). ``demo`` defaults
    to the running mode."""
    if demo is None:
        from src.config import IS_DEMO as demo
    listed = "; ".join(f"{what} at {path}" for what, path in committed_factor_files().items())
    writer = ("A maintainer's refresh tool" if demo else "tools/refresh_market_data.py")
    return ("Data: Ken French Data Library, Dartmouth (mba.tuck.dartmouth.edu). "
            f"Committed files with no TTL: {listed}. {writer} is "
            "their only writer, and nothing fetches them on read.")


# ── Utilities ─────────────────────────────────────────────────────────────────

def _nw_lags(T: int) -> int:
    """Newey-West HAC lag length: floor(4 * (T/100)^(2/9))."""
    return int(4 * (T / 100) ** (2 / 9))


# The significance legend under every regression table. Escaped: a caption starting
# "* p < 0.10" rendered the star as a markdown bullet, and "** p < 0.05 ... ***"
# paired the stars into bold, so the legend lost its first star (audit item 13).
SIG_LEGEND = r"\* p < 0.10 &nbsp; \*\* p < 0.05 &nbsp; \*\*\* p < 0.01"


def fmt_p(p: float) -> str:
    """A p-value to three places, unless rounding would carry it onto a threshold its
    stars say it is below: p = 0.0099 printed "0.010" beside *** (audit item 13)."""
    for threshold in (0.01, 0.05, 0.10):
        if p < threshold and round(p, 3) >= threshold:
            return f"<{threshold:.2f}"
    return f"{p:.3f}"


def sig_marker(p: float) -> str:
    """Return significance asterisk(s) at 10/5/1% levels."""
    if p < 0.01:
        return "***"
    if p < 0.05:
        return "**"
    if p < 0.10:
        return "*"
    return ""


def alpha_interval_bps(result: dict) -> "tuple[float, float] | None":
    """
    The annualized alpha's 95% Newey-West confidence interval, (low, high) in bps per
    year, or None when the regression has no standard error for its constant.

    THE ONE RULE for every page and the report (#525): the interval is the alpha
    ± 1.96 standard errors, where SE is result['_hac_bse']['const'] (daily decimal
    units) annualized over 252 trading days: SE_bps = SE_daily × 252 × 10000. The
    report's benchmark prose computed it a second time, so a change to one copy would
    have left a page and the PDF printing different intervals for one regression.
    The interval applies to alpha only; betas are not bounded here.
    """
    se_daily = result.get("_hac_bse", {}).get("const", float("nan"))
    if se_daily != se_daily:  # NaN check
        return None
    a_bps  = result["alpha_annual_bps"]
    se_bps = se_daily * 252 * 10_000
    return a_bps - 1.96 * se_bps, a_bps + 1.96 * se_bps


def interval_end_str(bps: float) -> str:
    """One end of an alpha interval in whole bps, with one minus sign (item 13): "-53",
    "+307"."""
    return f"-{abs(bps):.0f}" if bps < 0 else f"+{bps:.0f}"


def alpha_ci_str(result: dict) -> str:
    """
    Format annualized alpha with its 95% Newey-West confidence interval
    (alpha_interval_bps). Returns e.g. '+127 bps/yr [-53, +307]', or the alpha alone
    when the regression has no standard error for its constant.
    """
    a_bps = result["alpha_annual_bps"]
    interval = alpha_interval_bps(result)
    if interval is None:
        return f"{a_bps:+.0f} bps/yr"
    lo, hi = interval
    return f"{a_bps:+.0f} bps/yr [{interval_end_str(lo)}, {interval_end_str(hi)}]"


def _fmt_date(iso: str) -> str:
    """'2025-05-01' → 'May 1, 2025'."""
    d = date.fromisoformat(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


# ── Factor data ingestion ──────────────────────────────────────────────────────

def _parse_ff_csv_text(text: str) -> pd.DataFrame:
    """
    Parse the raw text of a Ken French daily factor CSV file.

    The file is comma-delimited.  Format::

        <descriptive header lines>
        ,Mkt-RF,SMB,HML,RMW,CMA,RF          ← column header (leading comma)
        19630701,   -0.67,    0.00, ...       ← data rows; date may have trailing spaces
        ...
        <annual averages footer with 4-digit year as first field>

    Two regional variants are handled:
      - US file: date field is clean (e.g. "19630701,")
      - Developed ex-US file: date field has trailing spaces ("19900702    ,")
      Both are handled by strip() on the first comma-separated token.

    Missing-data sentinel -99.99 is replaced with NaN.
    All factor columns are divided by 100 to convert percent to decimal.
    """
    raw_data_lines: list[str] = []
    in_data = False

    for line in text.splitlines():
        first_field = line.split(",")[0].strip()
        if len(first_field) == 8 and first_field.isdigit():
            in_data = True
            raw_data_lines.append(line)
        elif in_data:
            if len(first_field) == 4 and first_field.isdigit():
                break  # annual-averages footer

    if not raw_data_lines:
        raise ValueError("No daily factor data rows found in Ken French CSV text")

    csv_block = "date,Mkt-RF,SMB,HML,RMW,CMA,RF\n" + "\n".join(raw_data_lines)
    df = pd.read_csv(io.StringIO(csv_block))
    df["date"] = pd.to_datetime(df["date"].astype(str).str.strip(), format="%Y%m%d")
    df = df.set_index("date")

    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce") / 100.0

    # Replace missing-data sentinel (-99.99 in percent → approx -0.9999 in decimal).
    # Use a threshold rather than exact equality to avoid float precision issues.
    # No legitimate daily factor return is below -99%.
    df = df.where(df > -0.99, float("nan"))

    return df


def _fetch_factors(url: str) -> pd.DataFrame:
    """Download and parse a Ken French daily factor ZIP from Dartmouth, with retry."""
    last_exc: Exception = RuntimeError("no attempts made")
    delays = list(_FF_RETRY_DELAYS)
    for attempt in range(len(delays) + 1):
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                csv_name = next(n for n in zf.namelist() if n.upper().endswith(".CSV"))
                raw_text = zf.read(csv_name).decode("utf-8", errors="replace")
            return _parse_ff_csv_text(raw_text)
        except Exception as exc:
            last_exc = exc
            print(
                f"[INFO] Ken French fetch attempt {attempt + 1}/{len(delays) + 1} "
                f"for '{url}' failed: {exc}",
                file=sys.stderr,
            )
            if attempt < len(delays):
                time.sleep(delays[attempt])
    raise RuntimeError(
        f"Ken French factor download failed after {len(delays) + 1} attempts "
        f"for '{url}': {last_exc}"
    )


def load_factors(region: str) -> pd.DataFrame:
    """
    Return Ken French daily 5-factor data for the specified region.

    region: 'us' | 'developed_exus'

    Reads the COMMITTED cache only — never fetches, never writes. The tracked
    factor files are inputs, refreshed exclusively by
    tools/refresh_market_data.py as a deliberate, committed step: the old
    auto-refresh here rewrote tracked files from test runs and page renders,
    which made a clean working tree unverifiable, and its failure path
    discarded a successfully fetched frame whenever only the write was
    blocked. Staleness is surfaced to the reader (factor_frontier() +
    asof.staleness_note), not silently "fixed" at read time.
    """
    if region not in _FACTOR_CONFIG:
        raise ValueError(
            f"Unknown factor region '{region}'. "
            f"Valid regions: {list(_FACTOR_CONFIG)}"
        )
    # A quarter lock serves the rows it locked (#382), and never the file.
    from src.input_lock import FF5_DEVELOPED_EXUS, FF5_US, locked
    held = locked(FF5_US if region == "us" else FF5_DEVELOPED_EXUS)
    if held is not None:
        return held.copy()
    cache: Path = _FACTOR_CONFIG[region]["cache"]
    if not cache.exists():
        raise FileNotFoundError(
            f"Committed factor data missing: {cache}. Restore it from git, or "
            "regenerate it with tools/refresh_market_data.py."
        )
    return pd.read_csv(cache, index_col=0, parse_dates=True)


def factor_frontier(region: str) -> Optional[date]:
    """Last committed factor date for a region — the DATA frontier, never file
    mtime (a checkout resets mtime, so mtime says nothing about content age).
    None when the file is missing or unreadable."""
    try:
        df = load_factors(region)
        return df.index[-1].date() if len(df) else None
    except Exception:
        return None


# ── Momentum (UMD) factor data ────────────────────────────────────────────────

def _parse_momentum_csv_text(text: str) -> pd.Series:
    """
    Parse the raw text of a Ken French daily momentum factor CSV.
    Returns the Mom factor as a decimal Series (values divided by 100).
    Missing-data sentinel -99.99 is replaced with NaN.
    """
    raw_data_lines: list[str] = []
    in_data = False

    for line in text.splitlines():
        first_field = line.split(",")[0].strip()
        if len(first_field) == 8 and first_field.isdigit():
            in_data = True
            raw_data_lines.append(line)
        elif in_data:
            if len(first_field) == 4 and first_field.isdigit():
                break

    if not raw_data_lines:
        raise ValueError("No daily momentum data rows found in Ken French CSV text")

    rows = []
    for line in raw_data_lines:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            dt  = pd.to_datetime(parts[0].strip(), format="%Y%m%d")
            val = float(parts[1]) / 100.0
        except (ValueError, IndexError):
            continue
        if val <= -0.99:
            val = float("nan")
        rows.append({"date": dt, "Mom": val})

    if not rows:
        raise ValueError("No valid momentum rows after parsing")

    df = pd.DataFrame(rows).set_index("date")
    return df["Mom"].dropna()


def _fetch_umd() -> pd.Series:
    """Download and parse the Ken French daily momentum factor. Tool-only:
    returns the parsed series and never writes — tools/refresh_market_data.py
    owns the write and reports fetch and write outcomes separately."""
    resp = requests.get(_UMD_URL, timeout=30)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        csv_name = next(n for n in zf.namelist() if n.upper().endswith(".CSV"))
        raw_text = zf.read(csv_name).decode("utf-8", errors="replace")
    return _parse_momentum_csv_text(raw_text)


def hyg_credit_series(inception: str, end_date: str) -> pd.Series:
    """The HYG total-return series regress_fi_sleeve's CREDIT proxy reads: the price
    layer, as for every other ETF and as the Risk page's credit factor already read it.

    Until #386 it read data/cache/prices_hyg.parquet, a committed file that ended
    2026-05-05 while nothing refreshed it, so Q2 2026's regression held HYG flat for
    the quarter's last eight weeks. A quarter lock captures this series, cut at the
    quarter's end and gated on coverage like the French data (src.cache)."""
    p = get_prices("HYG", inception, end_date)
    p.index = pd.to_datetime(p.index)
    return total_return_series(p)


def hyg_credit_series_locked() -> "pd.Series | None":
    """The locked HYG series inside a quarter lock, else None (read as usual)."""
    from src.input_lock import HYG, locked
    held = locked(HYG)
    return held.copy() if held is not None else None


def load_umd_factor() -> pd.Series:
    """Return Ken French daily Momentum (UMD / Mom) factor as a decimal Series.

    Reads the COMMITTED cache only — same policy as load_factors: refresh is
    tools/refresh_market_data.py's job, never the loader's."""
    from src.input_lock import UMD, locked
    held = locked(UMD)
    if held is not None:
        return held.copy()
    if not _UMD_CACHE.exists():
        raise FileNotFoundError(
            f"Committed momentum data missing: {_UMD_CACHE}. Restore it from "
            "git, or regenerate it with tools/refresh_market_data.py."
        )
    return pd.read_csv(_UMD_CACHE, index_col=0, parse_dates=True).squeeze("columns")


def umd_frontier() -> Optional[date]:
    """Last committed momentum-factor date (data frontier, never mtime)."""
    try:
        s = load_umd_factor()
        return s.index[-1].date() if len(s) else None
    except Exception:
        return None


# ── BE/ME breakpoints (value-spread valuation, Phase 30) ───────────────────────
#
# This is an ADDITIVE Ken French ingestion path. It reuses the shared download
# constants (_BASE_URL, _CACHE_DIR, _FF_RETRY_DELAYS) and the same requests+zip
# mechanism, but has its own parser — the 5-factor parser (_parse_ff_csv_text)
# and loader (load_factors) are deliberately left untouched.

def _parse_beme_breakpoints_text(text: str) -> pd.DataFrame:
    """
    Parse the raw text of the Ken French BE/ME breakpoints CSV.

    File format (confirmed against the live file)::

        This file was created using the … CRSP database.  It contains every 5th
        NYSE BEME percentile.
        <blank>
          ,<= 0,>0                                   ← partial header (count cols)
        1926,   3,  429,  0.296, 0.404, …, 31.324     ← year, n(BE<=0), n(BE>0),
        …                                                then 20 breakpoints
        Copyright …                                   ← footer

    Each data row is a 4-digit formation year followed by two firm-count columns
    (number of firms with BE <= 0 and BE > 0) and 20 book-to-market breakpoints
    at the 5th, 10th, …, 100th NYSE percentiles. Breakpoints are formed each June;
    rows are indexed at June 30 of the formation year.

    Returns a DataFrame indexed by June-30 Timestamp with columns p5, p10, …, p100
    (book-to-market ratio at each percentile). Low percentiles are growth/expensive
    (low B/M); high percentiles are value/cheap (high B/M).
    """
    n_break = len(_BEME_PCTILES)
    rows: list[tuple[int, list[float]]] = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        first = parts[0] if parts else ""
        # Data rows: 4-digit year + 2 count cols + the 20 breakpoint columns.
        if len(first) == 4 and first.isdigit() and len(parts) >= 3 + n_break:
            try:
                year = int(first)
                vals = [float(x) for x in parts[3:3 + n_break]]
            except ValueError:
                continue
            rows.append((year, vals))

    if not rows:
        raise ValueError("No BE/ME breakpoint data rows found in Ken French CSV text")

    idx = pd.DatetimeIndex([pd.Timestamp(year=y, month=6, day=30) for y, _ in rows])
    data = {f"p{p}": [vals[i] for _, vals in rows] for i, p in enumerate(_BEME_PCTILES)}
    return pd.DataFrame(data, index=idx).sort_index()


def _fetch_beme_breakpoints() -> pd.DataFrame:
    """Download and parse the Ken French BE/ME breakpoints ZIP, with retry."""
    last_exc: Exception = RuntimeError("no attempts made")
    delays = list(_FF_RETRY_DELAYS)
    for attempt in range(len(delays) + 1):
        try:
            resp = requests.get(_BEME_URL, timeout=30)
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                csv_name = next(n for n in zf.namelist() if n.upper().endswith(".CSV"))
                raw_text = zf.read(csv_name).decode("utf-8", errors="replace")
            return _parse_beme_breakpoints_text(raw_text)
        except Exception as exc:
            last_exc = exc
            print(
                f"[INFO] Ken French BE/ME fetch attempt {attempt + 1}/{len(delays) + 1} "
                f"failed: {exc}",
                file=sys.stderr,
            )
            if attempt < len(delays):
                time.sleep(delays[attempt])
    raise RuntimeError(
        f"Ken French BE/ME breakpoint download failed after {len(delays) + 1} attempts: "
        f"{last_exc}"
    )


def load_beme_breakpoints(force_refresh: bool = False) -> pd.DataFrame:
    """
    Return Ken French NYSE BE/ME breakpoints (annual, June-formed).

    DataFrame indexed by June-30 Timestamp with columns p5…p100 (book-to-market
    ratio at each NYSE percentile). Refreshes the local cache when stale (file
    absent or mtime older than _BEME_REFRESH_DAYS). On fetch failure a stale
    cache is returned with a warning rather than crashing.
    """
    cache = _BEME_CACHE
    stale = True
    if cache.exists():
        mtime = date.fromtimestamp(cache.stat().st_mtime)
        stale = (date.today() - mtime).days > _BEME_REFRESH_DAYS

    if force_refresh or stale:
        try:
            df = _fetch_beme_breakpoints()
            cache.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(cache)
            return df
        except Exception as exc:
            if cache.exists():
                warnings.warn(
                    f"BE/ME breakpoint refresh failed ({exc}); using cached data.",
                    stacklevel=2,
                )
            else:
                raise

    return pd.read_csv(cache, index_col=0, parse_dates=True)


# ── Sleeve return series ───────────────────────────────────────────────────────

def _get_sleeve_return_series(
    tickers: list[str],
    inception: str,
    end_date: str,
    weights: Optional[dict[str, float]] = None,
) -> pd.Series:
    """
    Daily total-return series for an equity sleeve from inception through end_date.

    Single-ticker sleeves (VEA): adj_close pct_change — no weights needed.

    Multi-ticker sleeves (US equity): value-weighted daily return.
      When ``weights`` is supplied (SAA target proportions from _SLEEVES config),
      they are used directly and no database access is required.
      When ``weights`` is None, inception market values are computed from
      get_holdings_on_date — this fallback requires trades to exist in the DB.
      A date on which a leg has no return (before its first close in the window)
      is NaN, not 0% (#639): a missing stretch filled with 0% pulled the betas toward
      zero. The regression's dropna then keeps the dates every leg covers, and its
      sample window, printed from those dates, says where it starts.
      A leg whose price read fails has no return on any date, by the same path, and
      keeps its weight: no leg is dropped and no weight renormalized, so the sleeve
      has no date every leg covers and its regression is not computed (#647, decided
      by the owner on October 17, 2026). Only the read's own failure is caught (the
      network, Yahoo's answer, a read-only store: prices.gap_fetch_cause); a code
      error raises. Each such leg and its cause are returned in ``attrs["unread"]``,
      {ticker: cause}, for the regression to say why.

    Non-trading days (weekends, US holidays) carry forward Friday's price;
    inner-joining against the factor index in the regression step drops them.
    """
    date_range = pd.date_range(start=inception, end=end_date, freq="D")

    if len(tickers) == 1:
        ticker = tickers[0]
        p = get_prices(ticker, inception, end_date)
        p.index = pd.to_datetime(p.index)
        series = total_return_series(p)
        return series.reindex(date_range).ffill().pct_change().iloc[1:]

    # Multi-ticker: use provided weights or compute from inception market values
    if weights is not None:
        effective_weights = weights
    else:
        holdings = get_holdings_on_date(inception, account_id=get_portfolio_account_id())
        sleeve_values: dict[str, float] = {}
        for ticker in tickers:
            if ticker not in holdings.index:
                continue
            shares = float(holdings.loc[ticker, "net_shares"])
            try:
                p_inc = get_prices(ticker, inception, inception)
                p_inc.index = pd.to_datetime(p_inc.index)
                px = total_return_series(p_inc)
                price = float(px.dropna().iloc[-1]) if not px.dropna().empty else 0.0
                if price > 0:
                    sleeve_values[ticker] = shares * price
            except Exception:
                continue
        if not sleeve_values:
            raise ValueError(
                f"Could not compute inception market values for tickers: {tickers}"
            )
        total = sum(sleeve_values.values())
        effective_weights = {t: v / total for t, v in sleeve_values.items()}

    weighted_ret = pd.Series(0.0, index=date_range)
    unread: dict[str, str] = {}
    for ticker, w in effective_weights.items():
        try:
            p = get_prices(ticker, inception, end_date)
        except Exception as exc:                                 # noqa: BLE001
            cause = gap_fetch_cause(exc)
            if cause is None:
                raise
            unread[ticker] = cause
            weighted_ret = weighted_ret + w * pd.Series(np.nan, index=date_range)
            continue
        p.index = pd.to_datetime(p.index)
        series = total_return_series(p)
        daily_ret = series.reindex(date_range).ffill().pct_change()
        weighted_ret = weighted_ret + w * daily_ret

    out = weighted_ret.iloc[1:]  # drop inception day: no prior close, so no return
    out.attrs["unread"] = unread
    return out


def _unread_reason(unread: "dict[str, str]") -> str:
    """Each leg whose price read failed, with its cause, in words for the reader's
    not_computed_sentence (reports, #248): "the price read failed for VOO: Yahoo
    Finance returned HTTP 503"."""
    return "; ".join(f"the price read failed for {t}: {cause}" for t, cause in unread.items())


def _not_computed(sleeve_label: str, tickers: list[str], region: str,
                  unread: "dict[str, str]") -> dict:
    """A sleeve regression that is not computed because a leg's prices could not be
    read (#647): the sleeve's label and tickers, and ``not_computed``, the reason, which
    each surface prints in place of the loadings."""
    return {"sleeve_label": sleeve_label, "tickers": tickers, "region": region,
            "not_computed": _unread_reason(unread)}


def computed_result(res: Optional[dict]) -> Optional[dict]:
    """``res`` when it holds a regression's figures, else None: None for insufficient
    data, and for a regression not computed because a leg could not be read (#647)."""
    return None if res is None or res.get("not_computed") else res


# ── Regression engine ──────────────────────────────────────────────────────────

def _ols_ff5(R_excess: pd.Series, factors: pd.DataFrame) -> dict:
    """
    OLS regression with Newey-West HAC standard errors.

    R_excess : daily excess returns (R_sleeve - RF), decimal, DatetimeIndex
    factors  : DataFrame with columns Mkt-RF, SMB, HML, RMW, CMA, decimal

    Alpha is annualized as alpha_daily * 252 (linear daily-to-annual scaling).
    Both HAC and plain OLS standard errors are returned (HAC is authoritative;
    OLS is retained for the NW-vs-OLS diagnostic test).
    """
    T = len(R_excess)
    L = _nw_lags(T)

    X = add_constant(factors[_FF5_FACTORS])
    model = OLS(R_excess, X)
    res_hac = model.fit(cov_type="HAC", cov_kwds={"maxlags": L})
    res_ols = model.fit()

    params = res_hac.params
    tvals  = res_hac.tvalues
    pvals  = res_hac.pvalues

    alpha_daily  = float(params["const"])
    alpha_annual = alpha_daily * 252

    return {
        "alpha_daily":      alpha_daily,
        "alpha_annual":     alpha_annual,
        "alpha_annual_bps": alpha_annual * 10_000,
        "t_alpha":          float(tvals["const"]),
        "p_alpha":          float(pvals["const"]),
        "betas":   {f: float(params[f]) for f in _FF5_FACTORS},
        "t_stats": {f: float(tvals[f])  for f in _FF5_FACTORS},
        "p_values":{f: float(pvals[f])  for f in _FF5_FACTORS},
        "r_squared":     float(res_hac.rsquared),
        "adj_r_squared": float(res_hac.rsquared_adj),
        "T":      T,
        "nw_lags": L,
        "_ols_bse": res_ols.bse.to_dict(),
        "_hac_bse": res_hac.bse.to_dict(),
    }


def run_sleeve_regression(
    sleeve_label: str,
    tickers: list[str],
    region: str,
    inception: str,
    end_date: str,
    weights: Optional[dict[str, float]] = None,
) -> Optional[dict]:
    """
    Run FF5 regression for one equity sleeve against region-appropriate factors.

    The sleeve return series is aligned against the factor data via inner join,
    which naturally drops weekends, US holidays, and the factor publication lag
    window at the right edge of the sample.

    Returns None if fewer than 30 aligned observations are available, and a
    not-computed result naming each leg and its cause when a leg's prices could not be
    read (_not_computed, #647).
    """
    daily_ret = _get_sleeve_return_series(tickers, inception, end_date, weights=weights)
    unread = daily_ret.attrs.get("unread")
    daily_ret.index = pd.to_datetime(daily_ret.index)

    ff = load_factors(region)
    ff.index = pd.to_datetime(ff.index)

    # VEA trades on US exchanges and has no price information on US federal
    # holidays — its return series shows zero by forward-fill, not by
    # observation. The Dev FF calendar includes those dates (international
    # markets open); the US FF calendar excludes them. Filter to US trading
    # days so both sleeves use the same calendar and US-holiday rows with
    # artificially zeroed VEA returns are excluded.
    if region == "developed_exus":
        ff_us = load_factors("us")
        ff_us.index = pd.to_datetime(ff_us.index)
        us_trading_days = ff_us.loc[inception:end_date].index
        daily_ret = daily_ret.reindex(us_trading_days).dropna()

    merged = daily_ret.to_frame(name="R_sleeve").join(ff, how="inner").dropna()

    if len(merged) < 30:
        return _not_computed(sleeve_label, tickers, region, unread) if unread else None

    R_excess = merged["R_sleeve"] - merged["RF"]
    result = _ols_ff5(R_excess, merged)

    result["sleeve_label"]  = sleeve_label
    result["tickers"]       = tickers
    result["region"]        = region
    result["sample_start"]  = merged.index[0].date().isoformat()
    result["sample_end"]    = merged.index[-1].date().isoformat()
    result["exclusion_days"] = max(
        0, (pd.Timestamp(end_date) - pd.Timestamp(result["sample_end"])).days
    )

    return result


def run_sleeve_regressions(inception: str, end_date: str) -> dict:
    """
    Run FF5 regressions for the US equity and Developed ex-US equity sleeves.

    EM sleeve is excluded: Ken French does not publish daily EM factor data.
    Monthly EM factors would yield ~12 observations over a 1-year window,
    insufficient for stable inference.  See em_disclosure() for the qualitative
    note included in the PDF and Streamlit output.

    Returns a dict with keys 'us' and 'developed_exus'; each value is either
    a regression result dict or None if data is insufficient.
    """
    results: dict[str, Optional[dict]] = {}
    for key, spec in _SLEEVES.items():
        try:
            results[key] = run_sleeve_regression(
                sleeve_label=_sleeve_label(spec),
                tickers=spec["tickers"],
                region=spec["region"],
                inception=inception,
                end_date=end_date,
                weights=target_weights(spec["funds"]) if "funds" in spec else None,
            )
        except Exception:
            results[key] = None
    return results


# ── Carhart FF5+MOM (momentum supplement) ─────────────────────────────────────

def _ols_ff5_mom(R_excess: pd.Series, factors: pd.DataFrame) -> dict:
    """
    OLS regression with Newey-West HAC for the 6-factor Carhart model (FF5 + Mom).
    Returns the same structure as _ols_ff5 but with 'Mom' included in betas/t_stats/p_values.
    """
    T = len(R_excess)
    L = _nw_lags(T)

    X = add_constant(factors[_FF5_MOM_FACTORS])
    model   = OLS(R_excess, X)
    res_hac = model.fit(cov_type="HAC", cov_kwds={"maxlags": L})
    res_ols = model.fit()

    params = res_hac.params
    tvals  = res_hac.tvalues
    pvals  = res_hac.pvalues

    alpha_daily  = float(params["const"])
    alpha_annual = alpha_daily * 252

    return {
        "alpha_daily":      alpha_daily,
        "alpha_annual":     alpha_annual,
        "alpha_annual_bps": alpha_annual * 10_000,
        "t_alpha":          float(tvals["const"]),
        "p_alpha":          float(pvals["const"]),
        "betas":   {f: float(params[f]) for f in _FF5_MOM_FACTORS},
        "t_stats": {f: float(tvals[f])  for f in _FF5_MOM_FACTORS},
        "p_values":{f: float(pvals[f])  for f in _FF5_MOM_FACTORS},
        "r_squared":     float(res_hac.rsquared),
        "adj_r_squared": float(res_hac.rsquared_adj),
        "T":      T,
        "nw_lags": L,
        "_ols_bse": res_ols.bse.to_dict(),
        "_hac_bse": res_hac.bse.to_dict(),
    }


def run_sleeve_regressions_mom(inception: str, end_date: str) -> dict:
    """
    Run FF5+Momentum Carhart regressions for US equity and Developed ex-US equity sleeves.
    Uses the same sleeve definitions as run_sleeve_regressions but extends the factor set
    with the Ken French daily UMD/Mom factor.

    Returns dict with keys 'us' and 'developed_exus'; each value is a result dict or None,
    or a not-computed result when a leg's prices could not be read, as
    run_sleeve_regression (#647). Returns all-None values if the UMD factor data cannot
    be loaded.
    """
    try:
        umd = load_umd_factor()
        umd.index = pd.to_datetime(umd.index)
    except Exception:
        return {"us": None, "developed_exus": None}

    results: dict[str, Optional[dict]] = {}
    for key, spec in _SLEEVES.items():
        try:
            daily_ret = _get_sleeve_return_series(
                spec["tickers"], inception, end_date,
                weights=target_weights(spec["funds"]) if "funds" in spec else None,
            )
            unread = daily_ret.attrs.get("unread")
            daily_ret.index = pd.to_datetime(daily_ret.index)

            ff = load_factors(spec["region"])
            ff.index = pd.to_datetime(ff.index)

            if spec["region"] == "developed_exus":
                ff_us = load_factors("us")
                ff_us.index = pd.to_datetime(ff_us.index)
                us_trading_days = ff_us.loc[inception:end_date].index
                daily_ret = daily_ret.reindex(us_trading_days).dropna()

            merged = (
                daily_ret.to_frame(name="R_sleeve")
                .join(ff, how="inner")
                .join(umd.rename("Mom"), how="inner")
                .dropna()
            )

            if len(merged) < 30:
                results[key] = (_not_computed(_sleeve_label(spec), spec["tickers"],
                                              spec["region"], unread) if unread else None)
                continue

            R_excess = merged["R_sleeve"] - merged["RF"]
            result = _ols_ff5_mom(R_excess, merged)
            result["sleeve_label"]  = _sleeve_label(spec)
            result["tickers"]       = spec["tickers"]
            result["region"]        = spec["region"]
            result["sample_start"]  = merged.index[0].date().isoformat()
            result["sample_end"]    = merged.index[-1].date().isoformat()
            results[key] = result
        except Exception:
            results[key] = None

    return results


# run_intl_global_regression was removed along with the "global" factor file
# and config entry: Ken French ceased daily Global 5-factor publication in June
# 2019, before this portfolio's inception, so the cutoff gate returned None on
# every call — the function's only reachable behavior was a dormant
# fetch-and-write path that would have resurfaced if an inception date ever
# changed. The methodology note explaining the discontinuation (below) stays.


def run_intl_tilt_regressions(inception: str, end_date: str) -> dict:
    """Per-fund FF5 regressions for the international TILT sleeves + their controls.

    Runs only on the tilted (demo) book — the one that splits developed-
    international into Quality / Large Value / Small Value tilt sleeves. On the
    untilted (personal) book there is a single VEA sleeve, already regressed via
    run_sleeve_regressions, and this returns an empty dict.

    Each tilt fund (IDHQ, AVIV, AVDV) and its Canada-matched control (IQLT, IVLU,
    ISVL) is regressed against the Developed ex-US FF5 factors through the same
    run_sleeve_regression path VEA uses, so it inherits the US-trading-day
    reindex and the full result-key set. See _INTL_TILT_SLEEVES for the roster
    rationale (why the controls are Canada-matched and NOT the SAA benchmarks).

    Returns an ordered dict keyed by fund ticker; each value carries the fund and
    control result dicts (either may be None on insufficient data) plus the
    metadata the page needs to render and caption the pair.
    """
    try:
        from src.sleeve_config import international_sleeves
        book_sleeves = set(international_sleeves())
    except Exception:
        return {}

    # Untilted book: a single developed-international sleeve. Nothing to add.
    if len(book_sleeves) <= 1:
        return {}

    out: dict[str, dict] = {}
    for spec in _INTL_TILT_SLEEVES:
        if spec["sleeve"] not in book_sleeves:
            continue
        try:
            fund_res = run_sleeve_regression(
                sleeve_label=f"{spec['sleeve']} — {spec['fund']}",
                tickers=[spec["fund"]],
                region="developed_exus",
                inception=inception,
                end_date=end_date,
            )
        except Exception:
            fund_res = None
        try:
            control_res = run_sleeve_regression(
                sleeve_label=f"{spec['sleeve']} control — {spec['control']}",
                tickers=[spec["control"]],
                region="developed_exus",
                inception=inception,
                end_date=end_date,
            )
        except Exception:
            control_res = None

        out[spec["fund"]] = {
            "sleeve":         spec["sleeve"],
            "fund":           spec["fund"],
            "fund_name":      spec["fund_name"],
            "control":        spec["control"],
            "control_name":   spec["control_name"],
            "control_index":  spec["control_index"],
            "disclosure":     spec["disclosure"],
            "israel_pct":     spec.get("israel_pct"),
            "fund_result":    fund_res,
            "control_result": control_res,
        }
    return out


# ── FI sleeve TERM/CREDIT factor model ────────────────────────────────────────

def regress_fi_sleeve(inception: str, end_date: str) -> Optional[dict]:
    """
    TERM/CREDIT two-factor model for the FI sleeve: VGIT and SCHP at their sleeves'
    SAA targets (fi_weights).

    TERM   = IEF daily return − BIL daily return  (duration premium proxy)
    CREDIT = HYG daily return − IEF daily return  (credit spread premium proxy)
    RF     = Ken French US daily risk-free rate

    A date on which either fund has no return (before its first close in the window)
    is NaN, not 0% (#639), so the regression uses the dates both funds cover and its
    sample window, printed from them, says where it starts.

    Returns None if fewer than 30 aligned observations are available.
    """
    date_range = pd.date_range(start=inception, end=end_date, freq="D")

    # FI sleeve return (VGIT + SCHP at their sleeves' SAA targets)
    fi_ret = pd.Series(0.0, index=date_range)
    for ticker, w in fi_weights().items():
        p = get_prices(ticker, inception, end_date)
        p.index = pd.to_datetime(p.index)
        series = total_return_series(p)
        fi_ret = fi_ret + w * series.reindex(date_range).ffill().pct_change()
    fi_ret = fi_ret.iloc[1:]
    fi_ret.index = pd.to_datetime(fi_ret.index)

    # TERM and CREDIT factor proxies
    def _ret(ticker: str) -> pd.Series:
        if ticker == "HYG":
            # Inside a quarter lock, the HYG series the lock captured (#382).
            held = hyg_credit_series_locked()
            s = held if held is not None else hyg_credit_series(inception, end_date)
            return s.reindex(date_range).ffill().pct_change().iloc[1:]
        p = get_prices(ticker, inception, end_date)
        p.index = pd.to_datetime(p.index)
        return total_return_series(p).reindex(date_range).ffill().pct_change().iloc[1:]

    ief_ret = _ret("IEF")
    bil_ret = _ret("BIL")
    hyg_ret = _ret("HYG")

    factors_df = pd.DataFrame(
        {"TERM": (ief_ret.values - bil_ret.values), "CREDIT": (hyg_ret.values - ief_ret.values)},
        index=fi_ret.index,
    )

    # RF from Ken French US factors
    ff_us = load_factors("us")
    ff_us.index = pd.to_datetime(ff_us.index)

    merged = (
        fi_ret.to_frame(name="R_fi")
        .join(factors_df, how="inner")
        .join(ff_us["RF"], how="inner")
        .dropna()
    )

    if len(merged) < 30:
        return None

    T = len(merged)
    L = _nw_lags(T)

    R_excess = merged["R_fi"] - merged["RF"]
    X = add_constant(merged[_FI_FACTORS])

    model   = OLS(R_excess, X)
    res_hac = model.fit(cov_type="HAC", cov_kwds={"maxlags": L})
    res_ols = model.fit()

    params = res_hac.params
    tvals  = res_hac.tvalues
    pvals  = res_hac.pvalues

    alpha_daily  = float(params["const"])
    alpha_annual = alpha_daily * 252

    return {
        "alpha_daily":      alpha_daily,
        "alpha_annual":     alpha_annual,
        "alpha_annual_bps": alpha_annual * 10_000,
        "t_alpha":          float(tvals["const"]),
        "p_alpha":          float(pvals["const"]),
        "betas":   {f: float(params[f]) for f in _FI_FACTORS},
        "t_stats": {f: float(tvals[f])  for f in _FI_FACTORS},
        "p_values":{f: float(pvals[f])  for f in _FI_FACTORS},
        "r_squared":     float(res_hac.rsquared),
        "adj_r_squared": float(res_hac.rsquared_adj),
        "T":       T,
        "nw_lags": L,
        "_ols_bse": res_ols.bse.to_dict(),
        "_hac_bse": res_hac.bse.to_dict(),
        "sleeve_label": "FI Sleeve — TERM / CREDIT",
        "tickers":      list(_FI_FUNDS),
        "sample_start": merged.index[0].date().isoformat(),
        "sample_end":   merged.index[-1].date().isoformat(),
    }


# ── Interpretation helpers ─────────────────────────────────────────────────────

def _listed_mix(parts: "list[str]") -> str:
    """["VOO 35%", "VTV 20%", "AVUV 10%"] -> "VOO 35%, VTV 20% and AVUV 10%"."""
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _sign_word(beta: float) -> str:
    """The sign the regression found for a loading, as a word."""
    sign = "positive" if beta > 0 else "negative" if beta < 0 else "zero"
    return label("sign", sign, sign)


# How near 1.0 a market loading must be to be read as full exposure: one band, for the
# US sleeve's Mkt-RF loading and for the portfolio's loading on its own benchmark.
_NEAR_FULL_BETA = 0.15


def _distinguishable(t: float) -> bool:
    """Whether a t-statistic earns any significance label but the last: the loading can
    be told from zero at some level significance_label names."""
    return significance_level(t) != "none"


def momentum_caption(b_mom: float, t_mom: float) -> str:
    """The caption under the Factor Profile page's Carhart momentum table (#551): the
    loading, its t, and the significance label's words for that t, and nothing more.

    Whether the loading can be told from zero is the significance label's call, by its
    cut-offs, as the benchmark prose's is (#542). The caption decided with its own
    |t| >= 1.96, so a loading with t between 1.65 and 1.96, starred in the table above
    it, was read as "no systematic momentum tilt". It also called a significant loading
    "economically small", and said "the loading's size confirms momentum is not a
    deliberate tilt", whatever the loading was: no threshold defines a small momentum
    loading, so those words go.

    It names no fund and adds no sentence (#584, decided by the owner on October 15,
    2026). Under a significant loading it said "Some incidental momentum exposure leaks
    through the SPHQ quality factor", once the sleeve's regression held SPHQ (#560): a
    sleeve's regression cannot show which of its funds carries its loading, and #560
    settled only which fund the caption could name. Under a loading the label cannot
    tell from zero it said the loading "confirms the sleeve carries no systematic
    momentum tilt" (#573, #581); that loading is now stated in the label's words like
    any other."""
    head = f"Mom loading of {b_mom:+.3f} (t = {t_mom:.2f})"
    return f"{head} is {significance_label(t_mom)}."


def _market_reading(b_mkt: float, t_mkt: float) -> str:
    """The clause after the US sleeve's Mkt-RF loading, from the loading itself (#526).

    It read "consistent with near-fully-invested US equity exposure" whatever the
    regression returned. It states the sign and significance found, and draws that
    reading only from a loading that is distinguishable from zero and within
    _NEAR_FULL_BETA of one."""
    found = f"a {_sign_word(b_mkt)} loading that is {significance_label(t_mkt)}"
    if _distinguishable(t_mkt) and abs(b_mkt - 1.0) < _NEAR_FULL_BETA:
        reading = label("us_market_reading", "near_full",
                        "consistent with near-fully-invested US equity exposure")
    elif _distinguishable(t_mkt):
        reading = label("us_market_reading", "not_near_full",
                        "and not the near-one loading a fully invested US equity sleeve "
                        "shows")
    else:
        reading = label("us_market_reading", "not_shown",
                        "so this regression does not by itself show the sleeve's US "
                        "equity exposure")
    return f"{found}, {reading}."


def _tilt_reading(factor: str, beta: float, t: float, tilt: str) -> str:
    """One sentence for a style loading the sleeve is held for (HML, SMB), from the
    loading itself (#526): its value, its sign and significance, and the tilt it is
    read as, only when the loading is positive and distinguishable from zero. The
    HML sentence said "broadly consistent with the value tilts" and the SMB sentence
    "reflects the small-cap exposure" at any sign and any significance."""
    found = (f"The {factor} loading of {beta:.3f} (t = {t:.2f}) is {_sign_word(beta)} "
             f"and {significance_label(t)}")
    if _distinguishable(t) and beta > 0:
        return f"{found}, {label('tilt_reading', 'shown', f'consistent with {tilt}')}."
    if _distinguishable(t):
        contrary = label("tilt_reading", "contrary", f"this regression does not show {tilt}")
        return f"{found}: {contrary}."
    not_shown = label("tilt_reading", "not_shown",
                      f"so this regression does not by itself show {tilt}")
    return f"{found}, {not_shown}."


def _fi_alpha_reading(a_bps: float, t_a: float) -> str:
    """The FI sleeve's alpha, from the alpha itself (#526).

    The paragraph said the alpha "primarily reflects ETF-vs-index tracking differences
    and expense ratios rather than managerial skill", under a confidence interval it
    called wide, whatever the regression returned. It states the sign and significance
    found. An alpha that cannot be told from zero is said to be that, and so no
    evidence of skill. One that can is unexplained return, and the sentence says what
    this regression cannot separate, without choosing."""
    found = (f"Annualized alpha of {a_bps:+.0f} bps (t = {t_a:.2f}) is "
             f"{_sign_word(a_bps)} and {significance_label(t_a)}")
    if _distinguishable(t_a):
        reading = label(
            "fi_alpha_reading", "unexplained",
            "return the TERM/CREDIT proxies do not explain. ETF-vs-index tracking "
            "differences and expense ratios can leave such a remainder, and this "
            "regression does not separate them from managerial skill.")
    else:
        reading = label(
            "fi_alpha_reading", "not_from_zero",
            "the return the TERM/CREDIT proxies leave unexplained cannot be told from "
            "zero at this sample length, so it is not evidence of managerial skill.")
    return f"{found}: {reading}"


def _term_reading(b_term: float, t_term: float, duration_line_location: str) -> str:
    """What the FI sleeve's TERM loading shows, from the loading itself (#481).

    The paragraph said "The positive TERM loading confirms the sleeve carries meaningful
    interest-rate duration" whatever the regression returned. It now states the sign
    and the significance found, and draws the duration reading only from a positive
    loading that is distinguishable from zero. Every branch still points the reader to
    the Fixed Income Effective Duration line, where the fund durations are."""
    found = f"The TERM loading is {_sign_word(b_term)} and {significance_label(t_term)}"
    line = f"the Fixed Income Effective Duration line {duration_line_location}"
    distinguishable = _distinguishable(t_term)
    if distinguishable and b_term > 0:
        reading = label("term_reading", "duration",
                        f"the sleeve carries interest-rate duration, consistent with "
                        f"the fund durations behind {line}")
        return f"{found}: {reading}."
    if distinguishable:
        reading = label("term_reading", "contrary",
                        f"this regression does not show the interest-rate duration "
                        f"that the fund durations behind {line} imply")
        return f"{found}: {reading}."
    reading = label("term_reading", "not_shown",
                    f"so this regression does not by itself show interest-rate "
                    f"duration; the fund durations are behind {line}")
    return f"{found}, {reading}."


def build_factor_prose(
    results: dict,
    fi_result: Optional[dict] = None,
    duration_line_location: str = "on the Performance page",
) -> list[str]:
    """
    Generate institutional-register prose interpreting the sleeve regressions.

    results       : dict with keys 'us' and 'developed_exus' (each a result dict or None).
    fi_result     : optional TERM/CREDIT result dict from regress_fi_sleeve.
    duration_line_location : where the reader finds the Fixed Income Effective Duration
                    line the FI paragraph points to: the Performance page for the
                    Factor Profile page, "in this report" for the PDF.
    Called by both the PDF section builder and the Streamlit page to guarantee
    identical output.

    NO TYPED FIGURES (#478 I01 to I04). The literal text here carries no number that
    a fund, an index or the ledger could move: every figure is a regression result or
    read from this module's own constants. It typed VGIT's and SCHP's durations
    ("~5.5-year" and "~6.8-year", undated; data/etf_metadata.json held 4.9 and 6.3 on
    2026-10-04), Korea's weight in VEA ("~3-4%"; Vanguard's fact sheet as of June 30,
    2026 gives 10.2%), a 2025 return for Korean equities with no index named, and
    "VNQ 60%, DBC 40%" for a sleeve that holds VNQ and PDBC.
    tests/test_factor_prose_types_no_figures.py reads this function's literals and
    fails on one typed back.
    """
    lines: list[str] = []
    us  = computed_result(results.get("us"))
    dev = computed_result(results.get("developed_exus"))

    if us:
        b_hml = us["betas"]["HML"]
        t_hml = us["t_stats"]["HML"]
        b_smb = us["betas"]["SMB"]
        t_smb = us["t_stats"]["SMB"]
        b_mkt = us["betas"]["Mkt-RF"]
        t_mkt = us["t_stats"]["Mkt-RF"]
        a_bps = us["alpha_annual_bps"]
        t_a   = us["t_alpha"]
        T_us  = us["T"]
        s_start = _fmt_date(us["sample_start"])
        s_end   = _fmt_date(us["sample_end"])

        alpha_note = significance_label(t_a)

        # Each loading's sentence states the sign and significance the regression
        # found and draws its reading only from a loading that supports it (#526).
        lines.append(
            f"The US equity sleeve ({T_us} trading days, {s_start} to {s_end}) "
            f"loads on Mkt-RF at {b_mkt:.2f} (t = {t_mkt:.2f}), "
            f"{_market_reading(b_mkt, t_mkt)} "
            f"{_tilt_reading('HML', b_hml, t_hml, 'the value tilts in VTV and AVUV')} "
            f"{_tilt_reading('SMB', b_smb, t_smb, 'the small-cap exposure from the AVUV holding')} "
            f"Annualized alpha of {a_bps:+.0f} bps is {alpha_note}."
        )

    if dev:
        b_mkt_d = dev["betas"]["Mkt-RF"]
        t_mkt_d = dev["t_stats"]["Mkt-RF"]
        a_bps_d = dev["alpha_annual_bps"]
        t_a_d   = dev["t_alpha"]
        T_dev   = dev["T"]
        s_start_d = _fmt_date(dev["sample_start"])
        s_end_d   = _fmt_date(dev["sample_end"])

        alpha_sig_d = significance_label(t_a_d)

        # The two classification claims, each read from its owner's page on
        # 2026-10-04 (#478 I02):
        #   * VEA tracks the FTSE Developed All Cap ex US Index: Vanguard's prospectus
        #     of April 28, 2026 and its fact sheet as of June 30, 2026, which lists
        #     Korea among the fund's ten largest markets.
        #   * FTSE classifies South Korea as Developed: "Markets classified under the
        #     FTSE Equity Country Classification Scheme", as of April 7, 2026.
        #   * French's factors exclude Korea: the country table in "Description of
        #     Fama/French 5 Factors for Developed Markets" lists 23 countries for
        #     Developed and Developed ex US, and Korea is not one of them.
        # Korea's weight in VEA and its 2025 return are not stated: both were typed,
        # and neither is an input this module holds (I02, I03).
        lines.append(
            f"The {_intl_core_label()} sleeve (VEA, {T_dev} trading days, "
            f"{s_start_d} to {s_end_d}) "
            f"loads on Mkt-RF_dev at {b_mkt_d:.2f} (t = {t_mkt_d:.2f}), "
            f"a {_sign_word(b_mkt_d)} loading that is {significance_label(t_mkt_d)}. "
            f"The {a_bps_d:+.0f} bps annualized alpha (t = {t_a_d:.2f}) is {alpha_sig_d}, "
            f"and it reflects a universe mismatch, not skill. "
            f"VEA tracks the FTSE Developed All Cap ex US Index, which classifies "
            f"South Korea as Developed, while "
            f"Ken French's Developed ex-US factor universe excludes Korea entirely. "
            f"The return on Korean equities falls outside the FF Developed ex-US factor "
            f"span and accumulates in the alpha term. "
            f"The reported alpha is therefore return the Developed ex-US factors leave "
            f"unexplained because of the universe mismatch, not risk-adjusted excess return."
        )

    if fi_result:
        b_term   = fi_result["betas"]["TERM"]
        t_term   = fi_result["t_stats"]["TERM"]
        b_credit = fi_result["betas"]["CREDIT"]
        t_credit = fi_result["t_stats"]["CREDIT"]
        a_bps_fi = fi_result["alpha_annual_bps"]
        t_a_fi   = fi_result["t_alpha"]
        T_fi     = fi_result["T"]
        # The regression's own weights (fi_weights), as the page's caption reads
        # them. The paragraph typed "VGIT 60% / SCHP 40%".
        fi_mix = " / ".join(f"{t} {round(w * 100):.0f}%" for t, w in fi_weights().items())

        lines.append(
            f"The FI sleeve ({fi_mix}, {T_fi} trading days) loads on the "
            f"TERM factor (IEF − BIL duration premium) at {b_term:.3f} (t = {t_term:.2f}). "
            f"Its CREDIT factor (HYG − IEF spread premium) loading is {b_credit:.3f} "
            f"(t = {t_credit:.2f}). "
            f"{_term_reading(b_term, t_term, duration_line_location)} "
            f"{_fi_alpha_reading(a_bps_fi, t_a_fi)}"
        )

    lines.append(
        "The Emerging Markets sleeve (IEMG) is excluded from regression analysis: "
        "Ken French does not publish daily EM factor data, and the current "
        "portfolio history is insufficient for a meaningful monthly-frequency regression. "
        "Real assets are excluded: no liquid daily factor proxy set spans "
        "REIT and commodity exposure simultaneously."
    )

    return lines


# ── International tilt-sleeve residual reading + per-fund disclosures ───────────

def intl_residual_reading_order() -> str:
    """The explicit order in which a tilt-sleeve residual should be read.

    Skill is last and least: a non-zero residual is fully expected before any
    question of skill arises. This is the interpretation contract for the whole
    international tilt section and mirrors the 'How to read this page' expander.
    """
    return (
        "**A residual is weighed in this order, skill last and least.** "
        "**(1) Sampling noise.** At about one year of daily data the 95% confidence "
        "interval spans hundreds of basis points; most of any single residual estimate "
        "is noise, and the interval shown with each number says so. "
        "**(2) Universe / classification mismatch.** The fund's index and the Ken French "
        "factor universe classify countries differently. South Korea is developed to "
        f"S&P ({_SP_CLASSIFICATION}) and to FTSE, which provides VEA's index "
        f"({_FTSE_CLASSIFICATION}), but emerging to Ken French ({_FRENCH_EMERGING_LIST}). "
        f"Canada is in the factor universe ({_FRENCH_COUNTRY_TABLE}) but out of MSCI EAFE "
        f"({_MSCI_EAFE_FACTSHEET}). Returns that fall outside the factor span accumulate "
        "in the residual with no skill involved. "
        "**(3) Construction differences** between an investable fund and the academic "
        "factors. The factors are computed gross of foreign dividend withholding, while a "
        "fund reports net-of-withholding NAV. The factors rebalance costlessly and are "
        "long-short. The fund is long-only, reaches a microcap tail the NYSE-style "
        "breakpoints smooth over, and prices across time zones that close before the US "
        "tape (stale pricing). "
        "**(4) Skill**, considered last and weighted least, because every item above "
        "produces a non-zero residual without it. Each fund is shown beside a passive, "
        "Canada-matched control so the residual has an investable yardstick: a control "
        "that carries a large residual too is measuring construction, not selection."
    )


def intl_controls_note() -> str:
    """Why EFV and SCZ are not the tilt sleeves' controls, each universe claim cited to its
    owner's dated document (#623)."""
    return (
        "**EFV and SCZ are not the controls.** The Ken French Developed ex-US factors include "
        f"Canada and leave Korea out ({_FRENCH_COUNTRY_TABLE}); MSCI EAFE (EFV, SCZ) "
        f"excludes Canada ({_MSCI_EAFE_FACTSHEET}). Canada's return is in the factors but not "
        "in EFV or SCZ, so against these factors a control without Canada carries a residual "
        "that comes from the missing country, not from how the control is built. It would "
        "read as a clean gauge while hiding the missing Canada return, so IQLT, IVLU, and "
        "ISVL are used instead: each covers Canada and leaves Korea out, as the factors do. "
        "IQLT's and IVLU's indexes cover MSCI's 22 Developed Markets countries, Canada among "
        f"them and Korea not ({_MSCI_CONTROLS_FACTSHEETS}). ISVL's index selects from the "
        f"FTSE Developed ex US ex Korea Small Cap Index ({_FTSE_FOCUSED_VALUE_RULES}), and "
        f"FTSE classes Canada as Developed ({_FTSE_CLASSIFICATION})."
    )


def build_intl_tilt_disclosure(entry: dict) -> list[str]:
    """Per-fund disclosure paragraphs for one international tilt sleeve.

    ASYMMETRIC by design (`entry['disclosure']`):
      - IDHQ ('idhq') guards against reading the residual as skill — part of its
        residual can be a Korea universe artifact. The Korea mechanism is stated
        STRUCTURALLY (reconstitution-varying), never as a pinned weight, and each
        classification it rests on cites its owner's dated document (#614).
      - AVDV / AVIV ('avantis') guard against OVER-applying the Korea excuse —
        they are actively managed and observed Korea-clear, so the residual is
        construction (Israel, costs, withholding, a window-specific materials /
        gold-miner tilt Avantis flags as unrepeatable), not universe.
    """
    fund    = entry["fund"]
    control = entry["control"]
    cidx    = entry["control_index"]
    kind    = entry["disclosure"]

    paras: list[str] = []

    if kind == "idhq":
        paras.append(
            f"**Part of {fund}'s residual can be a universe artifact rather than "
            f"skill.** {fund}'s index, the S&P Quality Developed Ex-U.S. LargeMidCap "
            "Index, selects from the S&P Developed Ex-U.S. LargeMidCap Index "
            f"({_SP_QUALITY_METHODOLOGY}), and S&P classifies South Korea as "
            f"**developed**: {_SP_CLASSIFICATION}, lists it among S&P's Developed "
            f"markets. So Korean stocks are eligible for {fund}'s quality screen. Ken "
            f"French counts South Korea among the emerging markets ({_FRENCH_EMERGING_LIST}) "
            f"and leaves it out of his Developed ex-US factors ({_FRENCH_COUNTRY_TABLE}). "
            f"The {control} control leaves it out too: MSCI classifies Korea as an "
            f"Emerging Market ({_MSCI_EM_FACTSHEET}), and the control's index, {cidx}, "
            "covers 22 Developed Markets countries that include Canada and not Korea "
            f"({_MSCI_CONTROL_FACTSHEET}). The return of any Korean stock {fund} holds "
            f"therefore falls outside the factor span and lands in {fund}'s residual "
            "with no selection involved."
        )
        paras.append(
            "The exposure is **not a fixed weight**. S&P reconstitutes the index twice "
            "a year, after the close on the third Friday of June and of December, and "
            "weights each stock by its market capitalization times its quality score, "
            "within the methodology's caps. So Korea's share of the index can change at "
            "each reconstitution, and with it the part of the residual that Korea "
            "explains. A residual channel that rises and falls with an index rebalance "
            "is the signature of a universe mismatch, not persistent skill."
        )
        paras.append(
            "One residual channel does come from the screen rather than the universe. "
            "S&P's quality score and the Fama-French **RMW** (profitability) factor "
            "are related but different constructs, so the part of the quality "
            f"selection that RMW does not span also accrues to the residual. The "
            f"{control} control ({cidx}) includes Canada and excludes Korea on the "
            "same terms as the factors. It removes the Korea channel, so its residual "
            "is the cleaner read on what an international quality screen costs "
            "against these factors."
        )
        return paras

    # Avantis funds (AVDV, AVIV) — the "not-Korea" side of the asymmetry.
    israel = entry.get("israel_pct") or "a few"
    paras.append(
        f"**The Korea explanation does not apply to {fund}.** "
        f"Unlike IDHQ, the universe channel does not explain this residual. As of the "
        f"Avantis N-PORT holdings disclosure for the period ending {_NPORT_ASOF}, the "
        f"latest read here, "
        f"{fund} held **no South Korea position**. The absence is *observed* in the filing; "
        f"the mandate does not prohibit Korea, and {fund} is actively managed and could add "
        f"it at a future reconstitution. The {control} control ({cidx}) carves Korea out on "
        "the same terms as the factors, so the fund-minus-control residual is "
        "Korea-neutral by construction."
    )
    paras.append(
        f"What remains is **construction, not universe, and it is not a persistent "
        f"edge.** The fund-minus-control gap is not a repeatable "
        f"{fund}-over-control advantage of this size. A material part of it in this "
        f"window is a sector bet: an overweight to materials and gold miners that "
        f"contributed disproportionately and that Avantis characterizes as cyclical "
        f"and **not repeatable**. As that tilt normalizes, the gap should compress "
        f"rather than compound. What is left is persistent but still not skill. Israel "
        f"is roughly {israel}% of the fund: developed, so spanned by the factors, but a "
        f"source of idiosyncratic country return. The fund's expense ratio and "
        f"unrecovered foreign dividend-withholding drag, on a net-NAV basis, add to it, "
        f"as does the deeper microcap tail the NYSE-style breakpoints smooth over."
    )
    paras.append(
        f"Finally, a joint-metric caveat: Avantis integrates value and profitability "
        f"into a **single** selection metric, so {fund}'s **HML** and **RMW** loadings "
        "are not the separable, one-factor-at-a-time exposures a pure sort would show. "
        "The residual absorbs the part of that joint screen the two standalone factors "
        "do not span. That is again a construction property, weighed well before any "
        "question of skill."
    )
    return paras


def build_factor_methodology_notes(results: dict, fi_result: Optional[dict] = None) -> list[str]:
    """Return methodology disclosure bullet points for the sleeve regressions.

    NO TYPED FIGURES, as build_factor_prose (#481). The Factor Profile page prints
    these. Each figure is read from the code it describes or is gone:
      * the US and FI sleeve weights are the regression's own (us_weights,
        fi_weights). The notes typed 35.6/31.1/17.8/15.6% "proportional to the locked
        Phase 1 sleeve targets of 16/14/8/7%" beside weights of 17/15/9/8, and "9%/6%
        SAA targets" beside a comment saying 6% and 4%;
      * "VNQ 60%, DBC 40%" is gone from the real-assets sentences, as it went from the
        prose (#478 I04);
      * the EM note's "approximately 12 observations" and "3+ years" are gone: nothing
        here computes a monthly count, as em_disclosure records;
      * the two classification claims are the ones their owners' pages make
        (_FTSE_CLASSIFICATION, _FRENCH_COUNTRY_TABLE). "Treated as EM" and "a different
        Israel categorization" were read from no page.
    The momentum vintage note's worked example is one refresh's dated measurements.
    It is read from UMD_REFRESH_EVIDENCE, which carries the refresh's date and the
    commit that measured it (#526); the scan lists no exception.
    tests/test_factor_prose_types_no_figures.py reads this function's literals."""
    us  = computed_result(results.get("us"))
    dev = computed_result(results.get("developed_exus"))

    T_us  = us["T"]  if us  else "N/A"
    L_us  = us["nw_lags"]  if us  else "N/A"
    T_dev = dev["T"] if dev else "N/A"
    L_dev = dev["nw_lags"] if dev else "N/A"

    us_window  = (
        f"{_fmt_date(us['sample_start'])} to {_fmt_date(us['sample_end'])}"
        if us else "N/A"
    )
    dev_window = (
        f"{_fmt_date(dev['sample_start'])} to {_fmt_date(dev['sample_end'])}"
        if dev else "N/A"
    )

    from src.asof import data_vintage
    lag_str = data_vintage("Fama-French factor", factor_frontier("us"))

    # Committed-data staleness (threshold-gated): the frontier is the DATA's
    # last row, never file mtime. Auto-refresh was removed — age is surfaced
    # here (and on the Factor Profile banner), not silently "fixed" at read.
    from src.asof import MARKET_DATA_STALE_DAYS_FACTORS, staleness_note
    _stale_note = staleness_note(
        "Fama-French factor", factor_frontier("us"), MARKET_DATA_STALE_DAYS_FACTORS
    )

    # Committed-vintage disclosure: N/A only when the momentum file is missing
    # entirely, a state the loaders and staleness surfaces already fail loudly on.
    _umd_f = umd_frontier()

    # The one refresh the momentum note cites, read from its record (#526).
    _umd_ev = UMD_REFRESH_EVIDENCE
    # The regressions' own fixed weights, as they are applied (#481).
    us_mix = _listed_mix([f"{t} {w:.1%}" for t, w in us_weights().items()])
    fi_mix = " + ".join(f"{t} ({round(w * 100):.0f}%)" for t, w in fi_weights().items())

    notes = [
        f"Samples: US equity sleeve {T_us} US trading days ({us_window}), L = {L_us}. "
        f"{_intl_core_label()} sleeve {T_dev} US trading days ({dev_window}), L = {L_dev}. "
        "Both sleeves are restricted to US equity market trading days (dates present "
        "in the Ken French US factor calendar). The Developed sleeve applies this "
        "restriction explicitly: VEA trades on US exchanges and has no price observation "
        "on US federal holidays, so its return series shows zero there by forward-fill, "
        "not by market observation. The Dev FF dataset includes those holidays (international "
        "markets open); excluding them keeps both regression calendars consistent. "
        f"Sample sizes reflect the overlap with the factor data. {lag_str}",

        "Methodology: each equity sleeve is regressed against its own region-appropriate "
        "FF5 factor set: US factors for the US sleeve, Developed ex-US factors for VEA. "
        "This per-sleeve approach avoids the model-misspecification problem that inflated "
        "the alpha estimate in the prior single-portfolio regression. There, returns from non-US "
        "equity and real-asset sleeves that the US-only factor model cannot span had been "
        "flowing into the alpha term.",

        "Standard errors: Newey-West HAC, correcting for autocorrelation in daily return "
        "residuals. Lag L is computed per regression as floor(4 × (T/100)^(2/9)).",

        "Sleeve return series: the US equity sleeve return is the weighted daily total "
        f"return of its funds at the regression's fixed weights: {us_mix}. "
        "Weights are held constant. "
        "The Developed sleeve is VEA's daily adjusted-close return.",

        "Universe mismatch, Developed sleeve: VEA tracks the FTSE Developed All Cap ex US "
        "Index, and FTSE classifies both South Korea and Israel as Developed "
        f"({_FTSE_CLASSIFICATION}). Neither is among the countries of Ken French's "
        f"Developed ex-US factors ({_FRENCH_COUNTRY_TABLE}). "
        "The universe difference contributes to unexplained variance "
        "and inflates the Developed-sleeve alpha estimate. A future phase may address this "
        "by reweighting VEA's holdings to match Ken French's universe before regressing, "
        "or by using a custom factor set aligned to FTSE Developed All Cap ex US.",

        "EM sleeve exclusion: Ken French does not publish daily EM factor data, and the "
        "monthly history since this portfolio's inception is too short for stable "
        "inference. EM factor decomposition will be added when the portfolio has "
        "accumulated enough monthly history.",

        "Carhart Momentum supplement (FF5+MOM): Ken French daily UMD factor "
        "(F-F_Momentum_Factor_daily_CSV.zip) added as a sixth regressor alongside FF5 "
        f"to test whether the US Equity and {_intl_core_label()} sleeves each load "
        "systematically on the momentum premium. "
        # Each sleeve has its own table beside its own FF5 result (#590, decided by the
        # owner on October 15, 2026): the sentence spoke of one table and one result.
        "Each sleeve's supplementary table is diagnostic; its FF5 result is "
        "authoritative.",

        "Momentum (UMD) vintage: Mom loadings are computed against the Ken French "
        f"momentum series. {data_vintage('Momentum factor', _umd_f)} Loadings shift between "
        "refreshes chiefly because the sample extends and only secondarily because "
        f"the source revises history. At the {_umd_ev['refresh']} refresh, for example, the "
        f"added quarter moved the rendered Mom loadings by {_umd_ev['extension_moved']} "
        f"while a source revision touching {_umd_ev['history_revised']} of daily UMD "
        f"history back to {_umd_ev['history_from']} moved them by "
        f"less than {_umd_ev['revision_moved_under']}. Neither reflects portfolio changes.",

        f"Global factor supplement ({_intl_core_label()}): Ken French ceased publication "
        "of the daily Global 5-factor file in June 2019. This portfolio's inception "
        "post-dates that cutoff; no daily-frequency Global FF5 regression can be produced. "
        "The Developed ex-US factor set is the primary decomposition for the international "
        "sleeve. Global Mkt-RF would include US exposure in any case, making it less precise "
        "for a developed-ex-US ETF, and the Korea universe mismatch is better bounded by the "
        "Developed ex-US result alone.",
    ]

    if _stale_note:
        notes.insert(0, _stale_note)

    if fi_result:
        T_fi  = fi_result["T"]
        L_fi  = fi_result["nw_lags"]
        fi_window = (
            f"{_fmt_date(fi_result['sample_start'])} to {_fmt_date(fi_result['sample_end'])}"
        )
        notes += [
            f"FI sleeve model: (R_fi − RF) ~ TERM + CREDIT, {T_fi} observations "
            f"({fi_window}), Newey-West HAC L = {L_fi}. "
            "TERM = IEF daily return − BIL daily return (duration premium proxy). "
            "CREDIT = HYG daily return − IEF daily return (credit spread premium proxy). "
            f"FI sleeve return: {fi_mix}, the regression's fixed weights. "
            "Equity FF5 factors do not span fixed income; this dedicated two-factor model "
            "captures duration and credit risk explicitly. RF from Ken French US daily factors.",

            "Real assets remain excluded: no liquid daily factor proxy set spans "
            "REIT and commodity exposure simultaneously. Factor models for real assets are "
            "a future extension.",
        ]
    else:
        notes.append(
            "Fixed income (VGIT, SCHP): TERM/CREDIT factor model (IEF−BIL duration premium, "
            "HYG−IEF credit spread premium) in scope; the FI Sleeve panel above shows it. "
            "Real assets remain excluded; no liquid daily factor proxy set "
            "spans REIT and commodity exposure simultaneously."
        )

    return notes


# ── Benchmark-relative attribution regression ─────────────────────────────────

def _ols_benchmark(R_p_excess: pd.Series, factors: pd.DataFrame) -> dict:
    """
    OLS regression with NW-HAC for the benchmark-relative attribution model.

    R_p_excess : daily portfolio excess returns (R_p − RF), decimal, DatetimeIndex
    factors    : DataFrame with columns Bench-RF, HML, SMB, RMW, decimal

    The model is: R_p − RF ~ const + β_b*(R_b − RF) + β_hml*HML + β_smb*SMB + β_rmw*RMW
    The intercept is "active return after controlling for benchmark beta and style tilts"
    — the institutional alpha definition used by PRINCO, JPM IDD, and similar.

    Alpha is annualized as alpha_daily * 252.
    Both HAC and plain OLS standard errors are returned.
    """
    T = len(R_p_excess)
    L = _nw_lags(T)

    X = add_constant(factors[_BENCH_FACTORS])
    model   = OLS(R_p_excess, X)
    res_hac = model.fit(cov_type="HAC", cov_kwds={"maxlags": L})
    res_ols = model.fit()

    params = res_hac.params
    tvals  = res_hac.tvalues
    pvals  = res_hac.pvalues

    alpha_daily  = float(params["const"])
    alpha_annual = alpha_daily * 252

    return {
        "alpha_daily":      alpha_daily,
        "alpha_annual":     alpha_annual,
        "alpha_annual_bps": alpha_annual * 10_000,
        "t_alpha":          float(tvals["const"]),
        "p_alpha":          float(pvals["const"]),
        "betas":   {f: float(params[f]) for f in _BENCH_FACTORS},
        "t_stats": {f: float(tvals[f])  for f in _BENCH_FACTORS},
        "p_values":{f: float(pvals[f])  for f in _BENCH_FACTORS},
        "r_squared":     float(res_hac.rsquared),
        "adj_r_squared": float(res_hac.rsquared_adj),
        "T":       T,
        "nw_lags": L,
        "_ols_bse": res_ols.bse.to_dict(),
        "_hac_bse": res_hac.bse.to_dict(),
    }


def run_benchmark_attribution_regression(
    inception: str,
    end_date: str,
) -> Optional[dict]:
    """
    Benchmark-relative attribution regression (portfolio vs custom blended SAA).

    Model: (R_p − RF) ~ (R_b − RF) + HML + SMB + RMW

      R_p: daily portfolio total return from get_portfolio_value_series
      R_b: daily custom blended SAA benchmark return from get_custom_blended_series:
           the one series, rebalanced to target weights each calendar quarter (#383)
      RF:  Ken French US daily risk-free rate
      HML, SMB, RMW: Ken French US FF5 style factors (CMA excluded — see methodology)

    The intercept is active return after controlling for benchmark beta and style
    tilts. Standard errors: Newey-West HAC, L = floor(4*(T/100)^(2/9)).

    Returns None when fewer than 30 aligned observations are available.
    """
    date_range = pd.date_range(start=inception, end=end_date, freq="D")

    pv = get_portfolio_value_series(inception, end_date, account_id=get_portfolio_account_id())
    if pv.empty or float(pv.max()) == 0.0:
        return None
    pv = pv.reindex(date_range).ffill()
    R_p = pv.pct_change()

    bl = get_custom_blended_series(inception, end_date)
    bl = bl.reindex(date_range).ffill()
    R_b = bl.pct_change()

    ff = load_factors("us")
    ff.index = pd.to_datetime(ff.index)

    merged = (
        R_p.to_frame(name="R_p")
        .join(R_b.to_frame(name="R_b"), how="inner")
        .join(ff[["RF", "HML", "SMB", "RMW"]], how="inner")
        .dropna()
    )

    if len(merged) < 30:
        return None

    R_p_excess = merged["R_p"] - merged["RF"]
    R_b_excess = merged["R_b"] - merged["RF"]

    factors_df = pd.DataFrame({
        "Bench-RF": R_b_excess.values,
        "HML":      merged["HML"].values,
        "SMB":      merged["SMB"].values,
        "RMW":      merged["RMW"].values,
    }, index=merged.index)

    result = _ols_benchmark(R_p_excess, factors_df)
    result["sample_start"] = merged.index[0].date().isoformat()
    result["sample_end"]   = merged.index[-1].date().isoformat()

    return result


def build_benchmark_prose(
    result: Optional[dict],
    bhb_top_selection: Optional[list] = None,
    bhb_period_label: Optional[str] = None,
    bhb_location: str = "on the Performance page",
) -> list[str]:
    """
    Generate institutional-register prose interpreting the benchmark attribution regression.

    Called by both the PDF section builder and the Streamlit page.

    bhb_top_selection: optional list of dicts from the caller:
        [{"holding": "AVUV", "bench": "IWM", "sel_bps": 754.0}, ...]
      When provided and alpha is significant, a cross-reference to the
      Brinson-Fachler attribution is included with specific numbers.
      When None, the cross-reference is generic (no bps numbers).
    """
    if result is None:
        return [
            "Benchmark attribution regression is unavailable: insufficient aligned observations "
            "(minimum 30 trading days required). Section will populate as portfolio history grows."
        ]

    b_bench = result["betas"]["Bench-RF"]
    t_bench = result["t_stats"]["Bench-RF"]
    b_hml   = result["betas"]["HML"]
    t_hml   = result["t_stats"]["HML"]
    b_smb   = result["betas"]["SMB"]
    t_smb   = result["t_stats"]["SMB"]
    b_rmw   = result["betas"]["RMW"]
    t_rmw   = result["t_stats"]["RMW"]
    a_bps   = result["alpha_annual_bps"]
    t_a     = result["t_alpha"]
    T       = result["T"]
    r2      = result["r_squared"]
    s_start = _fmt_date(result["sample_start"])
    s_end   = _fmt_date(result["sample_end"])

    bench_note = (
        label("benchmark_beta", "near_full",
              "consistent with near-full beta to the SAA benchmark")
        if abs(b_bench - 1.0) < _NEAR_FULL_BETA
        else label("benchmark_beta", "departing",
                   "departing from benchmark (expected ≈ 1.0 for a fully-invested "
                   "passive portfolio)")
    )

    sig_label      = significance_label(t_a)
    # Significant at any level the label above names, by the label's own cut-offs (#542):
    # the cross-reference below decided with its own 1.65, so moving the label's 10%
    # cut-off alone would have called the intercept indistinguishable from zero and then
    # read it as the selection effects, or the reverse.
    is_significant = _distinguishable(t_a)

    # The alpha's 95% CI, by the rule the pages print it with (alpha_interval_bps).
    _interval = alpha_interval_bps(result)
    if _interval is not None:
        _ci_lo, _ci_hi = _interval
        _ci_part = (f" (95% CI: [{interval_end_str(_ci_lo)}, {interval_end_str(_ci_hi)}]; "
                    f"t = {t_a:.2f})")
    else:
        _ci_part = f" (t = {t_a:.2f})"

    # What a significant intercept is read as: the lead-in of the cross-reference.
    selection = label(
        "active_return_reading", "selection",
        "This active return is consistent with the selection effects documented in the "
        "Brinson-Fachler attribution")

    # Second paragraph: intercept interpretation + optional Brinson-Fachler cross-reference
    second_para = (
        f"The intercept, the active return after controlling for benchmark beta and style "
        f"tilts, is {a_bps:+.0f} bps/yr{_ci_part}, {sig_label}. "
    )

    if is_significant and bhb_top_selection:
        parts = []
        for item in (bhb_top_selection or [])[:3]:
            bps    = item["sel_bps"]
            # A difference whose printed figure is 0 has no sign to carry. It read
            # "outperformed ... by 0 bps", and reads "performed in line with", the class
            # #600 added to this family for the summary's alpha (#602).
            if f"{abs(bps):.0f}" == "0":
                parts.append(f"{item['holding']} "
                             f"{label('versus_benchmark', 'in_line', 'performed in line with')} "
                             f"{item['bench']}")
                continue
            verb   = (label("versus_benchmark", "out", "outperformed") if bps > 0
                      else label("versus_benchmark", "under", "underperformed"))
            parts.append(
                f"{item['holding']} {verb} {item['bench']} by {abs(bps):.0f} bps"
            )
        if len(parts) == 1:
            joined = parts[0]
        elif len(parts) == 2:
            joined = f"{parts[0]}, and {parts[1]}"
        else:
            joined = f"{parts[0]}, {parts[1]}, and {parts[2]}"
        second_para += (
            f"{selection}: {joined}. "
            f"The benchmark attribution regression aggregates these sleeve-level selection "
            f"effects into a single portfolio-level active return after controlling for "
            f"systematic style tilts. "
        )
    elif is_significant:
        second_para += (
            f"{selection}. "
            "The benchmark attribution regression aggregates sleeve-level selection effects "
            "into a single portfolio-level active return after controlling for systematic "
            "style tilts. "
        )

    second_para += (
        f"With {T} observations, the confidence interval around the alpha estimate is wide, "
        f"and persistence of the active return cannot be established without a longer history."
    )

    out = [
        f"Portfolio benchmark-relative regression ({T} trading days, {s_start} to {s_end}): "
        f"the portfolio loads on the custom blended benchmark at β = {b_bench:.3f} "
        f"(t = {t_bench:.2f}), {bench_note}. "
        f"Residual style exposures: HML β = {b_hml:.3f} (t = {t_hml:.2f}), "
        f"SMB β = {b_smb:.3f} (t = {t_smb:.2f}), "
        f"RMW β = {b_rmw:.3f} (t = {t_rmw:.2f}). "
        f"R² = {r2:.3f}.",

        second_para,
    ]

    # Cross-window disclosure: this regression is since-inception, whereas the
    # Brinson-Fachler attribution uses a shorter report-period window, so their top
    # selection drivers differ. The quarter is DERIVED when the caller knows it (the
    # PDF passes its report label); the Streamlit page leaves it generic because the
    # Performance page's BF window is user-selectable. `bhb_location` names where that
    # attribution lives — "on the Performance page" (default, the page) or "in this
    # report" (the PDF) — so neither surface names the wrong one, and the quarter is
    # never hardcoded ("Q1 2026" was both stale and, in PDF output, on the wrong page).
    if bhb_period_label:
        _bf_clause = f"the Brinson-Fachler attribution {bhb_location} covers {bhb_period_label}"
    else:
        _bf_clause = (
            f"the Brinson-Fachler attribution {bhb_location} uses a report-period "
            f"(single-quarter) window"
        )
    out.append(
        f"Note: this regression covers the since-inception window; {_bf_clause}. "
        f"The top selection drivers differ by design, reflecting the longer window captured here."
    )

    return out


def build_benchmark_methodology(result: Optional[dict]) -> list[str]:
    """Return methodology disclosure bullet points for the benchmark attribution regression."""
    T      = result["T"]      if result else "N/A"
    L      = result["nw_lags"] if result else "N/A"
    window = (
        f"{_fmt_date(result['sample_start'])} to {_fmt_date(result['sample_end'])}"
        if result else "N/A"
    )
    return [
        "R_p: daily portfolio total return (adjusted-close basis, SPAXX proxied via BIL normalized "
        "to $1.00 at inception). R_b: daily custom blended SAA benchmark return (target-weight "
        "basket: SPY, QUAL, IWD, IWM, EFA, EEM, IEF, TIP, 60% VNQ + 40% DBC, BIL). "
        "RF, HML, SMB, RMW: Ken French US daily factors (Dartmouth). "
        "All series aligned by inner join on trading dates.",

        "The benchmark beta (Bench-RF coefficient) measures how closely the portfolio tracks "
        "its own SAA benchmark. A value near 1.0 indicates near-full tracking; deviations "
        "reflect cross-sleeve return dispersion from active tilts relative to the benchmark "
        "basket. Style betas (HML, SMB, RMW) capture portfolio-wide factor tilts not "
        "explained by benchmark beta. The intercept is the active return component "
        "unexplained by benchmark beta and style.",

        "CMA (investment factor) is excluded from this regression. For a passive/semi-passive "
        "multi-ETF implementation, CMA primarily captures differences in accruals and capex "
        "patterns across the constituent ETFs, which reflect no deliberate active tilt. Including it "
        "would add collinearity without improving interpretation. HML, SMB, and RMW are "
        "the style factors most informative for this portfolio's deliberate tilts "
        "(value via VTV, size via AVUV, quality/profitability via SPHQ).",
    ]


# ── Per-sleeve and per-regression dynamic interpretation ──────────────────────

def interpret_benchmark_attribution(result: dict) -> str:
    """
    Generate a focused interpretation of the benchmark attribution regression.

    This regression is a SELECTION-and-INTRA-SLEEVE-TILT model:
      (R_p − RF) ~ β_bench(R_b − RF) + β_HML·HML + β_SMB·SMB + β_RMW·RMW + α

    The blended benchmark (R_b) captures the SAA policy allocation.
    Style factor loadings ABOVE the benchmark capture residual tilts not
    explained by the SAA's target weights. Alpha is the active return
    after controlling for both the benchmark and those residual style tilts.
    This is NOT a Brinson-Fachler decomposition; that lives on the
    Performance page.

    Whether a style loading or the intercept can be told from zero is the significance
    label's call, by its cut-offs, as the report's benchmark prose's is (#542) and the
    momentum caption's (#551). This caption decided with its own |t| >= 2.0, so a
    loading or intercept with t between 1.65 and 2.0, starred in the page's table and
    given a level in the report, was read here as not significant (#559). The intercept
    sentence names the level the label gives.

    Args:
        result: result dict from run_benchmark_attribution_regression.
    """
    if result is None:
        return "Benchmark attribution regression unavailable."

    b_bench = result["betas"]["Bench-RF"]
    t_bench = result["t_stats"]["Bench-RF"]
    b_hml   = result["betas"]["HML"]
    t_hml   = result["t_stats"]["HML"]
    b_smb   = result["betas"]["SMB"]
    t_smb   = result["t_stats"]["SMB"]
    b_rmw   = result["betas"]["RMW"]
    t_rmw   = result["t_stats"]["RMW"]
    a_bps   = result["alpha_annual_bps"]
    t_alpha = result["t_alpha"]
    r2      = result["r_squared"]

    # Benchmark beta sentence — meaning only, no specific coefficient values
    if abs(b_bench - 1.0) < 0.10:
        bench_sentence = (
            "The portfolio tracks its SAA policy benchmark closely, consistent with a "
            "fully invested passive/semi-passive implementation."
        )
    else:
        bench_sentence = (
            "The portfolio's benchmark beta departs from the expected 1.0, reflecting "
            "cross-sleeve return dispersion or cash drag."
        )

    # Style tilt sentences — meaning only, no specific β or t-stat values
    style_parts = []
    for fname, beta, tstat, context, noun in [
        ("HML", b_hml, t_hml, "value tilt (VTV, AVUV)", "value"),
        ("SMB", b_smb, t_smb, "small-cap tilt (AVUV)", "small-cap"),
        ("RMW", b_rmw, t_rmw, "profitability tilt (SPHQ, AVUV)", "profitability"),
    ]:
        if _distinguishable(tstat):
            if beta > 0:
                style_parts.append(f"{fname}: positive {context}")
            else:
                style_parts.append(
                    f"{fname}: negative, less {noun} exposure than the SAA benchmark carries")

    if style_parts:
        style_sentence = (
            "Residual style tilts beyond the SAA benchmark: "
            + "; ".join(style_parts) + ". "
            "The regression table above gives the loadings and their significance."
        )
    else:
        style_sentence = (
            "No statistically significant residual style tilts beyond the SAA benchmark "
            "are detected at the current sample length. "
        )

    # Alpha sentence — the level the label names and the direction; the estimate lives in
    # the table
    if _distinguishable(t_alpha):
        alpha_dir = "positive" if a_bps > 0 else "negative"
        alpha_sentence = (
            f"The active return intercept is {significance_label(t_alpha)}: a {alpha_dir} "
            "return after accounting for both the SAA benchmark and residual style exposures. "
            "The regression table and the Interpretation section below give the full "
            "alpha estimate with its confidence interval."
        )
    else:
        alpha_sentence = (
            "The active return intercept is not statistically significant at the current "
            "sample length: the portfolio's return is consistent with its SAA benchmark "
            "exposure and residual style tilts alone."
        )

    return bench_sentence + " " + style_sentence + " " + alpha_sentence


def interpret_correlations(corr: pd.DataFrame,
                           equity: "set[str] | frozenset[str]" = frozenset()) -> str:
    """
    Generate 2–3 sentences interpreting a correlation matrix of sleeve returns.

    Identifies highest and lowest off-diagonal pairs, cross-asset patterns,
    and flags any unexpected correlations. Returns an empty string if the
    matrix has fewer than 3 sleeves.

    The top pairs are called intra-equity co-movement only when both sleeves of every
    pair named are in ``equity`` (#427, decided by the owner on October 15, 2026): the
    sentence said so whatever the pairs were. With no equity set given, nothing is
    called equity.

    Args:
        corr: square correlation DataFrame (sleeves as both index and columns).
        equity: the book's equity sleeves (asset_evaluation.equity_sleeve_names()).
    """
    if corr.empty or len(corr) < 3:
        return ""

    # Build sorted list of off-diagonal pairs
    pairs: list[tuple[str, str, float]] = []
    cols = list(corr.columns)
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            pairs.append((cols[i], cols[j], float(corr.iloc[i, j])))

    if not pairs:
        return ""

    pairs_sorted = sorted(pairs, key=lambda x: x[2])
    lowest  = pairs_sorted[:2]
    highest = pairs_sorted[-3:][::-1]   # top 3, highest first

    # Highest correlations sentence — lead with allocator conclusion, pairs as evidence
    # Co-movement means a positive ρ: a small matrix's "top" pairs can be negative.
    high_parts = [
        f"{a} × {b} (ρ = {r:.2f})" for a, b, r in highest if 0 < r < 0.999
    ]
    high_list = (", ".join(high_parts[:-1]) + ", and " + high_parts[-1]
                 if len(high_parts) > 2 else " and ".join(high_parts))
    all_equity = bool(high_parts) and all(
        a in equity and b in equity for a, b, r in highest if 0 < r < 0.999)
    # The top pairs and their correlations, and no grade of them (#608, decided by the
    # owner on October 15, 2026). It opened "High intra-equity co-movement means the
    # equity sleeves largely share a single global market beta" for any top pair above
    # zero: "high" named no cut, and the reading followed from the grade.
    high_sentence = (
        f"The top {'intra-equity ' if all_equity else ''}"
        f"{'pairs are' if len(high_parts) > 1 else 'pair is'} {high_list}."
    ) if high_parts else ""

    # Lowest correlations sentence — frame as cross-asset structural difference
    low_parts = [
        f"{a} × {b} (ρ = {r:.2f})" for a, b, r in lowest
    ]
    negative = [(a, b) for a, b, r in pairs if r < 0]
    shared = set.intersection(*({a, b} for a, b in negative)) if negative else set()
    tail = ""
    if len(negative) > len(lowest):
        tail = (f" {len(negative)} of the {len(pairs)} pairs are negatively correlated"
                + (f", every one involving {sorted(shared)[0]}" if len(shared) == 1 else "")
                + ".")
    # "Return offsets" only when every pair named is negative: a pair at ρ = 0.05
    # co-moves weakly, it does not offset.
    low_lead = ("The strongest return offsets are" if all(r < 0 for _a, _b, r in lowest)
                else "The lowest correlations are")
    low_sentence = (
        f"{low_lead} {' and '.join(low_parts)}: pairs whose diversification comes "
        "from structural differences in risk exposure rather than style tilts alone." + tail
    ) if low_parts else ""

    sentences = [s for s in [high_sentence, low_sentence] if s]
    return "  \n".join(sentences)
