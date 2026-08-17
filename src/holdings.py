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

from src.coverage import PriceCoverage, TickerStatus, coverage_from_statuses
from src.db import get_connection
from src.prices import get_prices, classify_miss, _to_iso


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


def _price_and_status(
    ticker: str,
    start: str,
    end: str,
    *,
    price_ticker: Optional[str] = None,
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
    """
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
    return frame, TickerStatus(ticker, True, served_through=served)


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
        raise ValueError(
            "cannot resolve the portfolio account: no active self-directed taxable "
            "account carries a trade ledger. Refusing to guess a scope for the "
            "trade-derived 'Portfolio' views."
        )
    raise ValueError(
        "cannot resolve the portfolio account uniquely: "
        f"{len(rows)} self-directed taxable accounts carry trades "
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
    strictly before ``today``: the latest date on which EVERY holding the report
    consumes has a real, already-stored price. Returns an ISO date string, or
    ``None`` when no holding has any committed price.

    Three deliberate choices, each load-bearing:

    - **MIN, not MAX.** ``last_real_price_date`` returns the max, which
      over-promises: one ticker reaching a later date does not make the portfolio
      priceable there. The report consumes every holding, so the honest frontier
      is the one they all clear.
    - **``price_date < today``**, so a partial same-day bar written by a live
      mid-session fetch can never present itself as a settled frontier (the same
      reasoning as ``last_settled_price_date``).
    - **No fetch.** This answers "what does the committed data already support",
      which is precisely the question a gap-filling read would destroy by going
      and filling the gap. It is also called on every page render, so it must
      stay a couple of indexed ``MAX()`` lookups.

    Returns ``None`` — read by callers as "no opinion", not as "nothing is
    supported" — when the account or its holdings cannot be resolved. That keeps
    a cap built on this from being the thing that takes down a report: a DB with
    no holdings or no prices has no figures to misdate in the first place.
    """
    ref = today or date.today()
    if isinstance(ref, str):
        ref = date.fromisoformat(ref)
    ref_iso = ref.isoformat()

    try:
        if account_id is None:
            account_id = get_portfolio_account_id()
        holdings = get_holdings_on_date(ref_iso, account_id=account_id)
    except Exception:
        # No resolvable portfolio account / no ledger — see the docstring.
        return None
    if holdings.empty:
        return None

    frontiers: list[str] = []
    with get_connection() as conn:
        for ticker in holdings.index:
            price_ticker = "BIL" if ticker == "SPAXX" else ticker  # SPAXX proxied via BIL
            row = conn.execute(
                "SELECT MAX(price_date) FROM prices WHERE ticker = ? AND price_date < ?",
                (price_ticker, ref_iso),
            ).fetchone()
            # A holding with no committed price at all is an already-surfaced
            # price gap (see brinson_fachler_period's price_gaps), not a frontier
            # signal — skip it rather than collapsing the frontier to nothing.
            if row and row[0]:
                frontiers.append(row[0])
    return min(frontiers) if frontiers else None


def last_settled_price_date(start_date: str, end_date: Optional[str] = None) -> str:
    """Last COMPLETE settled trading day — the anchor for DISPLAYED period returns.

    Unlike last_real_price_date (which can return *today* when a live mid-session
    fetch has cached a partial intraday bar), this excludes the current day so an
    in-progress bar is never used as a period-window endpoint — which would swing
    displayed 1M/3M returns by the intraday move (~1:1). Simple, robust proxy: the
    last real price date strictly before today (always settled). Trade-off: on a
    fully-settled today (post-close) the display is one day stale rather than
    risking a timezone-fragile post-close check — robustness over cleverness.

    On frozen/committed data (frontier < today) this EQUALS last_real_price_date,
    so the deterministic BF reconciliation test (which anchors on last_real) is
    unaffected.
    """
    today = date.today()
    end = end_date or today.isoformat()
    yesterday = (today - timedelta(days=1)).isoformat()
    cap = min(end, yesterday)  # ISO strings compare lexicographically
    return last_real_price_date(start_date, cap)


def get_portfolio_value_series(
    start_date: str,
    end_date: Optional[str] = None,
    *,
    account_id: int,
) -> pd.Series:
    """Daily total portfolio market value — see _portfolio_value_series_impl.

    Unchanged for existing callers. Use portfolio_value_series_with_coverage where
    the gap has to be disclosed; both share one valuation loop.
    """
    series, _statuses = _portfolio_value_series_impl(
        start_date, end_date, account_id=account_id)
    return series


def portfolio_value_series_with_coverage(
    start_date: str,
    end_date: Optional[str] = None,
    *,
    account_id: int,
) -> "ValueSeries":
    """get_portfolio_value_series, plus the coverage record for its prices.

    This series drives every TWR figure and every risk metric on the Performance
    page, and it is where an unpriceable holding is hardest to see: its price
    column is all-NaN, and .sum(axis=1) defaults to skipna=True, so the holding
    contributes exactly zero dollars to every day without a NaN ever surfacing.
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
    Uses adj_close for ETF prices (total-return basis).
    SPAXX is valued at $1/share.
    Weekends / holidays are forward-filled from the previous trading day.

    DRIP lots (lot_source='drip') are EXCLUDED from the share count used here.
    adj_close already embeds dividend reinvestment into the existing shares, so
    counting the DRIP-reinvested shares on top of it would double-count income
    and overstate the total-return TWR (worst on the high-yielding FI sleeves).
    Valuing the actual (non-DRIP) shares at adj_close gives the correct total
    return. DRIP lots remain in the trades table for cost-basis / tax-lot
    displays (see src/tax_lots.py); only this value series excludes them.
    Personal mode holds no DRIP lots (pay-to-cash), so it is unaffected.
    """
    account_id = _require_account_id(account_id)
    end = end_date or date.today().isoformat()
    date_range = pd.date_range(start=start_date, end=end, freq="D")

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

    daily_changes = (
        trades_df.groupby(["trade_date", "ticker"])["signed"]
        .sum()
        .unstack(fill_value=0)
        .reindex(full_range, fill_value=0)
    )

    # Cumulative holdings at each date
    holdings_matrix = daily_changes.cumsum().clip(lower=0)

    # Slice to requested range (full_range ⊇ date_range since start ≤ start_date)
    holdings_matrix = holdings_matrix.reindex(date_range)

    tickers = holdings_matrix.columns.tolist()

    # Build price matrix (adj_close, forward-filled for non-trading days).
    # SPAXX NAV is always $1, but it accrues T-bill yield.  We proxy its total
    # return through BIL (SPDR Bloomberg 1-3 Month T-Bill ETF), whose adj_close
    # already reflects reinvested T-bill yield.  BIL is normalized to start at
    # $1.00 so that shares × price still equals dollars of SPAXX held.
    price_cols: dict = {}
    statuses: "list[TickerStatus]" = []
    for ticker in tickers:
        if ticker == "SPAXX":
            p, st = _price_and_status(ticker, start_date, end, price_ticker="BIL")
            statuses.append(st)
            try:
                p.index = pd.to_datetime(p.index)
                series = p["adj_close"].fillna(p["close"])
                p0 = series.first_valid_index()
                if p0 is not None and float(series[p0]) > 0:
                    series = series / float(series[p0])  # normalize to $1.00
                price_cols[ticker] = series.reindex(date_range).ffill()
            except Exception:
                price_cols[ticker] = pd.Series(1.0, index=date_range)
        else:
            p, st = _price_and_status(ticker, start_date, end)
            if not st.resolved:
                # Branch on the STATUS, not on an exception: _price_and_status
                # reports a miss instead of raising, and letting an empty frame
                # flow through the arithmetic below would build an object-dtype
                # NaN column where the original built a float one. Same zero
                # dollars either way, but this keeps the column byte-identical.
                price_cols[ticker] = pd.Series(dtype=float, index=date_range)
                statuses.append(st)
                continue
            try:
                p.index = pd.to_datetime(p.index)
                series = p["adj_close"].fillna(p["close"])
                price_cols[ticker] = series.reindex(date_range).ffill()
            except Exception:
                # An all-NaN column contributes exactly zero dollars to every day
                # of the series, because .sum(axis=1) below defaults to
                # skipna=True — no NaN escapes to warn anyone (#188). The status
                # is the only record that this holding was not valued.
                price_cols[ticker] = pd.Series(dtype=float, index=date_range)
                st = replace(st, resolved=False, reason="empty_window",
                             served_through=None)
            statuses.append(st)

    prices_matrix = pd.DataFrame(price_cols, index=date_range)

    common = [t for t in tickers if t in prices_matrix.columns]

    # DRIP reinvestment shares (lot_source='drip') are deliberately EXCLUDED by
    # the SQL query above: adj_close already embeds dividend reinvestment, so
    # adding the DRIP shares would double-count income. See the docstring.

    value = (holdings_matrix[common] * prices_matrix[common]).sum(axis=1)
    return value, statuses


def get_external_cashflow_series(
    start_date: str,
    end_date: Optional[str] = None,
    *,
    account_id: int,
) -> pd.Series:
    """
    Return the daily net EXTERNAL cash flow into the portfolio (signed dollars:
    contributions positive, withdrawals negative) over [start_date, end_date],
    zero on days with no flow. This is the flow series the TWR functions
    (src/returns.py) and compute_risk_metrics (src/performance.py) net out so
    that deposited cash is not counted as market return.

    The trades ledger stores no dollar amount, so each flow is reconstructed as
    shares × price per trade_date (write_trades_batch asserts the per-deploy sum
    matches the logged contribution). Buys are cash entering the measured
    portfolio, sells cash leaving it; fees always land as a performance cost
    (a buy costs shares × price + fees, a sell yields shares × price − fees).
    Same-date buy/sell pairs (internal rebalances, e.g. sell VOO → buy SPAXX)
    net toward zero because flows are summed per date.

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

    with get_connection() as conn:
        rows = conn.execute(
            """SELECT trade_date,
                      SUM(CASE WHEN LOWER(action) = 'buy'
                               THEN shares * price ELSE -shares * price END
                          + COALESCE(fees, 0)) AS flow
               FROM trades
               WHERE trade_date >= ? AND trade_date <= ? AND account_id = ?
                 AND (lot_source IS NULL OR lot_source != 'drip')
               GROUP BY trade_date""",
            (start_date, end, account_id),
        ).fetchall()

    flows = pd.Series(0.0, index=date_range)
    for r in rows:
        ts = pd.Timestamp(r["trade_date"])
        if ts in flows.index:
            flows[ts] = float(r["flow"])
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
    holdings = get_holdings_on_date(date_str, account_id=get_portfolio_account_id())
    statuses: "list[TickerStatus]" = []
    if holdings.empty:
        return pd.DataFrame(), statuses

    with get_connection() as conn:
        sec_rows = conn.execute(
            """SELECT s.ticker, ac.name AS sleeve
               FROM securities s
               JOIN asset_classes ac ON s.asset_class_id = ac.asset_class_id""",
        ).fetchall()
        ticker_to_sleeve = {r["ticker"]: r["sleeve"] for r in sec_rows}

        target_rows = conn.execute(
            """SELECT name, target_weight
               FROM asset_classes WHERE parent_id IS NOT NULL""",
        ).fetchall()
        target_weights = {r["name"]: r["target_weight"] for r in target_rows}

    # Look back up to 5 trading days to find the most recent price
    look_back = (date.fromisoformat(date_str) - timedelta(days=7)).isoformat()

    values_by_sleeve: dict[str, float] = {}
    for ticker, row in holdings.iterrows():
        sleeve = ticker_to_sleeve.get(ticker, "Unknown")
        shares = float(row["net_shares"])

        if ticker == "SPAXX":
            # Priced off BIL. The status is recorded under SPAXX, the holding
            # being valued; that this is a PROXY instrument is a substitution, and
            # reporting it belongs to the PR that populates that field.
            inception = "2025-05-01"
            p, st = _price_and_status(ticker, inception, date_str, price_ticker="BIL")
            statuses.append(st)
            try:
                if not p.empty:
                    bil = p["adj_close"].fillna(p["close"])
                    p0_idx = bil.first_valid_index()
                    if p0_idx is not None and float(bil[p0_idx]) > 0:
                        bil = bil / float(bil[p0_idx])
                    price = float(bil.ffill().iloc[-1])
                else:
                    price = 1.0
            except Exception:
                price = 1.0
        else:
            p, st = _price_and_status(ticker, look_back, date_str)
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
    (including DRIP lots) valued at the latest RAW close, plus cash.

    This is DISTINCT from get_portfolio_value_series (adj_close × NON-DRIP shares),
    which is the total-return series used for RETURNS. DRIP shares are real shares
    with real market worth, so they belong in the dollar value — even though
    adj_close already embeds their income for return purposes. The two answer
    different questions (what is the account worth today vs. what was the
    total return), so they use different share bases.

    Display-only: this figure must not feed any return calculation. Mirrors the
    valuation in get_sleeve_weights_on_date (raw close; SPAXX proxied via
    BIL adj_close normalized to $1 at inception).
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
    holdings = get_holdings_on_date(d, account_id=get_portfolio_account_id())  # all shares, incl DRIP lots
    statuses: "list[TickerStatus]" = []
    if holdings.empty:
        return 0.0, statuses

    look_back = (date.fromisoformat(d) - timedelta(days=7)).isoformat()
    total = 0.0
    for ticker, row in holdings.iterrows():
        shares = float(row["net_shares"])
        if ticker == "SPAXX":
            p, st = _price_and_status(ticker, "2025-05-01", d, price_ticker="BIL")
            statuses.append(st)
            try:
                if not p.empty:
                    bil = p["adj_close"].fillna(p["close"])
                    p0  = bil.first_valid_index()
                    if p0 is not None and float(bil[p0]) > 0:
                        bil = bil / float(bil[p0])
                    price = float(bil.ffill().iloc[-1])
                else:
                    price = 1.0
            except Exception:
                price = 1.0
        else:
            p, st = _price_and_status(ticker, look_back, d)
            try:
                price = float(p["close"].iloc[-1]) if not p.empty else 0.0
            except Exception:
                price = 0.0
                st = replace(st, resolved=False, reason="empty_window",
                             served_through=None)
            statuses.append(st)
        total += shares * price
    return round(total, 2), statuses


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
