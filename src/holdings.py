"""Derive holdings and portfolio value from the trades ledger.

Every trade-derived read is SCOPED to an explicit account. The trade-derived
"portfolio" is one account — the self-directed taxable book — and reading across
all accounts (the pre-Part-B defect) silently folds a Roth or IRA trade into a
figure labelled "Portfolio". The base reads therefore REQUIRE a keyword-only
``account_id`` and raise on None; the account identity itself is resolved in
exactly one place, ``get_portfolio_account()``, which raises rather than guessing.
"""
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from src.db import get_connection
from src.prices import get_prices, _to_iso


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
        return pd.Series(0.0, index=date_range)

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
    for ticker in tickers:
        if ticker == "SPAXX":
            try:
                p = get_prices("BIL", start_date, end)
                p.index = pd.to_datetime(p.index)
                series = p["adj_close"].fillna(p["close"])
                p0 = series.first_valid_index()
                if p0 is not None and float(series[p0]) > 0:
                    series = series / float(series[p0])  # normalize to $1.00
                price_cols[ticker] = series.reindex(date_range).ffill()
            except Exception:
                price_cols[ticker] = pd.Series(1.0, index=date_range)
        else:
            try:
                p = get_prices(ticker, start_date, end)
                p.index = pd.to_datetime(p.index)
                series = p["adj_close"].fillna(p["close"])
                price_cols[ticker] = series.reindex(date_range).ffill()
            except Exception:
                price_cols[ticker] = pd.Series(dtype=float, index=date_range)

    prices_matrix = pd.DataFrame(price_cols, index=date_range)

    common = [t for t in tickers if t in prices_matrix.columns]

    # DRIP reinvestment shares (lot_source='drip') are deliberately EXCLUDED by
    # the SQL query above: adj_close already embeds dividend reinvestment, so
    # adding the DRIP shares would double-count income. See the docstring.

    return (holdings_matrix[common] * prices_matrix[common]).sum(axis=1)


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
    if holdings.empty:
        return pd.DataFrame()

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
            try:
                inception = "2025-05-01"
                p = get_prices("BIL", inception, date_str)
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
            try:
                p = get_prices(ticker, look_back, date_str)
                price = float(p["close"].iloc[-1]) if not p.empty else 0.0
            except Exception:
                price = 0.0

        values_by_sleeve[sleeve] = values_by_sleeve.get(sleeve, 0.0) + shares * price

    total = sum(values_by_sleeve.values())
    if total == 0:
        return pd.DataFrame()

    # Ex-cash denominator: strategic weights are a share of INVESTED value.
    cash_mv  = values_by_sleeve.get(_CASH_SLEEVE, 0.0)
    invested = total - cash_mv
    if invested <= 0:
        return pd.DataFrame()

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
    return df


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
    d = date_str or date.today().isoformat()
    # Wrapper: resolves the portfolio account and passes it through.
    holdings = get_holdings_on_date(d, account_id=get_portfolio_account_id())  # all shares, incl DRIP lots
    if holdings.empty:
        return 0.0

    look_back = (date.fromisoformat(d) - timedelta(days=7)).isoformat()
    total = 0.0
    for ticker, row in holdings.iterrows():
        shares = float(row["net_shares"])
        if ticker == "SPAXX":
            try:
                p = get_prices("BIL", "2025-05-01", d)
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
            try:
                p = get_prices(ticker, look_back, d)
                price = float(p["close"].iloc[-1]) if not p.empty else 0.0
            except Exception:
                price = 0.0
        total += shares * price
    return round(total, 2)


def get_inception_date(*, account_id: int) -> str:
    """Return ISO date of the first recorded trade FOR ``account_id`` (the
    portfolio window start), falling back to '2025-05-01'.

    ``account_id`` is required (keyword-only): an unscoped MIN(trade_date) would
    move the whole analysis window if a second account's earlier trade landed.
    """
    account_id = _require_account_id(account_id)
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT MIN(trade_date) FROM trades WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        return row[0] if row and row[0] else "2025-05-01"
    except Exception:
        return "2025-05-01"
