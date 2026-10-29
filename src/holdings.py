"""Derive holdings and portfolio value from the trades ledger.

Every trade-derived read is SCOPED to an explicit account. The trade-derived
"portfolio" is one account — the self-directed taxable book — and reading across
all accounts (the pre-Part-B defect) silently folds a Roth or IRA trade into a
figure labelled "Portfolio". The base reads therefore REQUIRE a keyword-only
``account_id`` and raise on None; the account identity itself is resolved in
exactly one place, ``get_portfolio_account()``, which raises rather than guessing.
"""
from dataclasses import replace
from datetime import date, timedelta
from typing import NamedTuple, Optional

import pandas as pd

from src.coverage import (SPLIT_UNADJUSTED, PriceCoverage, TickerStatus, UnpricedHolding,
                          coverage_from_statuses, split_detail, unpriced_reason)
from src.db import get_connection
from src.prices import (get_prices, get_dividends, classify_miss, _to_iso,
                        total_return_series, gap_fetch_failure_for, unread_dividends_for)


# Distinguishes "argument omitted" from an explicit None meaning "this caller has
# decided None is the right answer". Same pattern, same reasoning, as
# location_actions._UNSET and asof._UNSET.
_UNSET = object()


class SleeveWeights(NamedTuple):
    """A sleeve-weights frame and the coverage record for the prices behind it.

    A NamedTuple rather than a frame attribute, deliberately: coverage travels as
    an explicit return value that a caller must destructure, so no pandas
    operation can drop it and no ``.get(key, default)`` can quietly substitute a
    different measurement for it. See the module docstring in src/coverage.py.
    """

    frame: pd.DataFrame
    coverage: PriceCoverage


class MarketValue(NamedTuple):
    """A market-value figure and the coverage record for the prices behind it."""

    value: float
    coverage: PriceCoverage


class ValueSeries(NamedTuple):
    """A daily value series and the coverage record for the prices behind it."""

    series: pd.Series
    coverage: PriceCoverage


class UnpricedHoldingError(Exception):
    """The portfolio's value series was asked for over a window in which a held
    position has no price on a date it was held (#639, #643, decided by the owner on
    October 17, 2026).

    Fail closed. The series used to count such a holding as $0 on those dates, so every
    return over the window was a return on a different book, and the day its price
    arrived booked the whole position as a gain. ``unpriced`` holds each holding and its
    dates (coverage.UnpricedHolding), and ``reason`` says them in words, for the
    reader's not_computed_sentence (reports, #248). Not a ValueError, so no handler
    written for a bad input reads it as one."""

    def __init__(self, unpriced: "tuple[UnpricedHolding, ...]"):
        self.unpriced = tuple(unpriced)
        self.reason = unpriced_reason(self.unpriced)
        super().__init__(self.reason)


_LOOK_BACK_DAYS = 7


def look_back_start(price_ticker: str, date_str: str) -> str:
    """Start of the price look-back for ``price_ticker`` on ``date_str`` (#302).

    Looks back from the NEWEST STORED price on or before ``date_str``, not from
    ``date_str`` itself. From ``date_str`` alone, a cache more than a week behind the
    calendar left the window empty whenever the gap could not be fetched (offline, or
    a failed fetch), so every holding went unresolved and the page rendered "No
    committed price data" for a book that HAS prices, just older ones. From the newest
    stored price, get_prices returns those stored rows and fetches the gap when it can,
    and the coverage record's frontier_served says how old what was served is, so the
    banner discloses the lag instead of the page going empty.

    Unchanged for a historical ``date_str`` (the newest stored price on or before it is
    within the week) and for a book with no stored price for this ticker at all.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT MAX(price_date) FROM prices WHERE ticker = ? AND price_date <= ?",
            (price_ticker, date_str),
        ).fetchone()
    anchor = row[0] if row and row[0] else date_str
    return (date.fromisoformat(min(anchor, date_str))
            - timedelta(days=_LOOK_BACK_DAYS)).isoformat()


def split_crossing(ticker: str, account_id: int) -> "tuple[str, float] | None":
    """(ex-date, ratio) of the first split the book has recorded for ``ticker`` that went
    ex after the account's first trade in it, or None.

    Such a position's share counts are on the old scale while every stored close is on
    the new one, so it is not valued (decided by the owner on October 18, 2026: handling
    the share counts is a later decision). A split that predates every trade in the
    position changes nothing, since no share count crosses it: SCHP's 2024 split and the
    demo's and the personal book's SCHP trades, all after it. A trade on the ex-date
    itself is at the new scale."""
    from src.prices import recorded_splits
    splits = recorded_splits(ticker)
    if not splits:
        return None
    with get_connection() as conn:
        first = conn.execute("SELECT MIN(trade_date) FROM trades WHERE ticker = ? AND "
                             "account_id = ?", (ticker, account_id)).fetchone()[0]
    if first is None:
        return None
    return next(((ex, ratio) for ex, ratio in splits if ex > first), None)


def _price_and_status(
    ticker: str,
    start: str,
    end: str,
    *,
    price_ticker: Optional[str] = None,
    account_id: Optional[int] = None,
) -> "tuple[pd.DataFrame, TickerStatus]":
    """One holding's price frame and what happened getting it.

    Calls the module-level ``get_prices``, so a caller that monkeypatches it (the
    DRIP tests do) still intercepts the fetch — there is exactly one fetch path,
    and this adds only the record of its outcome. The REASON for a miss comes from
    ``prices.classify_miss``, which consults the cache: that distinction is not
    available here, which is why coverage is two-layer.

    ``price_ticker`` covers SPAXX, valued off BIL. The status is recorded under the
    HOLDING's ticker; that the instrument differs is a substitution, and reporting
    it belongs to the PR that populates that field.

    ``account_id`` names the account holding the position. With it, a position whose
    ticker split after the account's first trade in it is unresolved, its reason
    ``split_unadjusted`` and its detail the split (split_crossing), and nothing is
    priced. Not inside a quarter lock: a lock serves the closes it holds, on the scale
    its share counts were valued at, and nothing locked moves.
    """
    from src import prices as _prices
    if account_id is not None and _prices._PRICE_LOCK.get() is None:
        crossing = split_crossing(ticker, account_id)
        if crossing is not None:
            return (
                pd.DataFrame({"close": pd.Series(dtype=float),
                              "adj_close": pd.Series(dtype=float)}),
                TickerStatus(ticker, False, reason=SPLIT_UNADJUSTED,
                             detail=split_detail(*crossing)),
            )
    fetch = price_ticker or ticker
    try:
        frame = get_prices(fetch, start, end)
    except Exception as exc:                                     # noqa: BLE001
        return (
            pd.DataFrame({"close": pd.Series(dtype=float),
                          "adj_close": pd.Series(dtype=float)}),
            TickerStatus(ticker, False,
                         reason=classify_miss(fetch, start, end, error=exc),
                         error=exc),
        )
    if frame.empty:
        return frame, TickerStatus(
            ticker, False, reason=classify_miss(fetch, start, end))
    try:
        served = max(_to_iso(d) for d in frame.index)
    except Exception:                                            # noqa: BLE001
        served = None
    # Served, and not ok, when the gap fetch for this window failed (#637), or when an
    # update sent a dividend in the window that could not be read (#642): that update
    # is not stored, so the frame stops where the store does (#658), and the status
    # names the event.
    failed = gap_fetch_failure_for(fetch, start, end)
    unread = "; ".join(u.words() for u in unread_dividends_for(fetch, start, end))
    return frame, TickerStatus(ticker, True, served_through=served,
                               fetch_failure=failed.cause if failed else None,
                               unread_dividends=unread or None)


def _first_session_on_or_after(day: str) -> str:
    """The first NYSE session on or after ``day``, by demo_refresh.is_session."""
    from src.demo_refresh import is_session
    d = date.fromisoformat(day)
    while not is_session(d):
        d += timedelta(days=1)
    return d.isoformat()


def _bil_for_spaxx(start: str, end: str) -> "tuple[pd.DataFrame, TickerStatus]":
    """BIL's frame for SPAXX's growth from ``start``, and SPAXX's status (#639).

    SPAXX's income is modeled at BIL's total return measured from ``start``, the
    inception of the book being valued at every site (#646). A BIL series starting
    later would measure it from a later base and understate it with nothing saying
    so. So BIL must hold the close of the first session on or after ``start``. When it does not, the frame comes back empty and SPAXX unresolved:
    ``fetch_failed`` when the fetch for the missing stretch failed
    (prices.gap_fetch_failure_for), ``no_cached_rows`` when the stretch is not stored
    and was not fetched. Each caller then values SPAXX as it already values an
    unresolved SPAXX."""
    frame, st = _price_and_status("SPAXX", start, end, price_ticker="BIL")
    if not st.resolved or frame.empty:
        return frame, st
    first = min(_to_iso(d) for d in frame.index)
    if first <= _first_session_on_or_after(start):
        return frame, st
    before = (date.fromisoformat(first) - timedelta(days=1)).isoformat()
    failed = gap_fetch_failure_for("BIL", start, before)
    return frame.iloc[0:0], TickerStatus(
        "SPAXX", False, reason="fetch_failed" if failed else "no_cached_rows")


# Why the lookup below finds nothing on a book with no trade logged, in its own words.
NO_TRADE_LEDGER = "no active self-directed taxable account carries a trade ledger"


class PortfolioAccountUnresolved(ValueError):
    """get_portfolio_account found no account, or more than one.

    Still a ValueError, raised as before: a figure must never be computed on a guessed
    scope. ``reason`` is the lookup's own statement of why, as a clause, for the one
    kind of caller that must SAY so instead of raising: a sentence about the book
    that names no figure (the as-of banner's report line, a page's empty state). A
    book with no trade logged yet is the common case, and it used to put a traceback
    on every page of a clean clone's first personal launch (#510)."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def get_portfolio_account() -> dict:
    """Resolve THE portfolio account — the single source of truth for the
    account identity behind every trade-derived "Portfolio" view.

    It is the active, self-directed, TAXABLE account that carries the
    discretionary trade ledger. Returns ``{"account_id", "display_name", "name"}``.

    RAISES (never falls back) when it cannot resolve EXACTLY ONE such account: a
    wrong or silently-defaulted scope here mis-states every headline number and,
    on the PDF path, a document that gets sent to someone. A retirement/external
    account that later carries trades (a Roth, an IRA) is excluded by the
    tax_treatment/managed_by predicate — which is exactly what keeps it out of the
    taxable-book totals. More than one candidate also raises: identity must be
    disambiguated, not guessed. (The historical acct_01/acct_taxable_01 duplication
    — the same real account under two ids, trades on acct_01 and positions on
    acct_taxable_01 — was merged into acct_01; see
    tools/migrate_accounts_phase45_merge.py.)

    ``display_name`` is read from the DB so labels are correct in demo too (the
    demo book's own account name), never a hardcoded "Self-Directed Taxable".
    """
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT a.account_id, a.display_name, a.name
                 FROM accounts a
                WHERE a.is_active = 1
                  AND a.tax_treatment = 'taxable'
                  AND a.managed_by = 'self'
                  AND EXISTS (SELECT 1 FROM trades t WHERE t.account_id = a.account_id)
                ORDER BY a.account_id""",
        ).fetchall()

    if len(rows) == 1:
        r = rows[0]
        return {
            "account_id":   int(r["account_id"]),
            "display_name": r["display_name"] or r["name"],
            "name":         r["name"],
        }
    if not rows:
        raise PortfolioAccountUnresolved(
            NO_TRADE_LEDGER,
            f"cannot resolve the portfolio account: {NO_TRADE_LEDGER}. Refusing to "
            "guess a scope for the trade-derived 'Portfolio' views."
        )
    several = f"{len(rows)} self-directed taxable accounts carry trades"
    raise PortfolioAccountUnresolved(
        several,
        f"cannot resolve the portfolio account uniquely: {several} "
        f"(ids {[int(r['account_id']) for r in rows]}). Disambiguate the "
        "account-identity duplication before scoping the reads."
    )


def get_portfolio_account_id() -> int:
    """account_id of THE portfolio account (see ``get_portfolio_account``)."""
    return get_portfolio_account()["account_id"]


def _require_account_id(account_id) -> int:
    """Reject a missing scope loudly — a None account_id would otherwise read as
    'no filter' (account-blind), the exact defect these reads exist to remove."""
    if account_id is None:
        raise ValueError(
            "account_id is required — this read is account-scoped and will not "
            "silently fall back to reading across every account. Pass the account "
            "to scope to (typically get_portfolio_account_id())."
        )
    return int(account_id)


def get_holdings_on_date(date_str: str, *, account_id: int) -> pd.DataFrame:
    """
    Return net shares per ticker as of date_str (inclusive), for ONE account.
    Index: ticker. Column: net_shares.
    SPAXX 'shares' represent dollar amount (NAV is always $1).

    ``account_id`` is required (keyword-only) and never defaulted — see the module
    docstring. Pass get_portfolio_account_id() for the trade-derived portfolio.
    """
    account_id = _require_account_id(account_id)
    # Inside a raw-close lock, from the ledger the lock holds (#369), so a later lot
    # rebuild cannot move it.
    from src.cache import valuation_in_force
    ledger = valuation_in_force()[1]
    if ledger is not None:
        book = _account_ledger(account_id, ledger)
        book = book[book["trade_date"] <= date_str]
        if book.empty:
            return pd.DataFrame(columns=["net_shares"])
        signed = book["shares"].where(book["action"].str.lower() == "buy", -book["shares"])
        df = signed.groupby(book["ticker"]).sum().rename("net_shares").to_frame()
        df.index.name = "ticker"
        return df[df["net_shares"] > 0].copy()
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT ticker,
                      SUM(CASE WHEN LOWER(action) = 'buy'
                               THEN shares ELSE -shares END) AS net_shares
               FROM trades
               WHERE trade_date <= ? AND account_id = ?
               GROUP BY ticker""",
            (date_str, account_id),
        ).fetchall()

    if not rows:
        return pd.DataFrame(columns=["net_shares"])

    df = pd.DataFrame([dict(r) for r in rows]).set_index("ticker")
    return df[df["net_shares"] > 0].copy()


_LEDGER_READ = ["trade_date", "ticker", "action", "shares", "price", "fees", "lot_source"]


def _account_ledger(account_id: int, ledger: "pd.DataFrame | None") -> pd.DataFrame:
    """Every trade of the account, DRIP lots included, in the order the value series
    reads them, with the columns it reads (fees 0 where none was recorded): from the
    quarter lock's ledger when the lock in force holds one (#369), from the trades table
    otherwise."""
    if ledger is not None:
        rows = ledger[ledger["account_id"] == account_id].sort_values(
            ["trade_date", "trade_id"])
        frame = rows[_LEDGER_READ].reset_index(drop=True)
    else:
        with get_connection() as conn:
            rows = conn.execute(
                """SELECT trade_date, ticker, action, shares, price, fees, lot_source
                   FROM trades WHERE account_id = ?
                   ORDER BY trade_date, trade_id""",
                (account_id,),
            ).fetchall()
        frame = pd.DataFrame([dict(r) for r in rows], columns=_LEDGER_READ)
    frame["fees"] = frame["fees"].fillna(0.0).astype(float)
    return frame


def last_real_price_date(start_date: str, end_date: Optional[str] = None) -> str:
    """Last calendar date with a REAL (non-forward-filled) market price across the
    portfolio's holdings — the true data frontier.

    get_portfolio_value_series forward-fills prices across every calendar day up to
    end_date (today), so its ``index[-1]`` is the wall-clock date, not the last
    traded day. Return/attribution windows that must reconcile (the BF real-price
    decomposition vs the value series) have to anchor here — the last date both
    sides have REAL data — otherwise they slice across a forward-filled tail and
    diverge whenever today > the last traded day (after the UTC rollover, on
    weekends/holidays, or on a sparse newly-funded portfolio).

    Returns an ISO date string; falls back to end_date when no holdings/prices exist.
    """
    end = end_date or date.today().isoformat()
    # Wrapper: resolves the portfolio account itself and passes it through, so its
    # callers stay unchanged while the underlying read is account-scoped.
    holdings = get_holdings_on_date(end, account_id=get_portfolio_account_id())
    max_date = None
    for ticker in holdings.index:
        price_ticker = "BIL" if ticker == "SPAXX" else ticker  # SPAXX proxied via BIL
        try:
            idx = get_prices(price_ticker, start_date, end).index
        except Exception:
            continue
        if len(idx) == 0:
            continue
        m = pd.to_datetime(idx).max().date()
        max_date = m if max_date is None else max(max_date, m)
    return max_date.isoformat() if max_date is not None else end


def committed_price_frontier(
    today: "date | str | None" = None, *, account_id: Optional[int] = None
) -> Optional[str]:
    """The holdings' COMMON committed price frontier — DB only, NEVER a fetch.

    ``MIN`` over the portfolio's holdings of each holding's ``MAX(price_date)``
    on or before ``today``: the latest date on which EVERY holding the report
    consumes has a real, already-stored price. Returns an ISO date string, or
    ``None`` when no holding has any committed price.

    THE PRICE DATE: one rule for the banner and the numbers. Stored bars are settled
    closes only (fetch_prices never caches the bar of an open session, #160), and in
    the demo every page reads only what is stored. So the numbers use every stored
    close through today, including today's once its session has closed and the
    refresh has stored it, and this names that same date.

    Three deliberate choices, each load-bearing:

    - **MIN, not MAX.** ``last_real_price_date`` returns the max, which
      over-promises: one ticker reaching a later date does not make the portfolio
      priceable there. The report consumes every holding, so the honest frontier
      is the one they all clear.
    - **``price_date <= today``.** This used to exclude today, so a partial
      same-day bar written by a live mid-session fetch could not present itself as
      settled. #160 stopped such bars being written, and the exclusion then only
      made the banner a day behind numbers that already used today's close.
    - **No fetch.** This answers "what does the committed data already support",
      which is precisely the question a gap-filling read would destroy by going
      and filling the gap. It is also called on every page render, so it must
      stay a couple of indexed ``MAX()`` lookups.

    Returns ``None`` — read by callers as "no opinion", not as "nothing is
    supported" — when the account or its holdings cannot be resolved. That keeps
    a cap built on this from being the thing that takes down a report: a DB with
    no holdings or no prices has no figures to misdate in the first place.
    """
    frontiers = _stored_frontiers(today, account_id=account_id)
    return min(frontiers) if frontiers else None


def _stored_frontiers(
    today: "date | str | None" = None, *, account_id: Optional[int] = None,
    start: Optional[str] = None,
) -> "list[str]":
    """Each holding's latest STORED (so settled) close on or before ``today``: the
    one rule behind both committed_price_frontier (the lowest, which every holding
    clears) and last_settled_price_date (the highest, which a window anchors on).
    DB only, never a fetch."""
    ref = today or date.today()
    ref = date.fromisoformat(ref) if isinstance(ref, str) else ref
    ref_iso = ref.isoformat()

    try:
        if account_id is None:
            account_id = get_portfolio_account_id()
        holdings = get_holdings_on_date(ref_iso, account_id=account_id)
    except Exception:
        # No resolvable portfolio account / no ledger — see committed_price_frontier.
        return []
    if holdings.empty:
        return []

    frontiers: list[str] = []
    with get_connection() as conn:
        for ticker in holdings.index:
            price_ticker = "BIL" if ticker == "SPAXX" else ticker  # SPAXX proxied via BIL
            row = conn.execute(
                "SELECT MAX(price_date) FROM prices WHERE ticker = ? AND price_date <= ? "
                "AND price_date >= ?",
                (price_ticker, ref_iso, start or "0000-00-00"),
            ).fetchone()
            # A holding with no committed price at all is an already-surfaced
            # price gap (see brinson_fachler_period's price_gaps), not a frontier
            # signal — skip it rather than collapsing the frontier to nothing.
            if row and row[0]:
                frontiers.append(row[0])
    return frontiers


def last_settled_price_date(start_date: str, end_date: Optional[str] = None) -> str:
    """Last COMPLETE settled trading day — the anchor for DISPLAYED period returns.

    The latest STORED close across the holdings, on or before ``end_date`` and
    today: the same rule as committed_price_frontier (see its docstring), so
    a window ends on the date the banner names. Stored bars are settled closes only
    (#160), so an in-progress bar is never a window endpoint, which would swing
    displayed 1M/3M returns by the intraday move. A live mark that a personal-mode
    read serves for an open session is not stored, so it never anchors a window.

    This used to cap at yesterday, from when a partial bar could be cached: on a
    settled today (post-close) the display was a day stale while the current-value
    figure beside it already used today's close.

    On frozen/committed data (frontier < today) this EQUALS last_real_price_date,
    so the deterministic BF reconciliation test (which anchors on last_real) is
    unaffected.
    """
    today = date.today().isoformat()
    cap = min(end_date or today, today)  # ISO strings compare lexicographically
    # The served read first, as always: where gap fills are on (personal mode) it
    # fetches and stores any settled close the cache lacks. Then the anchor is what
    # is STORED, so a served-but-unstored live bar cannot be it.
    served = last_real_price_date(start_date, cap)
    frontiers = _stored_frontiers(cap, start=start_date)
    return min(served, max(frontiers)) if frontiers else served


def get_portfolio_value_series(
    start_date: str,
    end_date: Optional[str] = None,
    *,
    account_id: int,
) -> pd.Series:
    """Daily total portfolio market value — see _portfolio_value_series_impl.

    RAISES UnpricedHoldingError, naming the holdings and dates, when a held position
    has no price on a date it was held in the window (#639, #643): every reader computes
    a return from this series, and none can be computed over a book missing a holding.
    portfolio_value_series_with_coverage returns the series and the record instead, for
    the page that withholds its figures itself; both share one valuation loop.
    """
    series, statuses = _portfolio_value_series_impl(
        start_date, end_date, account_id=account_id)
    unpriced = tuple(s.unpriced_held for s in statuses if s.unpriced_held)
    if unpriced:
        raise UnpricedHoldingError(unpriced)
    return series


def portfolio_value_series_with_coverage(
    start_date: str,
    end_date: Optional[str] = None,
    *,
    account_id: int,
) -> "ValueSeries":
    """get_portfolio_value_series, plus the coverage record for its prices.

    This series drives every TWR figure and every risk metric on the Performance
    page, and it is where an unpriceable holding was hardest to see: .sum(axis=1)
    skipped its all-NaN column, so it counted as zero dollars on every day. The series
    is NaN on a date a held position has no price now, and the record's
    ``unpriced_held`` names the holding and dates (#639, #643); the page withholds what
    it computes from the series when the record says so.
    """
    series, statuses = _portfolio_value_series_impl(
        start_date, end_date, account_id=account_id)
    as_of = end_date or date.today().isoformat()
    return ValueSeries(series, coverage_from_statuses(statuses, as_of))


def _portfolio_value_series_impl(
    start_date: str,
    end_date: Optional[str] = None,
    *,
    account_id: int,
) -> "tuple[pd.Series, list[TickerStatus]]":
    """
    Return daily total portfolio market value over [start_date, end_date], for
    ONE account (keyword-only ``account_id``, required — see the module docstring).
    Weekends / holidays are forward-filled from the previous trading day.
    A date on which a held position has no price is NaN, never a sum counting it as
    $0, and the holding's status records the dates (``unpriced_held``, #639, #643).
    SPAXX is valued at BIL's total return from the book's inception (#646).

    TWO VALUATION RULES (#369, decided by the owner on October 27 and 28, 2026), chosen
    by cache.valuation_in_force: live reads are on the raw-close rule; inside a quarter
    lock, the lock's own marker, and a lock with none is on adjusted levels.

    THE RAW-CLOSE RULE. Every share held, DRIP lots included, at that day's close, plus
    the income line (_dividend_income): each dividend is income on its ex-date, the
    shares held at the previous day's end times the amount. A DRIP lot is paid from its
    dividend's income, an internal move and not a flow; a dividend no DRIP lot
    reinvests goes into SPAXX on its ex-date and earns BIL's total return from then.
    Inside a raw-close lock the trades come from the ledger the lock holds. The series'
    last value is the current market value (_current_market_value_impl): one basis.

    ADJUSTED LEVELS, every lock taken before the marker. Non-DRIP shares at adj_close,
    the total-return level. DRIP lots (lot_source='drip') are EXCLUDED from the share
    count: adj_close already embeds dividend reinvestment into the existing shares, so
    counting the DRIP-reinvested shares on top of it would double-count income and
    overstate the total-return TWR (worst on the high-yielding FI sleeves).
    """
    account_id = _require_account_id(account_id)
    end = end_date or date.today().isoformat()
    date_range = pd.date_range(start=start_date, end=end, freq="D")
    from src.cache import RAW_CLOSE_RULE, valuation_in_force
    rule, ledger = valuation_in_force()
    raw = rule == RAW_CLOSE_RULE

    if raw:
        book = _account_ledger(account_id, ledger)
        trades_df = book[book["trade_date"] <= end].copy()
        if trades_df.empty:
            return pd.Series(0.0, index=date_range), []
    else:
        with get_connection() as conn:
            trade_rows = conn.execute(
                """SELECT trade_date, ticker, action, shares
                   FROM trades
                   WHERE trade_date <= ? AND account_id = ?
                     AND (lot_source IS NULL OR lot_source != 'drip')
                   ORDER BY trade_date, trade_id""",
                (end, account_id),
            ).fetchall()

        if not trade_rows:
            return pd.Series(0.0, index=date_range), []

        trades_df = pd.DataFrame([dict(r) for r in trade_rows])
    trades_df["trade_date"] = pd.to_datetime(trades_df["trade_date"])
    trades_df["signed"] = trades_df.apply(
        lambda r: r["shares"] if r["action"].lower() == "buy" else -r["shares"],
        axis=1,
    )

    # Build daily share-change matrix from first-ever trade date
    first_trade_date = trades_df["trade_date"].min()
    full_range = pd.date_range(
        start=min(first_trade_date, pd.Timestamp(start_date)),
        end=end,
        freq="D",
    )
    if raw and (trades_df["ticker"] == "SPAXX").any():
        # Under the raw-close rule a SPAXX trade enters at its dollars, as units bought
        # at SPAXX's level that day, so it earns BIL's total return from its own date.
        level, _st = _spaxx_levels(account_id, end, full_range)
        spaxx = trades_df["ticker"] == "SPAXX"
        trades_df.loc[spaxx, "signed"] = [
            _spaxx_units(float(n) * float(px), level.get(d))
            for n, px, d in zip(trades_df.loc[spaxx, "signed"], trades_df.loc[spaxx, "price"],
                                trades_df.loc[spaxx, "trade_date"])]

    daily_changes = (
        trades_df.groupby(["trade_date", "ticker"])["signed"]
        .sum()
        .unstack(fill_value=0)
        .reindex(full_range, fill_value=0)
    )

    # Cumulative holdings at each date
    held = daily_changes.cumsum().clip(lower=0)

    # Slice to requested range (full_range ⊇ date_range since start ≤ start_date)
    holdings_matrix = held.reindex(date_range)

    pending = None
    if raw:
        # The income line: SPAXX units bought with dividend income, held as SPAXX, and
        # income a DRIP lot has yet to be paid from, held at its amount.
        units, pending = _dividend_income(book, held, end, account_id=account_id)
        units, pending = units.reindex(date_range), pending.reindex(date_range)
        if (units > 0).any():
            spaxx = holdings_matrix["SPAXX"] if "SPAXX" in holdings_matrix else 0.0
            holdings_matrix["SPAXX"] = spaxx + units
    # Under adjusted levels DRIP reinvestment shares (lot_source='drip') are
    # deliberately EXCLUDED by the SQL query above: adj_close already embeds dividend
    # reinvestment, so adding the DRIP shares would double-count income.

    tickers = holdings_matrix.columns.tolist()
    prices_matrix, _factors, statuses = _valuation_levels(
        tickers, start_date, end, account_id=account_id, raw=raw)

    common = [t for t in tickers if t in prices_matrix.columns]

    # A held position with no price is never $0 (#639, #643): such a day is not a sum,
    # and the holding's status records the dates for the readers that must say so.
    unpriced = (holdings_matrix[common] > 1e-9) & prices_matrix[common].isna()
    value = (holdings_matrix[common] * prices_matrix[common]).sum(axis=1)
    if pending is not None:
        value = value + pending
    value[unpriced.any(axis=1)] = float("nan")
    from src.demo_refresh import is_session
    for i, ticker in enumerate(tickers):
        if ticker in unpriced.columns and unpriced[ticker].any():
            days = unpriced.index[unpriced[ticker]]
            statuses[i] = replace(statuses[i], unpriced_held=UnpricedHolding(
                ticker, days[0].date().isoformat(), days[-1].date().isoformat(),
                sum(is_session(d.date()) for d in days), why=statuses[i].detail))
    return value, statuses


def _spaxx_levels(account_id: int, end: str, days: pd.DatetimeIndex
                  ) -> "tuple[pd.Series, TickerStatus]":
    """SPAXX's level on each of ``days``, and SPAXX's status: BIL's total return,
    normalized to $1.00 at the book's inception, as at every site that values SPAXX
    (#646), forward-filled. All NaN when BIL does not cover the inception
    (_bil_for_spaxx), $1.00 when its frame will not read."""
    p, st = _bil_for_spaxx(get_inception_date(account_id=account_id), end)
    try:
        p.index = pd.to_datetime(p.index)
        series = total_return_series(p)
        p0 = series.first_valid_index()
        if p0 is not None and float(series[p0]) > 0:
            series = series / float(series[p0])  # normalize to $1.00
        return series.reindex(days).ffill(), st
    except Exception:
        return pd.Series(1.0, index=days), st


def _spaxx_units(dollars: float, level) -> float:
    """The SPAXX units ``dollars`` buys at SPAXX's ``level`` that day, under the raw-close
    rule (decided by the owner on October 28, 2026, #369): a SPAXX trade, or a dividend
    swept into SPAXX, enters at its dollars and earns BIL's total return from its own
    date. Where SPAXX has no level a unit is $1.00, as the current value prices an
    unresolved SPAXX, and SPAXX's own status says it is not priced."""
    return dollars / float(level) if level is not None and level > 0 else dollars


def _dividend_income(book: pd.DataFrame, held: pd.DataFrame, end: str, *,
                     account_id: int) -> "tuple[pd.Series, pd.Series]":
    """The raw-close rule's income line (#369, decided by the owner on October 27 and
    28, 2026), by day over ``held``'s index: (the SPAXX units bought with dividend
    income, the income held at its amount until a DRIP lot is paid from it), each
    cumulative.

    Every dividend is income on its ex-date: the shares held at the previous day's end,
    DRIP lots included, times the amount, from the stored dividends (get_dividends, which
    inside a quarter lock serves the lock's own).
    * A dividend on a fund the book reinvests, one its ledger holds DRIP lots for, is
      held at its amount from its ex-date. Each DRIP lot is paid from it on the lot's
      date: the lot's dollars, shares × price + fees, leave the line, an internal move
      and not a flow. The whole ledger decides which funds those are, so a window's end
      cannot change how a day inside it is valued.
    * Any other dividend, as in a book that takes its dividends in cash, goes into SPAXX
      on its ex-date as units bought at SPAXX's level that day (_spaxx_units), so it
      earns BIL's total return from then.

    book: the account's ledger (_account_ledger), every date. held: every share held each
    day, DRIP lots included, from the book's first trade.
    """
    days = held.index
    units = pd.Series(0.0, index=days)
    pending = pd.Series(0.0, index=days)
    reinvested = set(book.loc[book["lot_source"] == "drip", "ticker"])
    level = None
    first = days[0].date().isoformat()
    for ticker in held.columns:
        if ticker == "SPAXX":
            continue
        for ex, amount in get_dividends(ticker, first, end).items():
            ex_ts = pd.Timestamp(ex)
            before = ex_ts - pd.Timedelta(days=1)
            if before < days[0] or ex_ts > days[-1]:
                continue
            income = float(held.at[before, ticker]) * float(amount)
            if income <= 0:
                continue
            if ticker in reinvested:
                pending[ex_ts] += income
                continue
            if level is None:
                level, _st = _spaxx_levels(account_id, end, days)
            units[ex_ts] += _spaxx_units(income, level.get(ex_ts))
    lots = book[(book["lot_source"] == "drip") & (book["trade_date"] <= end)]
    for lot in lots.itertuples(index=False):
        lot_ts = pd.Timestamp(lot.trade_date)
        if lot_ts in pending.index:
            pending[lot_ts] -= float(lot.shares) * float(lot.price) + float(lot.fees)
    return units.cumsum(), pending.cumsum()


def _valuation_levels(
    tickers: "list[str]",
    start_date: str,
    end: str,
    *,
    account_id: int,
    raw: bool = False,
) -> "tuple[pd.DataFrame, pd.DataFrame, list[TickerStatus]]":
    """The level the value series counts one share of each ticker at, on every day of
    [start_date, end], each ticker's adjustment factor on those days, and each
    ticker's status, in ``tickers``' order.

    ``raw``: the raw-close rule (#369), where the level is the ticker's close and every
    factor is 1, since a flow is the ledger's dollars. SPAXX is BIL's total return
    under both rules.

    The factor is the level over the ticker's close, both read from the one frame, so
    it is what a dollar paid for the ticker that day counts for in the value series'
    units. One reading for both sides of the TWR's step (#687): the value series
    multiplies shares by the level, and get_external_cashflow_series takes each
    trade's own dollars at the factor. SPAXX's close is its $1 NAV, so its factor is
    its level. A ticker with no price is an all-NaN column in both, its status saying
    why.

    A frame serving no close, which only a legacy single-basis lock does (#193; no
    committed lock is one), has a factor of 1: its flows stay the ledger's dollars, as
    every flow was before #687, off by the dividend factor alone. The other way, NaN,
    is worse: every reader fills the flow series' NaN with zero, so the whole trade
    would count as that day's return.
    """
    date_range = pd.date_range(start=start_date, end=end, freq="D")

    # Build price matrix (adj_close, forward-filled for non-trading days).
    # SPAXX NAV is always $1, but it accrues T-bill yield.  We proxy its total
    # return through BIL (SPDR Bloomberg 1-3 Month T-Bill ETF), whose adj_close
    # already reflects reinvested T-bill yield.  BIL is normalized to $1.00 at the
    # book's inception, as at every site that values SPAXX (#646), so that
    # shares × price still equals dollars of SPAXX held.
    price_cols: dict = {}
    factor_cols: dict = {}
    statuses: "list[TickerStatus]" = []
    for ticker in tickers:
        if ticker == "SPAXX":
            price_cols[ticker], st = _spaxx_levels(account_id, end, date_range)
            statuses.append(st)
            factor_cols[ticker] = price_cols[ticker]
        else:
            p, st = _price_and_status(ticker, start_date, end, account_id=account_id)
            if not st.resolved:
                # Branch on the STATUS, not on an exception: _price_and_status
                # reports a miss instead of raising, and letting an empty frame
                # flow through the value series' arithmetic would build an object-dtype
                # NaN column where the original built a float one. Same zero
                # dollars either way, but this keeps the column byte-identical.
                price_cols[ticker] = pd.Series(dtype=float, index=date_range)
                factor_cols[ticker] = pd.Series(dtype=float, index=date_range)
                statuses.append(st)
                continue
            try:
                p.index = pd.to_datetime(p.index)
                series = p["close"].astype(float) if raw else total_return_series(p)
                price_cols[ticker] = series.reindex(date_range).ffill()
                factor = (series / p["close"] if "close" in p.columns and not raw
                          else pd.Series(1.0, index=p.index))
                factor_cols[ticker] = factor.reindex(date_range).ffill()
            except Exception:
                # An all-NaN column contributes exactly zero dollars to every day
                # of the series, because the value series' .sum(axis=1) defaults to
                # skipna=True — no NaN escapes to warn anyone (#188). The status
                # is the only record that this holding was not valued.
                price_cols[ticker] = pd.Series(dtype=float, index=date_range)
                factor_cols[ticker] = pd.Series(dtype=float, index=date_range)
                st = replace(st, resolved=False, reason="empty_window",
                             served_through=None)
            statuses.append(st)

    prices_matrix = pd.DataFrame(price_cols, index=date_range)
    factors_matrix = pd.DataFrame(factor_cols, index=date_range)

    # A window asked from a weekend or a holiday has days before its first session with
    # no close on or before them in the window. Nothing traded on them, so they carry
    # that session's close rather than reading as unpriced: a market holiday is never
    # a gap (#639).
    first_session = pd.Timestamp(_first_session_on_or_after(start_date))
    lead = date_range[date_range < first_session]
    if len(lead) and first_session in prices_matrix.index:
        prices_matrix.loc[lead] = prices_matrix.loc[lead].fillna(
            prices_matrix.loc[first_session])
        factors_matrix.loc[lead] = factors_matrix.loc[lead].fillna(
            factors_matrix.loc[first_session])
    return prices_matrix, factors_matrix, statuses


def get_external_cashflow_series(
    start_date: str,
    end_date: Optional[str] = None,
    *,
    account_id: int,
) -> pd.Series:
    """
    Return the daily net EXTERNAL flow into the portfolio (signed: contributions
    positive, withdrawals negative) over [start_date, end_date], zero on days with
    no flow, in the value series' own units. This is the flow series the TWR
    functions (src/returns.py) and compute_risk_metrics (src/performance.py) net
    out so that deposited cash is not counted as market return.

    UNDER THE RAW-CLOSE RULE, THE LEDGER'S DOLLARS (#369, decided by the owner on
    October 28, 2026): shares × the trade's price + fees for a buy, −shares × price +
    fees for a sell, DRIP lots excluded, since each is paid from its dividend's income.
    The value series counts a share at its close there, so there is nothing to convert,
    and inside a raw-close lock the trades come from the ledger the lock holds. The rest
    of this docstring is the adjusted-levels rule, every lock taken before the marker.

    IN THE VALUE SERIES' UNITS, NOT THE LEDGER'S DOLLARS (#687, convention chosen by
    the owner on October 27, 2026). The TWR's step is
    r_t = (V_t − V_{t−1} − CF_t) / V_{t−1}, and get_portfolio_value_series counts a
    share at its total-return level, which is back-adjusted for every stored dividend
    after the date. So each trade's own dollars, shares × its price, are taken at the
    ticker's adjustment factor f on the trade date, its level over its close
    (_valuation_levels, over this window). At the raw dollars, as this was until #687,
    a buy of a ticker that paid a later dividend took off more than it added, and the
    step read low by (CF_t / V_{t−1}) × (1 − f); a withdrawal read high by the same.
    The ledger's dollars differ from this flow by that factor, so this is not the
    amount deposited. Valuing the book at the raw close with dividends as income
    (#369) would remove the mismatch at its root.

    The trade's price, not its close: a trade at the close takes off exactly what it
    adds to V_t and earns nothing on its own day, as end-of-day timing says, while a
    trade away from the close keeps shares × (price − close) × f in that day's
    return, because the book paid the price. A buy above the close is that day's
    loss, a sale above it that day's gain.

    Buys are cash entering the measured portfolio, sells cash leaving it; fees
    always land as a performance cost, in dollars, not converted (a buy is
    shares × price × f + fees, a sell −shares × price × f + fees). Same-date buy/sell
    pairs (internal rebalances, e.g. sell VOO → buy SPAXX) are summed per date, so
    at the close they move no return on their day beyond their fees.

    A trade in a ticker with no level on its date makes that day's flow NaN, never
    a raw-price stand-in: the value series has no level for that holding there
    either, and is NaN on the days it holds it (#639, #643).

    DRIP lots (lot_source='drip') are EXCLUDED, mirroring
    get_portfolio_value_series: reinvested distributions are internal income
    already embedded in adj_close, not external cash. Inception seed buys
    ('initial') ARE included — they land on the series' first date, which both
    TWR methods treat as the starting NAV, so seeding stays inert while any
    post-inception deployment counts as a real flow.
    """
    account_id = _require_account_id(account_id)
    end = end_date or date.today().isoformat()
    date_range = pd.date_range(start=start_date, end=end, freq="D")

    from src.cache import RAW_CLOSE_RULE, valuation_in_force
    rule, ledger = valuation_in_force()
    if rule == RAW_CLOSE_RULE:
        book = _account_ledger(account_id, ledger)
        book = book[(book["trade_date"] >= start_date) & (book["trade_date"] <= end)
                    & (book["lot_source"] != "drip")]
        flows = pd.Series(0.0, index=date_range)
        for r in book.itertuples(index=False):
            ts = pd.Timestamp(r.trade_date)
            if ts in flows.index:
                signed = r.shares if r.action.lower() == "buy" else -r.shares
                flows[ts] += float(signed) * float(r.price) + float(r.fees)
        return flows

    with get_connection() as conn:
        rows = conn.execute(
            """SELECT trade_date, ticker, action, shares, price, COALESCE(fees, 0) AS fees
               FROM trades
               WHERE trade_date >= ? AND trade_date <= ? AND account_id = ?
                 AND (lot_source IS NULL OR lot_source != 'drip')
               ORDER BY trade_date, trade_id""",
            (start_date, end, account_id),
        ).fetchall()

    flows = pd.Series(0.0, index=date_range)
    if not rows:
        return flows
    _levels, factors, _statuses = _valuation_levels(
        sorted({r["ticker"] for r in rows}), start_date, end, account_id=account_id)
    for r in rows:
        ts = pd.Timestamp(r["trade_date"])
        if ts in flows.index:
            signed = r["shares"] if r["action"].lower() == "buy" else -r["shares"]
            flows[ts] += (signed * r["price"] * float(factors.at[ts, r["ticker"]])
                          + float(r["fees"]))
    return flows


_CASH_SLEEVE = "Cash / SPAXX"


def get_sleeve_weights_on_date(date_str: str) -> pd.DataFrame:
    """Actual vs. target weight per strategic sleeve — see _sleeve_weights_impl.

    Kept as-is so no existing caller changes. Callers that need to disclose what
    the figures are made of use sleeve_weights_with_coverage instead; both run the
    same valuation loop, so the two can never drift apart.
    """
    frame, _statuses = _sleeve_weights_impl(date_str)
    return frame


def sleeve_weights_with_coverage(date_str: str) -> "SleeveWeights":
    """get_sleeve_weights_on_date, plus the coverage record for its prices.

    Returns a NamedTuple, so `frame, coverage = sleeve_weights_with_coverage(...)`
    destructures and the coverage cannot be silently dropped by a pandas operation
    the way a frame attribute can (see src/coverage.py for why this is not .attrs).
    """
    frame, statuses = _sleeve_weights_impl(date_str)
    return SleeveWeights(frame, coverage_from_statuses(statuses, date_str))


def _sleeve_weights_impl(date_str: str) -> "tuple[pd.DataFrame, list[TickerStatus]]":
    """
    Return actual vs. target weight per STRATEGIC sleeve as of date_str.
    Columns: Market Value, Actual Weight, Target Weight, Drift (actual - target).
    Index: sleeve name.
    Uses close price (unadjusted) for current market value.

    Phase 38a — cash is operational float, not a strategic allocation. The
    Cash / SPAXX sleeve is excluded from the returned rows and weights are
    measured ex-cash: Actual Weight = sleeve_mv / invested_value, where
    invested_value = total_mv − cash_mv. Operational-cash figures are exposed
    on the DataFrame's ``.attrs`` for callers that display them:
        total_value, cash_mv, invested_value, cash_weight_of_total.
    """
    # Wrapper: resolves the portfolio account and passes it through.
    account_id = get_portfolio_account_id()
    holdings = get_holdings_on_date(date_str, account_id=account_id)
    statuses: "list[TickerStatus]" = []
    if holdings.empty:
        return pd.DataFrame(), statuses

    # The book's policy, or the lock's inside a quarter lock that holds one (#525).
    from src import policy
    ticker_to_sleeve = policy.sleeve_of_ticker()
    target_weights = {r["name"]: r["target_weight"] for r in policy.sleeves()}

    # Look back up to 5 trading days from the newest STORED price (#302); per ticker,
    # because each ticker's cache can end on a different day.

    values_by_sleeve: dict[str, float] = {}
    for ticker, row in holdings.iterrows():
        sleeve = ticker_to_sleeve.get(ticker, "Unknown")
        shares = float(row["net_shares"])

        if ticker == "SPAXX":
            # Priced off BIL, its growth measured from the book's inception (#646).
            # The status is recorded under SPAXX, the holding being valued; that
            # this is a PROXY instrument is a substitution, and reporting it belongs
            # to the PR that populates that field.
            p, st = _bil_for_spaxx(get_inception_date(account_id=account_id), date_str)
            statuses.append(st)
            try:
                if not p.empty:
                    bil = total_return_series(p)
                    p0_idx = bil.first_valid_index()
                    if p0_idx is not None and float(bil[p0_idx]) > 0:
                        bil = bil / float(bil[p0_idx])
                    price = float(bil.ffill().iloc[-1])
                else:
                    price = 1.0
            except Exception:
                price = 1.0
        else:
            p, st = _price_and_status(ticker, look_back_start(ticker, date_str), date_str,
                                      account_id=account_id)
            try:
                price = float(p["close"].iloc[-1]) if not p.empty else 0.0
            except Exception:
                # The frame arrived but the value would not extract. The price is
                # fabricated either way, so the status must not claim resolved.
                price = 0.0
                st = replace(st, resolved=False, reason="empty_window",
                             served_through=None)
            statuses.append(st)

        values_by_sleeve[sleeve] = values_by_sleeve.get(sleeve, 0.0) + shares * price

    total = sum(values_by_sleeve.values())
    if total == 0:
        return pd.DataFrame(), statuses

    # Ex-cash denominator: strategic weights are a share of INVESTED value.
    cash_mv  = values_by_sleeve.get(_CASH_SLEEVE, 0.0)
    invested = total - cash_mv
    if invested <= 0:
        return pd.DataFrame(), statuses

    rows = []
    for sleeve, mv in sorted(values_by_sleeve.items(), key=lambda x: -x[1]):
        if sleeve == _CASH_SLEEVE:
            continue  # operational float — surfaced via .attrs, not a strategic row
        target = target_weights.get(sleeve, 0.0)
        actual = mv / invested
        rows.append(
            {
                "Sleeve":         sleeve,
                "Market Value":   round(mv, 2),
                "Actual Weight":  round(actual, 4),
                "Target Weight":  round(target, 4),
                "Drift":          round(actual - target, 4),
            }
        )

    df = pd.DataFrame(rows).set_index("Sleeve")
    df.attrs["total_value"]          = round(total, 2)
    df.attrs["cash_mv"]              = round(cash_mv, 2)
    df.attrs["invested_value"]       = round(invested, 2)
    df.attrs["cash_weight_of_total"] = round(cash_mv / total, 6) if total else 0.0
    return df, statuses


def get_current_market_value(date_str: Optional[str] = None) -> float:
    """True current account market value for DISPLAY: every share actually held
    (including DRIP lots) valued at the latest RAW close, plus cash (SPAXX proxied via
    BIL adj_close normalized to $1 at inception).

    UNDER THE RAW-CLOSE RULE, live reads (#369), it also holds the income line
    (dividend_income), and it is the value series' last value: returns and the current
    value share one basis. Under adjusted levels, inside a lock taken before the marker,
    it holds no income line and is DISTINCT from get_portfolio_value_series there
    (adj_close × NON-DRIP shares, the total-return series used for that lock's returns).

    Display-only: this figure must not feed any return calculation.
    """
    value, _statuses = _current_market_value_impl(date_str)
    return value


def current_market_value_with_coverage(
    date_str: Optional[str] = None,
) -> "MarketValue":
    """get_current_market_value, plus the coverage record for its prices.

    This figure had nowhere to carry a frame attribute even in principle — it is a
    bare float — and it drives the most consequential number in the app: the
    "$X current value" header on the Performance page and the Total Market Value on
    Tax Lots. A sibling returning value-plus-coverage is the only additive way to
    make it disclosable.
    """
    value, statuses = _current_market_value_impl(date_str)
    as_of = date_str or date.today().isoformat()
    return MarketValue(value, coverage_from_statuses(statuses, as_of))


def _current_market_value_impl(
    date_str: Optional[str] = None,
) -> "tuple[float, list[TickerStatus]]":
    """The valuation loop behind both entry points above."""
    d = date_str or date.today().isoformat()
    # Wrapper: resolves the portfolio account and passes it through.
    account_id = get_portfolio_account_id()
    holdings = get_holdings_on_date(d, account_id=account_id)  # all shares, incl DRIP lots
    statuses: "list[TickerStatus]" = []
    if holdings.empty:
        return 0.0, statuses

    raw = _raw_close_on(d, account_id=account_id)
    total = 0.0
    for ticker, row in holdings.iterrows():
        shares = float(row["net_shares"])
        if ticker == "SPAXX":
            price, st = _spaxx_price(d)
            statuses.append(st)
            if raw is not None:
                shares = raw.spaxx_units
        else:
            p, st = _price_and_status(ticker, look_back_start(ticker, d), d,
                                      account_id=account_id)
            try:
                price = float(p["close"].iloc[-1]) if not p.empty else 0.0
            except Exception:
                price = 0.0
                st = replace(st, resolved=False, reason="empty_window",
                             served_through=None)
            statuses.append(st)
        total += shares * price
    if raw is not None:
        total += raw.income
        if raw.status is not None and "SPAXX" not in holdings.index:
            statuses.append(raw.status)
    return round(total, 2), statuses


class _RawCloseOn(NamedTuple):
    spaxx_units: float              # SPAXX units its trades hold (_spaxx_units)
    income: float                   # the income line's value (_dividend_income)
    status: "TickerStatus | None"   # SPAXX's, when the income line holds SPAXX


def _raw_close_on(d: str, *, account_id: int) -> "_RawCloseOn | None":
    """What the raw-close rule adds to the current value on ``d``, read as the value
    series reads it: the SPAXX units the trades hold, and the income line's value, its
    SPAXX part at SPAXX's level on ``d``. None under adjusted levels, which hold neither."""
    from src.cache import RAW_CLOSE_RULE, valuation_in_force
    rule, ledger = valuation_in_force()
    if rule != RAW_CLOSE_RULE:
        return None
    book = _account_ledger(account_id, ledger)
    book = book[book["trade_date"] <= d]
    if book.empty:
        return _RawCloseOn(0.0, 0.0, None)
    signed = book["shares"].where(book["action"].str.lower() == "buy", -book["shares"])
    days = pd.date_range(start=book["trade_date"].min(), end=d, freq="D")
    held = (signed.groupby([pd.to_datetime(book["trade_date"]), book["ticker"]]).sum()
            .unstack(fill_value=0).reindex(days, fill_value=0).cumsum().clip(lower=0))
    spaxx_units = 0.0
    spaxx = book[book["ticker"] == "SPAXX"]
    if not spaxx.empty:
        level, _st = _spaxx_levels(account_id, d, days)
        bought = [_spaxx_units(float(n) * float(px), level.get(pd.Timestamp(t)))
                  for n, px, t in zip(signed[spaxx.index], spaxx["price"], spaxx["trade_date"])]
        spaxx_units = max(sum(bought), 0.0)
    units, pending = _dividend_income(book, held, d, account_id=account_id)
    income = float(pending.iloc[-1])
    if float(units.iloc[-1]) <= 0:
        return _RawCloseOn(spaxx_units, income, None)
    price, st = _spaxx_price(d)
    return _RawCloseOn(spaxx_units, income + float(units.iloc[-1]) * price, st)


def dividend_income(date_str: Optional[str] = None) -> float:
    """What the current market value holds of dividend income on ``date_str`` (#369):
    income a DRIP lot has yet to be paid from, at its amount, and income taken in cash,
    in SPAXX at BIL's total return from its ex-date. 0.0 under adjusted levels."""
    d = date_str or date.today().isoformat()
    raw = _raw_close_on(d, account_id=get_portfolio_account_id())
    return raw.income if raw is not None else 0.0


def valuation_tickers(date_str: Optional[str] = None) -> "set[str]":
    """The tickers the current market value is priced from: every holding in the
    portfolio account, with SPAXX read as BIL, its price proxy (_spaxx_price)."""
    d = date_str or date.today().isoformat()
    holdings = get_holdings_on_date(d, account_id=get_portfolio_account_id())
    return {"BIL" if t == "SPAXX" else str(t) for t in holdings.index}


def _spaxx_price(d: str) -> "tuple[float, TickerStatus]":
    """SPAXX's value per share for the current market value: BIL's total return,
    normalized to $1.00 at the book's inception. SPAXX holds its $1.00 NAV and pays its
    income as new shares, which the ledger does not record, so this models that income.
    $1.00 when BIL does not cover the start, with SPAXX unresolved (_bil_for_spaxx).

    FROM THE INCEPTION OF THE BOOK BEING VALUED (#646, decided by the owner on October
    18, 2026), as the sleeve weights and the value series measure it. This and the
    sleeve weights measured from the literal 2025-05-01 while the value series measured
    from inception, so a book whose inception is later, with BIL stored from between
    the two, had SPAXX unresolved here and resolved there, and the Performance page's
    assertion that the two records agree failed."""
    p, st = _bil_for_spaxx(get_inception_date(account_id=get_portfolio_account_id()), d)
    try:
        if not p.empty:
            bil = total_return_series(p)
            p0  = bil.first_valid_index()
            if p0 is not None and float(bil[p0]) > 0:
                bil = bil / float(bil[p0])
            return float(bil.ffill().iloc[-1]), st
        return 1.0, st
    except Exception:
        return 1.0, st


def spaxx_modeled_income(date_str: Optional[str] = None) -> float:
    """What the current market value adds for SPAXX above its $1.00 NAV: the
    money-market income modeled at BIL's total return (see _spaxx_price). The
    Performance page's current value includes it; the Tax Lots page values SPAXX
    at NAV, as its lots are recorded, and holds no dividend income (dividend_income).
    The two pages' market values differ by exactly those two, and their unrealized
    gains do not differ at all. 0.0 with no SPAXX held."""
    d = date_str or date.today().isoformat()
    account_id = get_portfolio_account_id()
    holdings = get_holdings_on_date(d, account_id=account_id)
    if "SPAXX" not in holdings.index:
        return 0.0
    price, _st = _spaxx_price(d)
    nav = float(holdings.loc["SPAXX", "net_shares"])
    # Under the raw-close rule SPAXX holds the units its trades bought (_spaxx_units).
    raw = _raw_close_on(d, account_id=account_id)
    return raw.spaxx_units * price - nav if raw is not None else nav * (price - 1.0)


def get_inception_date(
    *, account_id: int, default: "str | None" = _UNSET
) -> "str | None":
    """ISO date of the first recorded trade for ``account_id`` — the window start.

    ``account_id`` is required (keyword-only): an unscoped MIN(trade_date) would
    move the whole analysis window if a second account's earlier trade landed.

    Two states used to share the literal "2025-05-01", and only one of them is a
    question:

    * **The query did not answer** — a locked DB, a renamed table, a permissions
      error. Always raises now; see the comment in the body.
    * **The account carries no trades** — genuinely has no inception. This is the
      caller's decision, and it is opt-in: omit ``default`` and it RAISES; pass
      ``default=None`` (or a date) and that is returned.

    The fallback is per-caller rather than in the return type because of
    reachability: ten of the eleven callers pass ``get_portfolio_account_id()``,
    and ``get_portfolio_account`` requires ``EXISTS (SELECT 1 FROM trades ...)``, so
    they CANNOT observe an empty ledger. Returning ``str | None`` to all of them
    would give ten callers a None branch that can never execute — and a dead
    defensive branch is where a wrong assumption gets written down and never
    tested. They keep a total function; the one caller with a real answer for the
    state (``drip.distribution_gaps_for_holdings``: no trades, no distributions)
    names it at the call site.
    """
    account_id = _require_account_id(account_id)
    # NO try/except. A locked database, a renamed table or a permissions error is
    # not an inception date, and the previous `except Exception: return
    # "2025-05-01"` answered all three with a confident one — 404 days before this
    # book's real inception, a window ~7.5x the portfolio's age of which ~87%
    # predates its existence. The underlying error IS the diagnosis; let it travel.
    with get_connection() as conn:
        row = conn.execute(
            "SELECT MIN(trade_date) FROM trades WHERE account_id = ?",
            (account_id,),
        ).fetchone()

    earliest = row[0] if row else None
    if not earliest:
        if default is _UNSET:
            raise ValueError(
                f"account {account_id} carries no trades, so it has no inception "
                "date. Refusing to invent one: this value is the left edge of every "
                "return window, the risk sample and the PDF cover period. A caller "
                "for which an empty ledger is a legitimate answer should say so "
                "explicitly with default=None."
            )
        return default

    try:
        date.fromisoformat(earliest)
    except (TypeError, ValueError):
        raise ValueError(
            f"MIN(trade_date) for account {account_id} is {earliest!r}, which is not "
            "an ISO date. Eleven callers feed this straight into date.fromisoformat; "
            "catching it here names the account instead of failing later without one."
        ) from None
    return earliest


def pair_legs(legs: "list[tuple[str, float]]", held: "list[str]") -> "dict[str, str] | None":
    """{leg: holding}: each leg with the holding of the same ticker, and one leg and one
    holding left over with each other. None for a single-fund benchmark, or when the
    holdings cannot be paired with the legs one to one."""
    if len(legs) < 2 or len(held) != len(legs):
        return None
    pairs = {t: t for t, _w in legs if t in held}
    free_legs = [t for t, _w in legs if t not in pairs]
    free_held = [t for t in held if t not in pairs.values()]
    if len(free_legs) > 1 or len(free_held) != len(free_legs):
        return None
    if free_legs:
        pairs[free_legs[0]] = free_held[0]
    return pairs


class BlendLeg(NamedTuple):
    holding: str                # the fund the sleeve holds
    leg: str                    # the benchmark leg it is set against
    leg_weight: float           # the leg's weight in the blend
    inception_weight: float     # the holding's share of the sleeve's inception buys
    current_weight: float       # the holding's share of the sleeve's value today


def blend_split(sleeve: str, date_str: Optional[str] = None) -> "list[BlendLeg] | None":
    """How a sleeve benchmarked to a blend holds its funds against the blend's legs
    (audit item 15c): each holding's share of the sleeve, bought at inception and held
    at ``date_str``, beside the weight of the leg it is set against.

    A leg pairs with the holding of the same ticker; one leg and one holding left over
    pair with each other (Real Assets: VNQ with VNQ, PDBC with the DBC leg). None when
    the sleeve's benchmark is a single fund, or its holdings cannot be paired with its
    legs one to one. Inception buys are the sleeve's trades on the portfolio's first
    day, DRIP lots excluded; today's shares include them."""
    from src.sleeve_config import parse_benchmark_spec
    d = date_str or date.today().isoformat()
    acct = get_portfolio_account_id()
    with get_connection() as conn:
        row = conn.execute("SELECT benchmark_ticker FROM asset_classes WHERE name = ? AND "
                           "parent_id IS NOT NULL", (sleeve,)).fetchone()
        held = [r[0] for r in conn.execute(
            "SELECT s.ticker FROM securities s JOIN asset_classes ac "
            "ON s.asset_class_id = ac.asset_class_id WHERE ac.name = ? "
            "AND ac.parent_id IS NOT NULL AND COALESCE(s.security_type, '') != 'benchmark' "
            "ORDER BY s.ticker", (sleeve,))]
    if row is None or not row[0]:
        return None
    legs = parse_benchmark_spec(row[0])
    pairs = pair_legs(legs, held)
    if pairs is None:
        return None

    inception = get_inception_date(account_id=acct)
    with get_connection() as conn:
        bought = dict(conn.execute(
            "SELECT ticker, SUM(shares * price) FROM trades WHERE account_id = ? AND "
            "trade_date = ? AND LOWER(action) = 'buy' AND (lot_source IS NULL OR "
            f"lot_source != 'drip') AND ticker IN ({','.join('?' * len(held))}) "
            "GROUP BY ticker", (acct, inception, *held)).fetchall())
    shares = get_holdings_on_date(d, account_id=acct)
    value = {}
    for t in held:
        n = float(shares["net_shares"].get(t, 0.0)) if not shares.empty else 0.0
        frame, _st = _price_and_status(t, look_back_start(t, d), d, account_id=acct)
        value[t] = n * float(frame["close"].iloc[-1]) if n and not frame.empty else 0.0
    if not all(bought.get(t) for t in held) or not all(value.values()):
        return None
    b_total, v_total = sum(bought[t] for t in held), sum(value.values())
    weight = dict(legs)
    return [BlendLeg(pairs[leg], leg, weight[leg], bought[pairs[leg]] / b_total,
                     value[pairs[leg]] / v_total) for leg, _w in legs]
