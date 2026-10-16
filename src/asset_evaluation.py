"""Asset evaluation framework — computations for candidate asset analysis.

Houses pure-Python, unit-testable functions for evaluating candidate assets
for SAA inclusion. Pages consume this module; computations do not import Streamlit.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from src.macro import FREDStillFetching, classify_regime, get_series
from src.prices import get_prices, total_return_series

# ── Constants ────────────────────────────────────────────────────────────────

# Bitcoin's tax line in the case against, one text for the Asset Evaluation page and the
# PDF (audit item 15d). The PDF's copy read "Commodity tax treatment: short-term ordinary
# income / long-term capital gains, no qualified-dividend treatment", which is a commodity
# fund's vocabulary, not bitcoin's: the IRS treats bitcoin as property.
# Each clause says what its IRS source says, with the source's date (#625, decided by the
# owner on October 16, 2026): Notice 2014-21 for the property treatment, and the IRS's
# virtual-currency FAQ (A4, A6) for the gain and its holding period. "Short-term at
# ordinary rates within 12 months" cited nothing, and "That is unfavorable vs. ETFs, which
# qualify for in-kind creation/redemption" went: in-kind redemption limits a fund's own
# distributions, not how a holder's sale is taxed.
BITCOIN_TAX_LINE = (
    "Capital gains tax treatment: the IRS treats bitcoin as property (Notice 2014-21, "
    "Internal Revenue Bulletin 2014-16, April 14, 2014). A sale is a capital gain or loss, "
    "short-term if the bitcoin was held one year or less and long-term if it was held more "
    "than one year (the IRS's \"Frequently asked questions on virtual currency "
    "transactions\", last reviewed or updated June 30, 2026)"
)

SAMPLE_START  = "2018-01-01"
# The DEFAULT rate for callers that pass none: the fixed 4.5% the page used before
# audit item 15a, equal to src/performance.py's compute_risk_metrics default (pinned
# by tests/test_asset_evaluation.py::test_rf_annual_matches_performance_rf). The page
# and the PDF pass sample_risk_free() instead, the sample's average 3-month bill
# rate. Same geometric daily-compounding convention either way:
# rf_daily = (1+rf)^(1/252)-1.
RF_ANNUAL     = 0.045
TRADING_DAYS  = 252

# Sleeve benchmark tickers + ex-cash MV weights, DERIVED from asset_classes at
# CALL time so they describe whatever book this process is pointed at (9 sleeves
# personal, 12 demo) rather than freezing a taxonomy. Never derived at import:
# an import-time derivation opens the database from `import` itself (pytest
# collection, a bare REPL import) and freezes the sleeve set before the phase-46
# international split can rename it. Cash / SPAXX is excluded: near-zero
# variance distorts correlation and optimisation. Names used to be abbreviated
# ("International …" -> "Intl …") for the correlation matrix, so Risk and Correlations
# said "Intl" where every other page says "International" (audit item 13). They are
# the DB names now; the function stays as the one place a display name is made.
def _abbrev(name: str) -> str:
    return name


def fmt_corr(v: float, sign: str = "") -> str:
    """A correlation to two places, never "-0.00": the page read "Low correlation
    (-0.00) with Core Fixed Income" (audit item 13). ``sign="+"`` forces a sign."""
    r = round(float(v), 2) + 0.0
    return f"{r:{sign}.2f}"


def _derive_sleeve_maps() -> tuple[dict, dict]:
    from src.sleeve_config import (
        sleeve_benchmarks as _sb,
        strategic_sleeve_weights as _sw,
    )
    bench = {_abbrev(k): v for k, v in _sb(include_cash=False).items()}
    raw = {_abbrev(k): v for k, v in _sw(include_cash=False).items()}
    total = sum(raw.values())
    weights = {k: v / total for k, v in raw.items()}
    return bench, weights


def sleeve_benchmarks() -> dict[str, list[tuple[str, float]]]:
    """Sleeve → benchmark legs for the CURRENT book, resolved at call time."""
    return _derive_sleeve_maps()[0]


def sleeve_weights() -> dict[str, float]:
    """Ex-cash MV weights for the CURRENT book, resolved at call time."""
    return _derive_sleeve_maps()[1]


def sleeve_names() -> list[str]:
    """Sleeve display names (abbreviated) for the CURRENT book, in DB order."""
    return list(_derive_sleeve_maps()[0].keys())

def pre_post_2020(rolling: "pd.Series | None") -> "tuple[float, float, pd.Timestamp | None]":
    """(average before 2020, average from 2020 on, the date the series runs through) of
    the rolling BTC-SPY correlation: the two figures the section prints under its
    rolling chart, computed in one place so the conclusion cites the same ones. NaN and
    None where there is nothing to average."""
    if rolling is None or rolling.empty:
        return float("nan"), float("nan"), None
    pre, post = rolling.loc[:"2019-12-31"], rolling.loc["2020-01-01":]
    return (float(pre.mean()) if len(pre) else float("nan"),
            float(post.mean()) if len(post) else float("nan"),
            pd.Timestamp(rolling.index.max()))


def rolling_averages_sentence(pre: float, post: float) -> str:
    """The page's sentence under its rolling chart: the two averages pre_post_2020
    returns, and no reading of them (#601, decided by the owner on October 15, 2026), as
    the PDF states them since #426. It called the pre-2020 average "broadly consistent
    with the 'uncorrelated alternative' narrative" and the later one "persistently
    elevated ... undermining the diversification claim", with a mechanism after it,
    whatever the two were. Each average renders only when there is one; empty when
    neither is."""
    first = (f"Before 2020, BTC's rolling 60-day correlation with SPY averaged "
             f"{fmt_corr(pre)}." if np.isfinite(pre) else "")
    subject = "it" if first else "BTC's rolling 60-day correlation with SPY"
    second = (f"From 2020 on, {subject} has averaged {fmt_corr(post)}."
              if np.isfinite(post) else "")
    return " ".join(s for s in (first, second) if s)


def bond_equity_paragraph(pre: float, now: float, window: int) -> str:
    """The Correlations page's bond-equity paragraph: Core Fixed Income's average
    correlation with the equity sleeves before 2020 and over the trailing window, and no
    reading of the two (#427, decided by the owner on October 15, 2026). It opened
    "Bond–equity correlations have shifted materially since 2020" and called the change
    "a regime, not a permanent structural change", whatever the two were. The pre-2020
    figure is called consistent with 60/40's negative-correlation assumption only when it
    is negative as printed."""
    pre_s = fmt_corr(pre)
    return (
        f"Core Fixed Income's average pairwise correlation with the equity sleeves was "
        f"ρ ≈ {pre_s} before 2020"
        + (", consistent with the negative-correlation assumption that underpins most "
           "60/40 frameworks" if float(pre_s) < 0 else "")
        + f". The trailing {window}-day estimate is ρ ≈ {fmt_corr(now)}. The 2022 joint "
        "drawdown, when Core Fixed Income declined alongside equities, is the relevant "
        "stress test for this allocation."
    )


def only_positive_difference(diff: pd.Series) -> "str | None":
    """5d's sentence on the weekly-minus-daily correlations, read from the table: which
    sleeve's difference is the only positive one, when exactly one is (#425). It named
    TIPS whenever TIPS's was positive, whatever the others were. A difference is positive
    as the table prints it, to two places, so +0.004 is not."""
    shown = diff.dropna().round(2)
    positive = shown[shown > 0]
    if len(positive) != 1:
        return None
    return (f"{positive.index[0]} shows the only positive daily-to-weekly difference "
            f"({float(positive.iloc[0]):+.2f}).")


def curve_top_allocation(curve: "pd.DataFrame | None") -> "str | None":
    """The marginal-Sharpe curve's highest Bitcoin allocation as a percent ("10%"), when
    the curve holds its 0% point and at least one weight above it; else None. The PDF's
    "none found" banner and the conclusion's second trigger name the range the curve was
    read over (#426, #627); the page and the PDF read it here, the same way."""
    if curve is None or curve.empty:
        return None
    above = curve[curve["btc_alloc"] > 0.00]
    if curve[curve["btc_alloc"] == 0.00].empty or above.empty:
        return None
    return f"{float(above['btc_alloc'].max()):.0%}"


def conclusion(rolling_corr: "pd.Series | None" = None,
               sharpe_curve_top: "str | None" = None) -> str:
    """The section's conclusion, for the page (pages/5_Asset_Evaluation.py, which ends
    with it) and the PDF, from the rolling BTC-SPY correlation the section charts.

    IT READS THE SECTION'S OWN FIGURES, WITH THEIR DATE (#478 I11). It was a constant.
    Its first re-evaluation trigger was a rolling 5-year correlation "below 0.20 (the
    pre-2020 baseline established in the rolling BTC-SPY correlation)": the section
    computes no 5-year correlation, and the pre-2020 average it does print, of the
    60-day one, is about zero. And its verdict said "currently" with no date. The
    trigger now names the correlation the section computes and gives its two averages,
    and the verdict carries the date that series runs through. With no series (the
    rolling analysis unavailable) both stand without figures and without a date.

    The second trigger reads its range from the marginal-Sharpe curve the section reads,
    ``sharpe_curve_top`` (curve_top_allocation). It typed "A 5–10% allocation", which
    nothing read (#627). With no curve it names no size.

    Excludes the page's "updates automatically" sentence, which the PDF cannot say."""
    from src.asof import format_long_date
    pre, post, through = pre_post_2020(rolling_corr)
    if through is None:
        verdict = "**Bitcoin does not belong in this portfolio's SAA.**"
    else:
        verdict = (f"**As of {format_long_date(through.date())}, Bitcoin does not belong "
                   "in this portfolio's SAA.**")
    # The trigger as the owner decided it on October 15, 2026 (#425): the average from
    # 2020 on falls to the pre-2020 average the page prints. It read "returns to its
    # pre-2020 level", which did not say which figure would have to move.
    trigger = ("- The rolling 60-day correlation with SPY, on average from 2020 on, falls "
               "to its pre-2020 average")
    if through is not None and not np.isnan(post):
        since = f"{fmt_corr(post)} from 2020 through {format_long_date(through.date())}"
        # A book whose candidate history starts in 2020 or later has no rolling
        # correlation before it: the section prints no pre-2020 average, so none is cited.
        trigger += (f": this sample holds none before 2020, and it has averaged {since}"
                    if np.isnan(pre) else
                    f": it averaged {fmt_corr(pre)} before 2020 and {since}")
    trigger += ". That would indicate a return to diversifying behavior.\n"
    return (
        _CONCLUSION_OPENING + verdict + "\n\n"
        "The framework would re-evaluate inclusion if any of the following "
        "observable conditions are met:\n"
        + trigger + _sharpe_trigger(sharpe_curve_top) + _CONCLUSION_STRESS_TRIGGER
    )


# The full-sample correlation with US Large Core above which the section lists it
# among the arguments against inclusion.
EQUITY_LIKE_ABOVE = 0.3


def equity_like_argument(corr_us_large_core: float) -> "str | None":
    """The "arguments against" bullet for an equity-like correlation, or None at or
    below EQUITY_LIKE_ABOVE. The figure is the FULL-SAMPLE correlation and says so. It
    was labelled "post-2020" (#478 I12), and the post-2020 figure the section prints is
    a different number: the rolling correlation's average from 2020 on."""
    if np.isnan(corr_us_large_core) or corr_us_large_core <= EQUITY_LIKE_ABOVE:
        return None
    # "Co-movement spikes during stress, when a hedge is most valuable" went (#625): no
    # figure the report prints shows a correlation spike in stress.
    return (f"Equity-like correlation with US Large Core ({corr_us_large_core:.2f} over "
            f"the full sample, {SAMPLE_START[:4]} to present)")


# The conclusion's opening (#625, decided by the owner on October 16, 2026). It said the
# Sharpe improvement was "real but almost entirely attributable to its exceptional
# 2020–2021 bull market return", which nothing here computes, and that "IBIT, FBTC, and
# similar wrappers remove the custody and counterparty frictions that defined the pre-2024
# implementation landscape", which IBIT's 10-K does not say. The spot-ETF sentence keeps
# what the 10-K states, as #611 cited it; "not operational" went with the frictions claim,
# which it restated.
_CONCLUSION_OPENING = (
    "IBIT's shares are \"intended to constitute a simple means of making an investment "
    "similar to an investment in bitcoin rather than by acquiring, holding and trading "
    "bitcoin directly on a peer-to-peer or other basis or via a digital asset platform\", "
    "and custodians hold the trust's bitcoin: Coinbase Custody Trust Company, with "
    "Anchorage Digital Bank as an additional available custodian (iShares Bitcoin Trust "
    "ETF, annual report on Form 10-K for 2025, filed February 27, 2026). "
    "The barriers to inclusion are analytical (the post-2020 correlation regime and the "
    "2022 joint stress) and fundamental (no cash flow anchor for valuation). "
)
def _sharpe_trigger(sharpe_curve_top: "str | None") -> str:
    """The conclusion's second re-evaluation trigger, as the owner decided it on October 16,
    2026 (#627): its range is the marginal-Sharpe curve's, and with no curve it names no
    size."""
    size = (f"An allocation of up to {sharpe_curve_top}" if sharpe_curve_top
            else "A Bitcoin allocation")
    return (f"- {size} improves the portfolio's Sharpe ratio after explicit tax-drag "
            "adjustment (the analytical gap in the constrained mean-variance analysis).\n")


_CONCLUSION_STRESS_TRIGGER = (
    "- A negative-correlation episode is observed during a defined equity "
    "stress period (the 2022 joint drawdown in the drawdown-sensitivity "
    "analysis is the relevant counter-example)."
)


# ── Data loading ─────────────────────────────────────────────────────────────

def _price_series(ticker: str, start: str, end: str) -> pd.Series:
    """adj_close price series indexed by pd.Timestamp, or empty Series on failure."""
    try:
        p = get_prices(ticker, start, end)
        p.index = pd.to_datetime(p.index)
        return total_return_series(p).ffill()
    except Exception:
        return pd.Series(dtype=float)


def get_candidate_returns(
    ticker: str,
    start_date: str = SAMPLE_START,
    end_date: Optional[str] = None,
) -> pd.Series:
    """Daily pct_change returns for a candidate asset (e.g., BTC-USD)."""
    end    = end_date or date.today().isoformat()
    prices = _price_series(ticker, start_date, end)
    if prices.empty:
        return pd.Series(dtype=float)
    return prices.pct_change().dropna().sort_index()


def get_sleeve_returns(
    start_date: str = SAMPLE_START,
    end_date: Optional[str] = None,
    exclude: Optional[list[str]] = None,
) -> pd.DataFrame:
    """
    Daily returns for the non-cash SAA sleeves, trading-day intersection.

    Blended sleeves use weighted-average daily returns.
    Rows where any sleeve has a near-zero absolute return sum are dropped
    (Phase 11 zero-return rule: these are non-trading days for equity markets).

    All sleeves of the CURRENT book are included by default; the common-date
    intersection is then bounded by the youngest benchmark. Pass ``exclude`` (a
    list of sleeve names) to drop sleeves *before* the intersection. Derive that
    set with ``late_inception_sleeves()`` — do not name tickers here.

    This docstring used to read *"``exclude=["US Large Quality"]`` drops QUAL so
    the remaining 8-sleeve set extends back to ~2007 (DBC inception)"*. True of
    the 9-sleeve book, and wrong from the moment the Phase 39 international split
    added IQLT (2015-01-21): IQLT starts LATER than QUAL (2013-07-18), so
    dropping QUAL alone stopped unlocking anything, and DBC stopped being the
    binding kept leg — SCZ (2007-12-12) is.
    """
    end  = end_date or date.today().isoformat()
    skip = set(exclude or [])
    ret_dict: dict[str, pd.Series] = {}

    for sleeve_name, components in sleeve_benchmarks().items():
        if sleeve_name in skip:
            continue
        blended: pd.Series | None = None
        for ticker, weight in components:
            p = _price_series(ticker, start_date, end)
            if p.empty:
                blended = None
                break
            r = p.pct_change().dropna()
            blended = r * weight if blended is None else blended.add(r * weight, fill_value=0.0)
        if blended is not None and not blended.empty:
            ret_dict[sleeve_name] = blended

    if not ret_dict:
        return pd.DataFrame()

    df       = pd.DataFrame(ret_dict).dropna()
    non_zero = df.abs().sum(axis=1) > 0
    return df[non_zero]


# Sleeves whose benchmark series begins after this date constrain the common
# window short of the 2008 crisis; the Extended-history option drops them.
# 2010-01-01 sits in the empty decade between the crisis and the factor-ETF
# launches (QUAL 2013, IQLT 2015), so it separates them without naming either.
LATE_INCEPTION_CUTOFF = "2010-01-01"

# Requested start for inception probing. get_prices returns from the ticker's
# actual first trading day, so asking from 1990 yields true inception rather
# than the analysis window's start.
_HISTORY_PROBE_START = "1990-01-01"


def sleeve_first_dates(end_date: Optional[str] = None) -> dict[str, str]:
    """{sleeve -> ISO first date of its benchmark return series}, current book.

    The empirical inception of each sleeve's benchmark. Blended sleeves are
    summed with ``fill_value=0.0`` exactly as ``get_sleeve_returns`` does, so a
    blend's first date is its EARLIEST leg — the same convention
    pages/9_Correlations.py:_sleeve_first_dates() uses, so the two derivations
    cannot disagree about which sleeves are late.

    Sleeves whose series fails to load are omitted rather than guessed: a
    missing sleeve must not silently become a late one and get excluded.
    """
    end = end_date or date.today().isoformat()
    out: dict[str, str] = {}

    for sleeve_name, components in sleeve_benchmarks().items():
        blended: pd.Series | None = None
        for ticker, weight in components:
            p = _price_series(ticker, _HISTORY_PROBE_START, end)
            if p.empty:
                blended = None
                break
            r = p.pct_change().dropna()
            blended = r * weight if blended is None else blended.add(r * weight, fill_value=0.0)
        if blended is not None and not blended.empty:
            out[sleeve_name] = blended.index.min().date().isoformat()

    return out


def late_inception_sleeves(
    cutoff: str = LATE_INCEPTION_CUTOFF,
    firsts: Optional[dict[str, str]] = None,
) -> list[str]:
    """Sleeves starting after ``cutoff``, oldest first — the set to ``exclude``
    from ``get_sleeve_returns``/``average_pairwise_rolling_correlation`` to reach
    the 2008 crisis on WHATEVER book this process is pointed at.

    Derived, never a literal. A hardcoded ``["US Large Quality"]`` was correct
    for the 9-sleeve book and silently stopped working when the Phase 39
    international split added a younger benchmark (IQLT, 2015-01-21) — the
    exclusion still ran, still returned data, and simply stopped buying any
    history. Pass ``firsts`` to reuse an already-computed map (each call
    otherwise re-probes every benchmark from 1990).
    """
    f = sleeve_first_dates() if firsts is None else firsts
    return sorted((s for s, d in f.items() if d > cutoff), key=lambda s: f[s])


def align_to_equity_days(
    candidate: pd.Series,
    sleeves: pd.DataFrame,
) -> tuple[pd.Series, pd.DataFrame]:
    """
    Return (candidate, sleeves) restricted to dates present in both index sets.
    BTC trades 24/7; restricting to equity trading days makes correlations comparable.
    """
    common = candidate.index.intersection(sleeves.index)
    return candidate.reindex(common), sleeves.reindex(common)


# ── Univariate statistics ─────────────────────────────────────────────────────

def compute_univariate_stats(
    returns: pd.Series,
    rf_annual: float = RF_ANNUAL,
) -> dict[str, float]:
    """
    Annualized return, vol, Sharpe, max drawdown, skewness, and excess kurtosis.
    Annualization: geometric mean scaled to 252 trading days.
    """
    n = len(returns)
    if n < 2:
        return {}
    ann_ret  = float((1 + returns).prod() ** (TRADING_DAYS / n) - 1)
    ann_vol  = float(returns.std() * np.sqrt(TRADING_DAYS))
    sharpe   = (ann_ret - rf_annual) / ann_vol if ann_vol > 0 else 0.0
    cum      = (1 + returns).cumprod()
    roll_max = cum.cummax()
    max_dd   = float((cum / roll_max - 1).min())
    return {
        "ann_return":   ann_ret,
        "ann_vol":      ann_vol,
        "sharpe":       sharpe,
        "max_drawdown": max_dd,
        "skewness":     float(returns.skew()),
        "kurtosis":     float(returns.kurtosis()),   # excess kurtosis (normal = 0)
    }


def sample_risk_free(btc: pd.Series, sleeves: pd.DataFrame):
    """The sample's risk-free rate: the average 3-month Treasury bill rate from
    SAMPLE_START to the last day both return series reach (src.risk_free, audit item
    15a). A risk_free.RiskFreeRate; raises risk_free.RiskFreeUnavailable."""
    from src import risk_free
    end = min(pd.Timestamp(btc.index.max()), pd.Timestamp(sleeves.index.max()))
    return risk_free.rate_over(SAMPLE_START, end)


# What the univariate table sets beside each other: the candidate and four comparison
# assets. Its last row is the Real Assets sleeve's benchmark (real_assets_basket).
UNIVARIATE_TICKERS = ("BTC-USD", "SPY", "AGG", "GLD", "VOO")
REAL_ASSETS = "Real Assets"


def real_assets_basket() -> "list[tuple[str, float]]":
    """The univariate table's Real Assets row: the sleeve's own benchmark spec, legs and
    weights, read from the book (VNQ 60% and DBC 40% today). [] for a book with no such
    sleeve.

    The row was 50% VNQ + 50% PDBC, typed here, while the only definition a reader is
    given is the sleeve's benchmark, 60% VNQ + 40% DBC (#478 I13)."""
    return list(sleeve_benchmarks().get(REAL_ASSETS, []))


def tickers_read(candidate: str = "BTC-USD", screened: str = "") -> "list[str]":
    """Every ticker whose closes the page's figures are computed from: the case study's
    candidate, each sleeve's benchmark legs, the univariate table's assets and basket,
    the naive benchmark's two legs (the 2022 comparison), and ``screened``, the ticker
    in the candidate screen's box (QQQ until the reader types another), when it is one.
    The basket's legs are the Real Assets sleeve's, so they are among the sleeves'. The
    page's gap note reads this."""
    from src.benchmarks import NAIVE_60_40
    from src.prices import is_valid_ticker
    read = {candidate, *UNIVARIATE_TICKERS}
    if is_valid_ticker(screened):
        read.add(screened)
    read.update(t for t, _w in NAIVE_60_40)
    for legs in sleeve_benchmarks().values():
        read.update(t for t, _w in legs)
    return sorted(read)


def series_ends(candidate: str = "BTC-USD") -> "dict[str, str | None]":
    """{ticker: the date of its last stored close, ISO} for every ticker the section
    reads (tickers_read), or None for one with no close. Read through the same reader
    as the figures, so it is the last day each figure's series can reach."""
    today = date.today().isoformat()
    ends: "dict[str, str | None]" = {}
    for ticker in tickers_read(candidate):
        series = _price_series(ticker, SAMPLE_START, today)
        ends[ticker] = None if series.empty else series.index.max().date().isoformat()
    return ends


def through_line(ends: "dict[str, str | None]", start: str = SAMPLE_START) -> str:
    """The report section's first line: that it is live, and through when (#523).

    One through-date only when every series the section reads ends on it. The line
    gave the last day three joined series shared, as the section's through-date: on a
    book whose tickers end on different days some rows read later closes than it said
    (the univariate table runs each asset through its own last close). Otherwise it
    names each series' last date, the earliest first, and one with no close as that."""
    head = f"Live, not locked to the report's quarter: returns from {start}"
    dated = {t: d for t, d in ends.items() if d}
    missing = sorted(t for t, d in ends.items() if not d)
    if not ends:
        return f"{head}."
    if dated and not missing and len(set(dated.values())) == 1:
        return f"{head} through {next(iter(dated.values()))}."

    def _names(tickers: "list[str]") -> str:
        return tickers[0] if len(tickers) == 1 else ", ".join(tickers[:-1]) + " and " + tickers[-1]

    groups = [f"{_names(sorted(t for t, d in dated.items() if d == day))} through {day}"
              for day in sorted(set(dated.values()))]
    if missing:
        groups.append(f"{_names(missing)} with no close stored")
    return (f"{head}. Its series do not all end on one day: " + "; ".join(groups) + ". "
            f"A figure over the whole sample runs through the last day the series it "
            f"reads share.")


def price_gap_note(candidate: str = "BTC-USD", screened: str = "", *,
                   in_report: bool = False) -> "str | None":
    """What the page says when the book lacks a close inside the history of a ticker it
    reads, naming each ticker and date; None when it lacks none. With ``in_report`` it
    is the PDF section's sentence: the same tickers and dates, worded for a report,
    which does not change once it is generated (#521).

    Every series here is built from a ticker's stored closes with the close before a
    missing day carried forward, so a gap changes figures and nothing on the page showed
    it. For a ticker a quarter lock covers the Performance page steps back and names the
    close; no lock covers BTC-USD, the ticker this page is about (#501). The refresh
    asks for a missing close again every time it runs (src.price_gaps), so the note
    lasts only while the provider does not supply it."""
    from src import price_gaps
    from src.asof import format_long_date
    by_ticker: "dict[str, list[str]]" = {}
    for ticker, day in price_gaps.gaps_for(tickers_read(candidate, screened)):
        by_ticker.setdefault(ticker, []).append(format_long_date(day))
    if not by_ticker:
        return None
    named = "; ".join(f"{t} on {', '.join(days)}" for t, days in sorted(by_ticker.items()))
    one = sum(len(days) for days in by_ticker.values()) == 1
    it, it_is = ("it", "it is") if one else ("them", "they are")
    if in_report:
        return (f"No close is stored for {named}. The figures in this section that read "
                f"{it} carry the close before forward. Each price refresh asks for {it} "
                f"again, and a report generated once {it_is} stored carries no such note.")
    return (f"No close is stored for {named}. The figures on this page that read "
            f"{it} carry the close before forward. Each price "
            f"refresh asks for {it} again, and this note goes once "
            f"{it_is} stored.")


def build_univariate_table(
    start_date: str = SAMPLE_START,
    end_date: Optional[str] = None,
    rf_annual: float = RF_ANNUAL,
) -> pd.DataFrame:
    """
    Univariate stats table for BTC plus comparison assets.
    Rows: BTC, SPY, AGG, GLD, VOO, and Real Assets, the sleeve's benchmark as its spec
    weights it (real_assets_basket).
    Returns a DataFrame indexed by asset name.
    """
    end = end_date or date.today().isoformat()
    assets: dict[str, pd.Series] = {}

    for ticker in UNIVARIATE_TICKERS:
        p = _price_series(ticker, start_date, end)
        if not p.empty:
            assets[ticker.replace("-USD", "")] = p.pct_change().dropna()

    # Real Assets: the sleeve's benchmark legs on their common dates, weighted as the
    # spec weights them.
    legs = [(weight, _price_series(ticker, start_date, end))
            for ticker, weight in real_assets_basket()]
    if legs and not any(p.empty for _w, p in legs):
        common = legs[0][1].index
        for _w, p in legs[1:]:
            common = common.intersection(p.index)
        assets[REAL_ASSETS] = sum(
            p.reindex(common).pct_change() * weight for weight, p in legs).dropna()

    rows = {}
    for name, rets in assets.items():
        rows[name] = compute_univariate_stats(rets, rf_annual)
    return pd.DataFrame(rows).T


# ── Correlation analysis ──────────────────────────────────────────────────────

def compute_full_sample_correlations(
    candidate: pd.Series,
    sleeves: pd.DataFrame,
) -> pd.Series:
    """Pearson correlation of candidate vs each sleeve, full sample on common days."""
    cand_a, slv_a = align_to_equity_days(candidate, sleeves)
    if cand_a.empty:
        return pd.Series(dtype=float)
    return pd.Series({col: float(cand_a.corr(slv_a[col])) for col in slv_a.columns})


def compute_rolling_correlation(
    candidate: pd.Series,
    reference: pd.Series,
    window: int = 60,
) -> pd.Series:
    """Rolling window Pearson correlation on the common-date intersection."""
    common = candidate.index.intersection(reference.index)
    if len(common) < window:
        return pd.Series(dtype=float)
    return candidate.reindex(common).rolling(window).corr(reference.reindex(common)).dropna()


def compute_weekly_correlations(
    candidate: pd.Series,
    sleeves: pd.DataFrame,
) -> pd.Series:
    """
    Pearson correlation using weekly Friday-to-Friday returns.
    Resamples daily returns to weekly cumulative returns, then correlates.
    """
    cand_a, slv_a = align_to_equity_days(candidate, sleeves)
    if cand_a.empty:
        return pd.Series(dtype=float)

    def _weekly(r: pd.Series) -> pd.Series:
        return (1 + r).resample("W-FRI").prod() - 1

    cand_w  = _weekly(cand_a)
    result  = {}
    for col in slv_a.columns:
        slv_w  = _weekly(slv_a[col])
        common = cand_w.index.intersection(slv_w.index)
        result[col] = (float(cand_w.reindex(common).corr(slv_w.reindex(common)))
                       if len(common) >= 4 else float("nan"))
    return pd.Series(result)


# ── Mean-variance optimization ────────────────────────────────────────────────

def portfolio_sharpe_annual(
    weights: np.ndarray,
    mu: np.ndarray,
    cov: np.ndarray,
    rf_annual: float = RF_ANNUAL,
) -> float:
    """Annualized Sharpe ratio from daily sample mean / covariance."""
    port_ret = float(np.dot(weights, mu)) * TRADING_DAYS - rf_annual
    port_vol = float(np.sqrt(weights @ cov @ weights)) * np.sqrt(TRADING_DAYS)
    return port_ret / port_vol if port_vol > 1e-12 else 0.0


def solve_tangency_unconstrained(
    mu: np.ndarray,
    cov: np.ndarray,
    rf_annual: float = RF_ANNUAL,
) -> np.ndarray:
    """
    Closed-form unconstrained tangency portfolio.
    w* = Σ^{-1}(μ − rf*1) / [1'Σ^{-1}(μ − rf*1)].
    May produce negative weights (short positions allowed).
    Input mu and cov are daily; rf_annual is converted to daily internally via
    geometric compounding, (1+rf_annual)^(1/252) - 1 — matching
    src/performance.py's compute_risk_metrics convention.
    """
    rf_daily = (1 + rf_annual) ** (1 / TRADING_DAYS) - 1
    excess   = mu - rf_daily
    cov_inv  = np.linalg.pinv(cov)
    raw      = cov_inv @ excess
    total    = raw.sum()
    if abs(total) < 1e-10:
        return np.ones(len(mu)) / len(mu)
    return raw / total


def solve_tangency_constrained(
    mu: np.ndarray,
    cov: np.ndarray,
    rf_annual: float = RF_ANNUAL,
    max_wt: float = 0.10,
) -> np.ndarray:
    """
    Constrained tangency: 0 ≤ w_i ≤ max_wt, Σw = 1. Maximizes Sharpe.
    Uses scipy.optimize.minimize with SLSQP.
    """
    from scipy.optimize import minimize

    rf_daily = (1 + rf_annual) ** (1 / TRADING_DAYS) - 1
    n        = len(mu)

    def neg_sharpe(w: np.ndarray) -> float:
        port_ret = float(np.dot(w, mu)) - rf_daily
        port_vol = float(np.sqrt(w @ cov @ w))
        return -(port_ret / port_vol) if port_vol > 1e-12 else 0.0

    result = minimize(
        neg_sharpe,
        x0=np.ones(n) / n,
        method="SLSQP",
        bounds=[(0.0, max_wt)] * n,
        constraints=[{"type": "eq", "fun": lambda w: float(w.sum()) - 1.0}],
        options={"ftol": 1e-12, "maxiter": 2000},
    )
    w = np.clip(result.x, 0.0, max_wt)
    return w / w.sum()


def compute_mv_analysis(
    candidate_ret: pd.Series,
    sleeve_rets: pd.DataFrame,
    rf_annual: float = RF_ANNUAL,
    max_wt: float = 0.25,
) -> dict:
    """
    Full MV analysis: unconstrained and constrained tangency with/without candidate.

    Returns a dict with:
      sleeves               — ordered list of sleeve names (no BTC)
      w_unc_no, w_unc_with  — unconstrained tangency weights (9 sleeves / 10 incl. BTC)
      w_con_no, w_con_with  — constrained tangency weights
      sharpe_unc_no/with    — theoretical max-Sharpe: √((μ-rf)'Σ⁻¹(μ-rf)) × √252
      sharpe_con_no/with    — Sharpe of the constrained tangency portfolio
    """
    cand_a, slv_a  = align_to_equity_days(candidate_ret, sleeve_rets)
    sleeves_list   = list(slv_a.columns)

    mu_nb  = slv_a.mean().values
    cov_nb = slv_a.cov().values

    combined        = slv_a.copy()
    combined["BTC"] = cand_a
    mu_wb  = combined.mean().values
    cov_wb = combined.cov().values

    w_unc_nb = solve_tangency_unconstrained(mu_nb, cov_nb, rf_annual)
    w_con_nb = solve_tangency_constrained(mu_nb, cov_nb, rf_annual, max_wt)
    w_unc_wb = solve_tangency_unconstrained(mu_wb, cov_wb, rf_annual)
    w_con_wb = solve_tangency_constrained(mu_wb, cov_wb, rf_annual, max_wt)

    # Unconstrained Sharpe: √((μ-rf)'Σ⁻¹(μ-rf)) × √252 — non-negative by construction.
    # Using portfolio_sharpe_annual on normalized weights can produce negative Sharpe when
    # the normalization denominator (1'Σ⁻¹excess) is negative, flipping all weight signs.
    rf_daily   = (1 + rf_annual) ** (1 / TRADING_DAYS) - 1
    excess_nb  = mu_nb - rf_daily
    cov_inv_nb = np.linalg.pinv(cov_nb)
    sharpe_unc_no = float(
        np.sqrt(max(float(excess_nb @ cov_inv_nb @ excess_nb), 0.0)) * np.sqrt(TRADING_DAYS)
    )

    excess_wb  = mu_wb - rf_daily
    cov_inv_wb = np.linalg.pinv(cov_wb)
    sharpe_unc_with = float(
        np.sqrt(max(float(excess_wb @ cov_inv_wb @ excess_wb), 0.0)) * np.sqrt(TRADING_DAYS)
    )

    return {
        "sleeves":         sleeves_list,
        "w_unc_no":        w_unc_nb,
        "w_unc_with":      w_unc_wb,
        "w_con_no":        w_con_nb,
        "w_con_with":      w_con_wb,
        "sharpe_unc_no":   sharpe_unc_no,
        "sharpe_unc_with": sharpe_unc_with,
        "sharpe_con_no":   portfolio_sharpe_annual(w_con_nb, mu_nb, cov_nb, rf_annual),
        "sharpe_con_with": portfolio_sharpe_annual(w_con_wb, mu_wb, cov_wb, rf_annual),
        "mu_nb": mu_nb, "cov_nb": cov_nb,
        "mu_wb": mu_wb, "cov_wb": cov_wb,
    }


# ── Marginal Sharpe curve ─────────────────────────────────────────────────────

def compute_marginal_sharpe_curve(
    candidate_ret: pd.Series,
    sleeve_rets: pd.DataFrame,
    sleeve_weights: dict[str, float] | None = None,
    rf_annual: float = RF_ANNUAL,
    allocations: list[float] | None = None,
) -> pd.DataFrame:
    """
    Portfolio Sharpe at each candidate allocation.

    For each α, the remaining (1−α) is distributed proportionally across
    existing sleeves using sleeve_weights (defaults to the book's derived
    ex-cash MV weights).
    Returns DataFrame with columns: btc_alloc, sharpe.
    """
    if allocations is None:
        allocations = [0.00, 0.01, 0.02, 0.03, 0.05, 0.07, 0.10]
    if sleeve_weights is None:
        # The module function sleeve_weights() is shadowed by this parameter.
        sleeve_weights = _derive_sleeve_maps()[1]

    cand_a, slv_a = align_to_equity_days(candidate_ret, sleeve_rets)
    sleeves_list  = [s for s in slv_a.columns if s in sleeve_weights]
    raw_total     = sum(sleeve_weights[s] for s in sleeves_list)
    norm_sw       = {s: sleeve_weights[s] / raw_total for s in sleeves_list}

    combined       = slv_a[sleeves_list].copy()
    combined["BTC"] = cand_a
    mu  = combined.mean().values
    cov = combined.cov().values
    n   = len(sleeves_list)

    rows = []
    for alpha in allocations:
        w = np.array([norm_sw[s] * (1 - alpha) for s in sleeves_list] + [alpha])
        w = np.clip(w, 0, 1)
        w = w / w.sum()
        rows.append({"btc_alloc": alpha, "sharpe": portfolio_sharpe_annual(w, mu, cov, rf_annual)})

    return pd.DataFrame(rows)


# ── Drawdown sensitivity ──────────────────────────────────────────────────────

def _port_series(weights: np.ndarray, rets_df: pd.DataFrame, cols: list[str]) -> pd.Series:
    """Daily portfolio return series given weight vector and return DataFrame."""
    return (rets_df[cols].values @ weights)


def compute_drawdown_sensitivity(
    candidate_ret: pd.Series,
    sleeve_rets: pd.DataFrame,
    sleeve_weights: dict[str, float] | None = None,
    allocations: list[float] | None = None,
    rf_annual: float = RF_ANNUAL,
) -> pd.DataFrame:
    """
    Portfolio CAGR, max drawdown, and Sharpe at each candidate allocation.
    Also computes portfolio MDD during the 2022 stress period.
    """
    if allocations is None:
        allocations = [0.00, 0.02, 0.05, 0.10]
    if sleeve_weights is None:
        # The module function sleeve_weights() is shadowed by this parameter.
        sleeve_weights = _derive_sleeve_maps()[1]

    cand_a, slv_a = align_to_equity_days(candidate_ret, sleeve_rets)
    sleeves_list  = [s for s in slv_a.columns if s in sleeve_weights]
    raw_total     = sum(sleeve_weights[s] for s in sleeves_list)
    norm_sw       = {s: sleeve_weights[s] / raw_total for s in sleeves_list}

    combined = slv_a[sleeves_list].copy()
    combined["BTC"] = cand_a
    cols = sleeves_list + ["BTC"]

    stress = combined.loc["2022-01-01":"2022-12-31"]

    rows = []
    for alpha in allocations:
        w = np.array([norm_sw[s] * (1 - alpha) for s in sleeves_list] + [alpha])
        w = w / w.sum()

        dr   = _port_series(w, combined, cols)
        n    = len(dr)
        cagr = float((1 + dr).prod() ** (TRADING_DAYS / n) - 1) if n > 0 else 0.0
        vol  = float(dr.std() * np.sqrt(TRADING_DAYS))
        shr  = (cagr - rf_annual) / vol if vol > 0 else 0.0
        cum  = (1 + dr).cumprod()
        mdd  = float((cum / np.maximum.accumulate(cum) - 1).min()) if n > 0 else 0.0

        mdd22 = float("nan")
        if len(stress) > 1:
            dr22  = _port_series(w, stress, cols)
            cum22 = (1 + dr22).cumprod()
            mdd22 = float((cum22 / np.maximum.accumulate(cum22) - 1).min())

        rows.append({
            "BTC Alloc": f"{alpha:.1%}",
            "CAGR":       cagr,
            "Max DD":     mdd,
            "Sharpe":     shr,
            "2022 MDD":   mdd22,
        })

    return pd.DataFrame(rows)


# ── Regime-conditional correlation ────────────────────────────────────────────

def _build_regime_series(start_date: str, end_date: str) -> pd.Series:
    """
    Vectorized daily regime label series from FRED monthly/daily data.
    Returns a Series indexed by pd.Timestamp with string regime labels.
    """
    # Past FRED's wait (#435) a series is its stored copy, and its fetch date rides on
    # the result (attrs["fred_stored"]) so the page can say which copies it used.
    stored: dict[str, str] = {}

    def _read(series_id: str, start: str) -> pd.Series:
        try:
            return get_series(series_id, start_date=start).dropna()
        except FREDStillFetching as exc:
            if exc.stored is None:
                raise
            stored[series_id] = exc.stored_on
            return exc.stored.dropna()

    try:
        usrec  = _read("USREC",  "1945-01-01")
        t10y2y = _read("T10Y2Y", "1976-06-01")
        unrate = _read("UNRATE", "1948-01-01")
    except Exception:
        return pd.Series(dtype=str)

    dr = pd.date_range(start=start_date, end=end_date, freq="D")
    df = pd.DataFrame({
        "usrec":  usrec.reindex(dr).ffill().bfill(),
        "t10y2y": t10y2y.reindex(dr).ffill().bfill(),
        "unrate": unrate.reindex(dr).ffill().bfill(),
    }, index=dr)

    # Vectorized classify_regime — same priority order as macro.classify_regime
    labels = pd.Series("Mid-cycle", index=dr, dtype=str)

    curve_inv   = df["t10y2y"].notna() & (df["t10y2y"] < -0.25)
    labor_tight = df["unrate"].notna() & (df["unrate"] < 4.2)
    labels[curve_inv | labor_tight] = "Late-cycle"

    unrate_high = df["unrate"].notna() & (df["unrate"] > 5.5)
    curve_ok    = df["t10y2y"].isna()  | (df["t10y2y"] > -0.25)
    labels[unrate_high & curve_ok] = "Early-cycle"

    labels[df["usrec"].notna() & (df["usrec"] >= 0.5)] = "Recession"

    labels.attrs["fred_stored"] = stored
    return labels


def compute_regime_conditional_correlation(
    candidate_ret: pd.Series,
    reference_ret: pd.Series,
    start_date: str = SAMPLE_START,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """
    BTC-vs-reference correlation conditional on macro regime.
    Returns DataFrame with columns: Regime, Correlation, N Obs.
    """
    end    = end_date or date.today().isoformat()
    common = candidate_ret.index.intersection(reference_ret.index)
    cand   = candidate_ret.reindex(common)
    ref    = reference_ret.reindex(common)

    regimes = _build_regime_series(start_date, end)
    if regimes.empty:
        return pd.DataFrame(columns=["Regime", "Correlation", "N Obs"])
    stored = dict(regimes.attrs.get("fred_stored", {}))

    reg_aligned = regimes.reindex(common).dropna()
    cand_r = cand.reindex(reg_aligned.index)
    ref_r  = ref.reindex(reg_aligned.index)

    rows = []
    for label in ("Recession", "Early-cycle", "Mid-cycle", "Late-cycle"):
        mask = reg_aligned == label
        n    = int(mask.sum())
        corr = float(cand_r[mask].corr(ref_r[mask])) if n >= 10 else float("nan")
        rows.append({"Regime": label, "Correlation": corr, "N Obs": n})

    out = pd.DataFrame(rows)
    out.attrs["fred_stored"] = stored
    return out


# ── Aggregate rolling correlation over time (Phase 31) ─────────────────────────
#
# These operate on any returns DataFrame, so Phase 32 can reuse rolling_pairwise_*
# by passing a frame that includes a candidate column alongside the sleeves.

# Sleeve-name groups for the equity-vs-bond-equity decomposition. Real Assets is
# intentionally in neither group (a diversifier that is neither equity nor bond).
BOND_SLEEVES = ["Core Fixed Income", "TIPS"]


def equity_sleeve_names() -> list[str]:
    """Equity sleeves of the CURRENT book, resolved at call time.

    Derived from the book's own sleeve set rather than hardcoded: the old
    frozen list carried the personal book's names, so on the demo book the
    four international sleeves silently vanished from the intra-equity line
    (the `c in cols` filter dropped them without error). Call-time (not
    import-time) so importing this module never opens the database and the
    set tracks the phase-46 split within a warm process. Distinct on purpose
    from household.equity_sleeves, which returns household sleeve *category
    keys* — this returns display names for the correlation views."""
    return [s for s in sleeve_names() if s not in BOND_SLEEVES and s != "Real Assets"]


def _ordinal(n: float) -> str:
    """Format a percentile as an ordinal, e.g. 58 -> '58th'."""
    n = int(round(n))
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th', 'st', 'nd', 'rd', 'th'][min(n % 10, 4)]}"


def _pairwise_rolling_frame(returns_df: pd.DataFrame, window: int) -> pd.DataFrame:
    """
    DataFrame of every off-diagonal pairwise rolling correlation, one column per
    unordered pair ("A × B"), indexed like ``returns_df``. Generic over columns.
    """
    cols = list(returns_df.columns)
    data: dict[str, pd.Series] = {}
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            a, b = cols[i], cols[j]
            data[f"{a} × {b}"] = returns_df[a].rolling(window).corr(returns_df[b])
    return pd.DataFrame(data, index=returns_df.index)


def rolling_pairwise_mean(returns_df: pd.DataFrame, window: int) -> pd.Series:
    """
    Per date, the mean of all off-diagonal pairwise rolling correlations across
    the columns of ``returns_df`` over the trailing ``window``.

    Generic — works for any number of columns (N >= 2). Phase 32 reuses this by
    passing a returns frame that includes a candidate column to obtain the
    candidate's average rolling correlation with the rest.
    """
    pf = _pairwise_rolling_frame(returns_df, window)
    if pf.empty:
        return pd.Series(dtype=float)
    return pf.mean(axis=1, skipna=True).dropna().rename("avg_pairwise_corr")


def rolling_pairwise_band(returns_df: pd.DataFrame, window: int) -> pd.DataFrame:
    """
    Per date, the min and max of the off-diagonal pairwise rolling correlations —
    the dispersion the mean hides. Columns: ``min``, ``max``.
    """
    pf = _pairwise_rolling_frame(returns_df, window)
    if pf.empty:
        return pd.DataFrame(columns=["min", "max"])
    out = pd.DataFrame({
        "min": pf.min(axis=1, skipna=True),
        "max": pf.max(axis=1, skipna=True),
    })
    return out.dropna(how="all")


def average_pairwise_rolling_correlation(
    window: int = 60,
    start_date: str = "2007-01-01",
    end_date: Optional[str] = None,
    exclude: Optional[list[str]] = None,
) -> pd.DataFrame:
    """
    Sleeve-level aggregate: the mean (and min/max band) of all pairwise rolling
    correlations across the SAA sleeves over time.

    ``exclude`` is forwarded to ``get_sleeve_returns``; pass
    ``late_inception_sleeves()`` to reach the 2008 crisis on whatever book this
    is. Naming a sleeve literally here is exactly what went stale — see that
    function. Returns a DataFrame with columns ``mean``, ``min``, ``max`` (empty
    if data is unavailable).
    """
    returns = get_sleeve_returns(start_date=start_date, end_date=end_date, exclude=exclude)
    if returns.empty or returns.shape[1] < 2:
        return pd.DataFrame(columns=["mean", "min", "max"])
    mean = rolling_pairwise_mean(returns, window).rename("mean")
    band = rolling_pairwise_band(returns, window)
    out = pd.concat([mean, band], axis=1).dropna(subset=["mean"])
    return out


def rolling_equity_vs_bondequity(returns_df: pd.DataFrame, window: int) -> pd.DataFrame:
    """
    Two decomposed rolling-correlation lines over time:
      - ``intra_equity``: mean pairwise correlation AMONG the equity sleeves.
      - ``bond_equity``:  mean correlation BETWEEN the bond sleeves
        (Core Fixed Income, TIPS) and the equity sleeves.

    Sleeves are classified by name via equity_sleeve_names() / BOND_SLEEVES;
    Real Assets is excluded from both groups. Columns absent from ``returns_df``
    are skipped.
    """
    cols = list(returns_df.columns)
    eq = [c for c in equity_sleeve_names() if c in cols]
    bd = [c for c in BOND_SLEEVES if c in cols]

    intra_eq = (
        rolling_pairwise_mean(returns_df[eq], window)
        if len(eq) >= 2 else pd.Series(dtype=float)
    )

    be_pairs: dict[str, pd.Series] = {}
    for b in bd:
        for e in eq:
            be_pairs[f"{b} × {e}"] = returns_df[b].rolling(window).corr(returns_df[e])
    bond_eq = (
        pd.DataFrame(be_pairs, index=returns_df.index).mean(axis=1, skipna=True)
        if be_pairs else pd.Series(dtype=float)
    )

    out = pd.DataFrame({"intra_equity": intra_eq, "bond_equity": bond_eq})
    return out.dropna(how="all")


def interpret_rolling_correlation(current_value: float, history_series: pd.Series) -> str:
    """
    Banded interpretation of the average pairwise sleeve correlation, in the
    macro/Factor-Regime voice.

    Reports the current average correlation, its percentile within its own
    history (reusing ``macro.percentile``), and frames diversification as
    strong / moderate / compressed by the absolute current level. Distinguishes
    the equity sleeves' convergence toward +1 in stress from the bond-damped
    blended average, and notes the non-stationary caveat.
    """
    from src.macro import percentile as _macro_percentile

    hist = history_series.dropna()
    # NO INLINE EMPTY GUARD (#245). This line used to read
    #     _macro_percentile(hist, current_value) if not hist.empty else 50.0
    # which guarded emptiness ITSELF and supplied the median rank inline, so it could
    # never reach macro.percentile's empty branch — whatever that function returned,
    # this site was untouched by it. Its own neighbour one line down already degraded
    # honestly under the identical test, which is the tell: someone reached the honest
    # answer on one line and not on the one above it.
    pct  = _macro_percentile(hist, current_value)      # None on empty, by contract

    if current_value >= 0.65:
        level = "compressed: the sleeves are moving largely together and offer little offset right now"
    elif current_value >= 0.40:
        level = "moderate: the sleeves share meaningful common market beta but retain some offset"
    else:
        level = "strong: average co-movement is low and the sleeves diversify one another"

    # The level band is derived from current_value, which is KNOWN, so it stays
    # truthful either way. Only the historical rank is withheld.
    #
    # `start_year` used to be computed here with its own `else "the sample start"`
    # fallback — the honest neighbour that made #245 legible, since it reached the
    # right answer under the same guard the line above fabricated under. That
    # fallback is now UNREACHABLE and has been removed rather than left: pct is None
    # exactly when hist is empty (macro.percentile returns None only on an empty
    # series, and hist is already dropna'd), so the two conditions coincide and the
    # no-history clause below subsumes what "since the sample start" used to admit.
    # Keeping a dead honest fallback beside a deleted dishonest one would leave the
    # same latency this change exists to remove.
    rank = (f"the {_ordinal(pct)} percentile of its history since "
            f"{hist.index.min().year}"
            if pct is not None else
            "with no history available to rank it against")

    return (
        f"Average pairwise sleeve correlation is currently ρ ≈ {current_value:+.2f}, "
        f"{rank}. Cross-sleeve "
        f"diversification is {level}. In stress episodes the equity sleeves converge toward "
        "+1, and diversification among equity styles evaporates in a crash. The blended average "
        "stays lower only because the bond sleeves decouple from equities, which is the "
        "diversification the fixed-income allocation is there to provide. Correlations are "
        "non-stationary and tend to rise when diversification is most needed."
    )


# ── Candidate-to-sleeves correlation (Phase 32) ────────────────────────────────
#
# The right metric for "how correlated is a candidate to my sleeves" is the mean
# of the candidate's correlation ROW — NOT rolling_pairwise_mean on an augmented
# frame (which averages all pairs, in which the candidate is a small minority and
# barely moves the result). Full-sample average is
# compute_full_sample_correlations(candidate, sleeves).mean(); the rolling version
# is rolling_correlation_to_set below.

def rolling_correlation_to_set(
    candidate_returns: pd.Series,
    references_df: pd.DataFrame,
    window: int = 60,
) -> pd.Series:
    """
    Per date, the mean over the reference columns of the rolling correlation
    between ``candidate_returns`` and each reference, over the trailing ``window``.

    This is the candidate's average rolling correlation to the reference set — the
    mean of its correlation row, computed on the candidate/reference common-date
    intersection. Generic over the number of reference columns. NOT the same as
    ``rolling_pairwise_mean`` (which averages all pairs, including reference-vs-
    reference, and so does not isolate the candidate).
    """
    common = candidate_returns.index.intersection(references_df.index)
    if len(common) < window or references_df.shape[1] == 0:
        return pd.Series(dtype=float)
    cand = candidate_returns.reindex(common)
    refs = references_df.reindex(common)
    per_ref = [cand.rolling(window).corr(refs[c]) for c in refs.columns]
    mat = pd.concat(per_ref, axis=1)
    return mat.mean(axis=1, skipna=True).dropna().rename("avg_corr_to_set")


def interpret_candidate_diversification(
    avg_corr: float,
    per_sleeve: pd.Series,
    ticker: str = "The candidate",
) -> str:
    """
    "Diversifies vs doubles down" verdict for a candidate, in the page's 5j voice.

    Classifies by BOTH the average correlation to the sleeves and the bond-sleeve
    correlations (Core Fixed Income, TIPS): a high average with near-zero bond
    correlation reads as an equity double-down with some duration offset; a high
    average including the bonds reads as broadly redundant; a low average reads as
    a genuine diversifier. Names the highest-correlation sleeves (what it doubles
    down on) and any genuinely low/negative ones (the real offsets).

    Thresholds: avg ≥ 0.60 → doubles down; avg < 0.30 → genuine diversifier;
    in between → mixed. Bond split at 0.20.
    """
    ps = per_sleeve.dropna().sort_values(ascending=False)
    top = ps.head(2)
    top_str = " and ".join(f"ρ ≈ {v:+.2f} with {s}" for s, v in top.items())

    offsets = ps[ps < 0.15].sort_values().head(3)
    if not offsets.empty:
        from src.prose_helpers import _list
        off_str = _list([f"{s} ({v:+.2f})" for s, v in offsets.items()])
        if avg_corr < 0.30:
            offset_clause = f"It correlates least with {off_str}. "
        else:
            offset_clause = f"Offset comes only from {off_str}. "
    else:
        offset_clause = "No sleeve offers a low or negative correlation. "

    bond_vals = [
        float(per_sleeve[b]) for b in BOND_SLEEVES
        if b in per_sleeve.index and pd.notna(per_sleeve[b])
    ]
    avg_bond = float(np.mean(bond_vals)) if bond_vals else float("nan")

    if avg_corr >= 0.60:
        if not np.isnan(avg_bond) and avg_bond < 0.20:
            verdict = (
                "largely doubles down on the existing equity beta, with some duration "
                "offset from the bond sleeves"
            )
            tail = (
                "It adds little as a diversifier and largely duplicates the exposure "
                "the existing SAA equity sleeves already carry."
            )
        else:
            verdict = (
                "is redundant across the board: it co-moves even with the bond sleeves and "
                "adds little diversification"
            )
            tail = (
                "It overlaps the existing SAA broadly rather than filling a correlation gap."
            )
    elif avg_corr < 0.30:
        verdict = "is a genuine diversifier: its co-movement with the existing sleeves is low"
        tail = (
            "It is additive: it fills a correlation gap the current SAA sleeves do not cover."
        )
    else:
        # "But meaningful offset elsewhere" printed beside "No sleeve offers a low or
        # negative correlation" when no sleeve fell below the offset cut (#425). The offset
        # is claimed only when one was found.
        verdict = ("is a mixed case: partial overlap with the equity sleeves"
                   + (" and some offset elsewhere" if not offsets.empty else ""))
        tail = (
            "It is neither a pure double-down nor a clean diversifier; the case turns on "
            "the specific sleeve overlaps set against the offsets."
            if not offsets.empty else
            "It is neither a pure double-down nor a clean diversifier."
        )

    return (
        f"{ticker}'s average correlation to the SAA sleeves is ρ ≈ {avg_corr:+.2f}, driven by "
        f"{top_str}. It {verdict}. {offset_clause}{tail}"
    )


def return_in_year(returns, year: int = 2022) -> float:
    """A daily return series' calendar-year return: every return dated in ``year``
    compounded, as a signed fraction. NaN when the series has no returns in it.

    The Asset Evaluation page states 2022 once, as each series' calendar-year return
    (#425, decided by the owner on October 15, 2026), and the PDF's Drawdown Sensitivity
    note does the same since #604. The page printed bitcoin's 2022 fall three times in
    two signs: a peak-to-trough decline within the year in 5h's two paragraphs, and the
    same figure signed, its 2022 drawdown, in 5j's bullet."""
    s = returns.loc[f"{year}-01-01":f"{year}-12-31"].dropna()
    if s.empty:
        return float("nan")
    return float((1 + s).prod() - 1)


def year_returns_sentence(returns: "dict[str, float]", year: int = 2022) -> "str | None":
    """ "Over calendar 2022, SPY returned -18.2%, AGG returned -13.0% and BTC
    returned -64.3%", from return_in_year's figures by name, in the order given. A
    figure that prints as 0.0% reads "was flat". None when any figure is missing: the
    sentence states all three or none (#425)."""
    if not returns or not all(np.isfinite(v) for v in returns.values()):
        return None
    parts = [f"{name} was flat" if f"{abs(v):.1%}" == "0.0%" else f"{name} returned {v:.1%}"
             for name, v in returns.items()]
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"Over calendar {year}, {joined}"


def stress_2022_sentences(returns: "dict[str, float]", year: int = 2022) -> str:
    """What the Drawdown Sensitivity paragraph says of 2022 after its opening: the
    year-returns sentence, and the joint-stress reading only when every series fell
    (#425). It said "all at the same time. That eliminated any diversification benefit
    and amplified drawdown" whatever the figures were. A figure that prints as flat did
    not fall. Empty when a figure is missing."""
    sentence = year_returns_sentence(returns, year)
    if sentence is None:
        return ""
    if not all(v < 0 and f"{abs(v):.1%}" != "0.0%" for v in returns.values()):
        return f"{sentence}."
    every = "all three" if len(returns) == 3 else "all"
    return (f"{sentence}: {every} fell in the same year. This joint stress scenario, not "
            "the full-sample average correlation, is the relevant measure of risk.")


def max_drawdown(returns) -> float:
    """Worst peak-to-trough loss of a daily return series, as a NEGATIVE fraction.

    Signed, and deliberately so: every drawdown figure in this section's table is
    signed, and the decision-framework sentence sets one against another. A
    magnitude here would read as the same quantity as the table's while carrying
    the opposite sign convention, which is the confusion #276 exists to remove.

    NaN on an empty series rather than 0.0 — a zero drawdown is a real and very
    different claim from an unmeasured one.
    """
    s = returns.dropna()
    if s.empty:
        return float("nan")
    cum = (1 + s).cumprod()
    return float((cum / np.maximum.accumulate(cum) - 1).min())


def drawdown_verb(mdd_without: float, mdd_with: float) -> str:
    """What a BTC allocation did to the portfolio's 2022 drawdown, from the table's two
    figures: its 2022 MDD at no allocation and at the largest one (#529). Drawdowns
    are negative, so a lower figure is a deeper one. The texts said "deepened"
    whatever the two figures were."""
    if mdd_with < mdd_without:
        return "deepened"
    if mdd_with > mdd_without:
        return "cushioned"
    return "left unchanged"


def drawdown_2022_note(returns: "dict[str, float] | None", port: "dict | None") -> str:
    """The sentences that close the PDF's Drawdown Sensitivity note: 2022 stated once, as
    each series' calendar-year return read from the book, then what adding BTC did to the
    portfolio's 2022 drawdown, read from the table's two figures (#529).

    #604, decided by the owner on October 15, 2026, as #425 decided for the page: the
    note said "That year equities and bonds fell together while Bitcoin fell 66.9%", a
    peak-to-trough fall, and the case-against bullet gave the same fall again, signed.
    The returns are the page's 5h sentence (stress_2022_sentences), so a figure that
    prints as 0.0% reads "was flat" and a missing one drops the sentence. ``returns``
    maps the naive benchmark's two legs and "BTC" to return_in_year's figures. ``port``
    is the dict drawdown_argument takes; without it the direction is not stated, because
    nothing measured it."""
    year = stress_2022_sentences(returns or {})
    lead = f"{year} " if year else ""
    if not port:
        return (f"{lead}What a BTC allocation did to the portfolio's 2022 drawdown is not "
                f"available this run.")
    verb = drawdown_verb(port["mdd22_lo"], port["mdd22_hi"])
    moved = (f"from {port['mdd22_lo']:.1%} at 0% allocation to {port['mdd22_hi']:.1%} at "
             f"{port['alloc_top']}")
    if verb == "deepened":
        return (f"{lead}Adding BTC deepened the portfolio's 2022 drawdown, {moved}, instead "
                f"of cushioning it. 2022 is the critical test: diversification value eroded "
                f"in the year it was most needed.")
    if verb == "cushioned":
        return (f"{lead}Adding BTC cushioned the portfolio's 2022 drawdown, {moved}. 2022 is "
                f"the critical test, and on these figures the allocation did not deepen it.")
    return (f"{lead}Adding BTC left the portfolio's 2022 drawdown unchanged at "
            f"{port['mdd22_lo']:.1%}. 2022 is the critical test.")


def drawdown_argument(btc_mdd: float, port: dict, sample_start: str) -> str:
    """The case-against drawdown bullet, in one of TWO forms (#276).

    The figure was never the defect. ">80%" was TRUE — -81.5% over this report's
    own sample — and stale-proofing it by derivation alone would have fixed a
    correct number and left the confusion untouched, because what it never said is
    WHOSE drawdown. It renders directly under a table whose Max DD column is the
    PORTFOLIO's and four times smaller, so the two read as a contradiction.

    FULL form when the sweep ran: names Bitcoin's own figure, names the table's as
    something else, and sizes what a BTC allocation did to 2022. It gives no 2022 figure
    of Bitcoin's own: the page and the PDF state 2022 once, as calendar-year returns
    (#425, #604). It added "Its 2022 drawdown of -66.9% came while equities and bonds fell
    together", the PDF's note having just said how far Bitcoin fell.

    REDUCED form when 5h failed: Bitcoin's own figure still renders (btc_ret is
    guaranteed past the loader guard), and the missing portfolio contrast is
    declared ABSENT. That last clause is load-bearing — a contrast that quietly
    vanishes reads as no contrast, i.e. as though the allocation cost nothing.
    """
    head = (f"Bitcoin's own maximum drawdown is {btc_mdd:.1%} over "
            f"{sample_start}–present")
    if port:
        # The verb is the table's two figures' (#529): it read "deepened" whatever
        # they were, and closed "with no diversification benefit" either way.
        verb = drawdown_verb(port["mdd22_lo"], port["mdd22_hi"])
        opening = (f"{head}, not the {port['max_dd']:.1%} in the table above, which is "
                   f"the portfolio's. ")
        if verb == "left unchanged":
            return (f"{opening}It left the portfolio's own 2022 drawdown unchanged at "
                    f"{port['mdd22_lo']:.1%}, from 0% allocation to {port['alloc_top']}")
        moved = (f"It {verb} the portfolio's own 2022 drawdown "
                 f"from {port['mdd22_lo']:.1%} at 0% allocation to {port['mdd22_hi']:.1%} "
                 f"at {port['alloc_top']}")
        if verb == "deepened":
            return (f"{opening}{moved}, with no diversification benefit when it was most "
                    f"needed")
        return f"{opening}{moved}, so on this measure it did not deepen the year's loss"
    return (
        f"{head}. The portfolio-level comparison (what a BTC allocation did to "
        f"the 2022 drawdown) is not available this run: the drawdown "
        f"sweep did not produce data. Absent, not zero"
    )
