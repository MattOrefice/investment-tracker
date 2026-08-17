"""DRIP lot persistence — compute and persist DRIP reinvestment lots from inception.

SPAXX EXCLUSION: SPAXX is a money market sweep. Interest accrues to the cash
balance, not as new shares. SPAXX is excluded from all backfill operations.
Its return is proxied through BIL adj_close in get_portfolio_value_series and
is unchanged by this module.

PDBC NOTE: PDBC distributions have mixed tax character (qualified dividend,
return of capital, ordinary income). Phase 17 treats PDBC identically to other
ETFs — distributions are reinvested at payment-date adj_close. Tax-character
splitting for PDBC cost basis is deferred to a future phase.

PRICE CONVENTION: DRIP share counts and cost_basis_per_share use adj_close
(total-return basis), consistent with the TWR methodology used throughout.
This ensures get_portfolio_value_series produces identical results whether
DRIP is computed in-memory or from persisted lots.

PAYMENT DATE: DRIP executes on payment_date (the date Fidelity actually
reinvests the dividend), not on ex_dividend_date. Payment date is derived
as ex_date + PAYMENT_DATE_OFFSET_TRADING_DAYS trading days (weekday arithmetic,
Saturday/Sunday skipped). NYSE holidays are not currently modeled — a future
phase may add pandas_market_calendars for exact holiday-aware arithmetic.
"""
from __future__ import annotations

import logging
import warnings
from datetime import date, timedelta
from typing import NamedTuple, Optional

import pandas as pd

from src.db import get_connection
from src.prices import get_dividends, get_prices, total_return_series

_SPAXX_TICKER = "SPAXX"

# Every outcome a per-ticker backfill can have. Closed set, like coverage.py's
# UNRESOLVED_REASONS: a new skip reason has to be added here deliberately rather
# than travelling as an unrecognised string, because the operator's receipt renders
# off these values and an unknown one would fall through to no label at all.
BACKFILL_STATUSES = frozenset({
    "inserted",             # lots were written
    "current",              # nothing to write; the account really is up to date
    "no_distributions",     # no distribution history on record for the window
    "no_trades",            # no non-drip trades for this ticker in this account
    "price_fetch_failed",   # prices could not be fetched; lots NOT computed
})

# The three that are NOT a statement about the book being current. Reporting any of
# them as "0 (already current)" tells the operator the opposite of what happened.
_SKIPPED_STATUSES = frozenset({"no_distributions", "no_trades", "price_fetch_failed"})


class BackfillResult(NamedTuple):
    """Per-ticker outcome of a backfill run: the count AND why it is that count.

    ``inserted`` alone cannot distinguish "nothing to do" from "could not tell" —
    a zero from a failed price fetch and a zero from an already-current book are
    the same integer. Carrying the reason alongside the value is the same shape as
    PriceCoverage travelling beside a price frame, for the same reason.
    """
    inserted: int
    status: str
    detail: str = ""

    @property
    def skipped(self) -> bool:
        """True when no lots were computed, so this ticker's zero is not a result."""
        return self.status in _SKIPPED_STATUSES


def _require_account_id(account_id) -> int:
    """Reject a missing scope loudly.

    A None account_id reads as "no filter" in every query in this module — the
    account-blind read that is the whole defect. Mirrors
    src.holdings._require_account_id with a message naming this module's paths;
    kept local rather than imported so drip.py does not take a top-level dependency
    on holdings (which imports prices and db, and is imported lazily below).
    """
    if account_id is None:
        raise ValueError(
            "account_id is required — DRIP lots are computed from, and written to, "
            "ONE account's trade ledger and will not silently span every account. "
            "Pass the account to scope to (typically get_portfolio_account_id())."
        )
    return int(account_id)


def _traded_tickers(conn, account_id: int) -> list[str]:
    """DISTINCT tickers traded in ``account_id``, SPAXX excluded.

    Shared by backfill_all_drip_lots and distribution_gaps_for_holdings, which held
    character-identical copies of this query. They are the same question and must
    stay the same answer: the gap notice exists to name what the backfill skipped,
    so a divergence would report gaps for tickers the backfill never considered.
    """
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM trades WHERE account_id = ? AND ticker != ? "
        "ORDER BY ticker",
        (account_id, _SPAXX_TICKER),
    ).fetchall()
    return [r["ticker"] for r in rows]

# Trading days to add to ex_dividend_date to derive payment_date.
# Vanguard, iShares, Schwab, and Invesco ETFs in the SAA typically execute
# DRIP reinvestment 1–3 trading days after ex-date; +2 is the adopted default.
PAYMENT_DATE_OFFSET_TRADING_DAYS = 2

# Per-ticker overrides if a specific fund is known to deviate from the default.
# Keys are ticker symbols; values are integer trading-day offsets.
_PAYMENT_DATE_OVERRIDES: dict[str, int] = {}


def derive_payment_date(ex_date: date, ticker: str = "") -> date:
    """Return the DRIP payment date for a distribution with the given ex_date.

    Adds PAYMENT_DATE_OFFSET_TRADING_DAYS (or a per-ticker override) trading
    days to ex_date, skipping Saturday and Sunday. NYSE holidays are not
    modeled (no pandas_market_calendars dependency).

    Args:
        ex_date: The ex-dividend date of the distribution.
        ticker:  Ticker symbol, used to look up per-ticker offset overrides.

    Returns:
        The derived payment date as a datetime.date.
    """
    offset = _PAYMENT_DATE_OVERRIDES.get(ticker, PAYMENT_DATE_OFFSET_TRADING_DAYS)
    d = ex_date
    added = 0
    while added < offset:
        d += timedelta(days=1)
        if d.weekday() < 5:  # Mon=0 … Fri=4
            added += 1
    return d


def fetch_distributions(
    ticker: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """
    Return historical distributions for ticker as a DataFrame.

    Columns: ex_date (datetime.date), dividend_per_share (float).
    Rows sorted chronologically. Returns empty DataFrame if none in window.
    Pulls from the dividends table (already populated via fetch_prices).
    """
    s = get_dividends(ticker, start_date, end_date)
    if s.empty:
        return pd.DataFrame(columns=["ex_date", "dividend_per_share"])
    df = pd.DataFrame({"ex_date": s.index, "dividend_per_share": s.values})
    df["ex_date"] = pd.to_datetime(df["ex_date"]).dt.date
    return df.sort_values("ex_date").reset_index(drop=True)


def compute_drip_lots(
    ticker: str,
    initial_shares: pd.Series,
    distributions: pd.DataFrame,
    price_history: pd.Series,
) -> list[dict]:
    """
    Compute DRIP reinvestment lots. Pure function — no DB access.

    For each distribution event, in chronological order:
      1. shares_before = initial shares held strictly BEFORE ex_date
                         + cumulative DRIP shares from prior events
      2. payment_date  = derive_payment_date(ex_date, ticker)
      3. price         = adj_close on or before payment_date
      4. new_shares    = (shares_before × dividend_per_share) / price
      5. cumulative_drip += new_shares  (compounding for subsequent events)

    The dividend entitlement is determined by shares held on ex_date (Fidelity
    pays based on the record date, which is T+1 from ex_date). Reinvestment
    executes at the payment_date closing price, matching Fidelity DRIP mechanics.

    Events where shares_before == 0 are skipped. This correctly excludes
    distributions dated on or before inception when no portfolio existed yet.

    Args:
        ticker: ticker symbol, stored on each returned lot dict.
        initial_shares: cumulative NON-DRIP shares indexed by datetime.date,
            built from buy/sell trades with lot_source != 'drip'.
        distributions: DataFrame with ex_date (datetime.date) and
            dividend_per_share (float), sorted chronologically.
        price_history: adj_close Series indexed by datetime.date.

    Returns:
        List of lot dicts with keys: ticker, ex_date (datetime.date),
        purchase_date (datetime.date, = payment_date), shares (float),
        cost_basis_per_share (float), lot_source='drip'.
        Empty list if no qualifying events.
    """
    if distributions.empty:
        return []

    cumulative_drip = 0.0
    lots: list[dict] = []

    for _, row in distributions.iterrows():
        ex_date = row["ex_date"]  # datetime.date
        dps     = float(row["dividend_per_share"])

        # Shares held strictly before ex_date (dividend entitlement date)
        before = initial_shares[initial_shares.index < ex_date]
        shares_initial = float(before.iloc[-1]) if not before.empty else 0.0
        shares_before  = shares_initial + cumulative_drip

        if shares_before <= 0:
            continue

        # Derive payment_date and look up adj_close on or before it
        payment_date = derive_payment_date(ex_date, ticker)
        avail = price_history[price_history.index <= payment_date].dropna()
        if avail.empty:
            continue
        price = float(avail.iloc[-1])
        if price <= 0:
            continue

        cash_div   = shares_before * dps
        new_shares = cash_div / price
        cumulative_drip += new_shares

        lots.append(
            {
                "ticker":               ticker,
                "ex_date":              ex_date,
                "purchase_date":        payment_date,
                "shares":               new_shares,
                "cost_basis_per_share": price,
                "lot_source":           "drip",
            }
        )

    return lots


def persist_drip_lots(ticker: str, lots: list[dict], *, account_id: int) -> int:
    """
    Insert DRIP lots into ONE account's trade ledger.

    ``account_id`` is required (keyword-only) and never resolved here. It used to be
    guessed — ``WHERE is_active=1 ORDER BY account_id LIMIT 1`` — which wrote to
    whichever account happened to sort first and raised only when there were no
    active accounts at all, never on ambiguity. The write target of a synthetic
    trade row is an identity, not a default.

    Idempotent within the account: a lot is skipped if a drip row already exists for
    the same (account_id, ticker, trade_date). The account_id is part of that key
    deliberately — without it, one account's drip row suppressed another account's
    insert and the return value reported zero new work rather than a conflict.

    Returns count of new rows inserted.
    """
    account_id = _require_account_id(account_id)   # before the early return: a None
    if not lots:                                   # scope is wrong whether or not
        return 0                                   # there is anything to write

    with get_connection() as conn:
        inserted = 0
        for lot in lots:
            pd = lot["purchase_date"]
            date_str = pd.isoformat() if hasattr(pd, "isoformat") else str(pd)

            already = conn.execute(
                "SELECT 1 FROM trades WHERE account_id=? AND ticker=? AND trade_date=? "
                "AND lot_source='drip'",
                (account_id, ticker, date_str),
            ).fetchone()
            if already:
                continue

            conn.execute(  # write-guard-exempt: admin backfill tool, not user-triggered
                """INSERT INTO trades
                   (account_id, ticker, trade_date, action, shares, price,
                    fees, notes, lot_source)
                   VALUES (?, ?, ?, 'Buy', ?, ?, 0, 'DRIP', 'drip')""",
                (
                    account_id,
                    ticker,
                    date_str,
                    float(lot["shares"]),
                    float(lot["cost_basis_per_share"]),
                ),
            )
            inserted += 1

    return inserted


def backfill_all_drip_lots(
    start_date: str = "2025-05-01",
    end_date: str | None = None,
    *,
    account_id: int,
) -> dict[str, BackfillResult]:
    """
    Backfill DRIP lots for ONE account's holdings, start_date to end_date (today).

    Skips SPAXX (money market sweep; no DRIP reinvestment as shares).
    Safe to re-run: persist_drip_lots skips already-inserted lots.

    ACCOUNT SCOPE (required, keyword-only). Both the ticker list and the share
    timeline the lots are computed FROM read only ``account_id``. They used to read
    across every account while the write landed in a single guessed one, so a lot
    could be correctly attributed and still carry a household-wide share count —
    the failure mode that looks right, and the reason scoping only the write would
    have been worse than leaving it alone.

    Returns dict mapping ticker → BackfillResult. The status half is not decoration:
    a zero from a failed price fetch, a zero from an absent distribution history and
    a zero from an already-current book are the same integer, and the operator's
    receipt used to render all three as "0 (already current)".

    Raises ValueError when ``account_id`` carries no traded tickers. The intended
    caller resolves the id through get_portfolio_account(), whose EXISTS-on-trades
    clause already excludes a tradeless account, so reaching here means a wrong id
    was passed programmatically — and an empty result dict renders identically to a
    book that needed nothing.
    """
    account_id = _require_account_id(account_id)
    end = end_date or date.today().isoformat()

    with get_connection() as conn:
        tickers = _traded_tickers(conn, account_id)

    if not tickers:
        raise ValueError(
            f"account_id={account_id} has no trades, so there is no ledger to "
            "reconstruct DRIP lots against. Refusing to report an empty backfill as "
            "a completed one — check the account being passed."
        )

    results: dict[str, BackfillResult] = {}

    for ticker in tickers:
        distributions = fetch_distributions(ticker, start_date, end)
        if distributions.empty:
            warnings.warn(
                f"{ticker}: no distributions found in {start_date}–{end}; skipping."
            )
            results[ticker] = BackfillResult(
                0, "no_distributions", f"none on record in {start_date}–{end}"
            )
            continue

        # Cumulative non-DRIP shares timeline (buys minus sells, excluding drip lots)
        # — this account's only, which is what makes the computed lot size correct
        # for the account it is about to be written to.
        with get_connection() as conn:
            trade_rows = conn.execute(
                """SELECT trade_date,
                          SUM(CASE WHEN LOWER(action)='buy' THEN shares ELSE -shares END) AS net
                   FROM trades
                   WHERE ticker = ?
                     AND account_id = ?
                     AND (lot_source IS NULL OR lot_source != 'drip')
                   GROUP BY trade_date
                   ORDER BY trade_date""",
                (ticker, account_id),
            ).fetchall()

        if not trade_rows:
            # Reachable because the ticker list above has no lot_source filter: a
            # ticker whose only rows in this account are drip lots arrives here.
            # Pre-existing silent zero, now at least named in the receipt (#207).
            results[ticker] = BackfillResult(
                0, "no_trades", "no non-drip trades for this ticker in this account"
            )
            continue

        dates          = [date.fromisoformat(r["trade_date"]) for r in trade_rows]
        nets           = [float(r["net"]) for r in trade_rows]
        initial_shares = pd.Series(nets, index=dates).cumsum()

        try:
            price_df = get_prices(ticker, start_date, end)
        except Exception as exc:
            warnings.warn(f"{ticker}: price fetch failed ({exc}); skipping.")
            results[ticker] = BackfillResult(0, "price_fetch_failed", str(exc))
            continue

        price_history       = total_return_series(price_df)
        price_history.index = pd.to_datetime(price_history.index).date

        lots = compute_drip_lots(ticker, initial_shares, distributions, price_history)
        n    = persist_drip_lots(ticker, lots, account_id=account_id)
        results[ticker] = BackfillResult(n, "inserted" if n else "current")

    return results


def drip_shares_by_ticker(*, account_id: int) -> list[tuple[str, float]]:
    """Total persisted DRIP shares per ticker, for ONE account.

    Backs the post-backfill summary the operator sees. Account-scoped for the same
    reason the write is: an unscoped SUM credits other accounts' existing lots to
    the run that just finished, in the only output the run produces.
    """
    account_id = _require_account_id(account_id)
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT ticker, SUM(shares) AS total_drip_shares
               FROM trades
               WHERE lot_source='drip' AND account_id = ?
               GROUP BY ticker ORDER BY ticker""",
            (account_id,),
        ).fetchall()
    return [(r["ticker"], float(r["total_drip_shares"])) for r in rows]


_STATUS_LABELS = {
    "current":            "0 (already current)",
    "no_distributions":   "0 — SKIPPED: no distributions on record",
    "no_trades":          "0 — SKIPPED: no non-drip trades",
    "price_fetch_failed": "0 — SKIPPED: price fetch FAILED",
}


def format_backfill_report(results: dict[str, BackfillResult]) -> str:
    """Render a backfill run so that no zero claims to be a result it is not.

    Three of the four ways this run can produce a zero are not "already current",
    and the warnings that distinguish them go to stderr, detached from the table the
    operator actually reads. So the reason is rendered inline, and a run that
    skipped anything refuses to present its total as complete — the same rule the
    coverage substrate applies to a value with unresolved inputs.
    """
    lines = [f"  {'Ticker':<8}  {'New DRIP Lots':>38}", "  " + "-" * 50]
    for ticker, r in sorted(results.items()):
        label = str(r.inserted) if r.inserted else _STATUS_LABELS[r.status]
        lines.append(f"  {ticker:<8}  {label:>38}")

    total   = sum(r.inserted for r in results.values())
    skipped = {t: r for t, r in sorted(results.items()) if r.skipped}

    lines.append("")
    lines.append(f"  Total new DRIP lots inserted: {total}")

    if skipped:
        lines.append(
            f"  INCOMPLETE: {len(skipped)} of {len(results)} tickers were skipped "
            f"without a result, so the total above covers only the "
            f"{len(results) - len(skipped)} that completed:"
        )
        for ticker, r in skipped.items():
            detail = f" ({r.detail})" if r.detail else ""
            lines.append(f"    {ticker} — {r.status.replace('_', ' ')}{detail}")

    return "\n".join(lines)


def distribution_gaps_for_holdings(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    *,
    account_id: int,
) -> list[str]:
    """Tickers traded in ``account_id`` (excluding SPAXX) with NO distribution history
    on record over [start_date, end_date] — the same condition backfill_all_drip_lots
    warns and skips on (see above), detected live so it can be surfaced in-app
    rather than only in that CLI script's console output.

    Account-scoped (keyword-only, required): reads only the portfolio account's
    traded tickers, so a differently-scoped account's holdings never trigger a
    distribution-gap notice on a report whose value figure is this account's alone.

    A ticker in the returned list means its DRIP lots could not be computed, so
    the displayed current-market-value figure (get_current_market_value, which
    includes DRIP-reinvested shares) may understate that holding. Read-only
    detection — does not compute, persist, or alter any DRIP lot.

    Returns an empty list when every held ticker has at least one distribution
    on record (the common case). Reuses fetch_distributions verbatim, so this
    adds no new distribution-fetching logic beyond what backfill already runs.
    """
    account_id = _require_account_id(account_id)
    if start_date is None:
        from src.holdings import get_inception_date
        # default=None: this is the one caller for which an empty ledger is a
        # legitimate answer rather than an error — an account with no trades has no
        # distributions to be missing, so the gap list is empty. Every other caller
        # omits `default` and keeps a total function (see get_inception_date).
        start_date = get_inception_date(account_id=account_id, default=None)
        if start_date is None:
            return []
    end = end_date or date.today().isoformat()

    with get_connection() as conn:
        tickers = _traded_tickers(conn, account_id)

    gaps: list[str] = []
    for ticker in tickers:
        distributions = fetch_distributions(ticker, start_date, end)
        if distributions.empty:
            logging.warning(
                "%s: no distributions found in %s–%s; DRIP reinvestment "
                "could not be computed, so the displayed current-market-value "
                "figure may understate this holding.",
                ticker, start_date, end,
            )
            gaps.append(ticker)
    return gaps


def drip_distribution_gap_notice(tickers: list[str]) -> str:
    """Single-source user-facing notice for tickers with no distribution history
    on record — the displayed current-market-value figure (which includes
    DRIP-reinvested shares) may understate these holdings. Shared across the
    Performance page and the PDF report so the wording can't drift between them,
    mirroring src.attribution.price_gap_notice for the analogous price gap.
    """
    if not tickers:
        return ""
    parts = ", ".join(tickers)
    return (
        f"Distribution data unavailable for {parts} — DRIP reinvestment could "
        "not be computed, so the displayed current market value may understate "
        "this holding."
    )
