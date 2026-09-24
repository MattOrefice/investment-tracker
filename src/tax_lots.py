"""Tax lot inventory derived from the trades ledger.

The trades table is the lot ledger. Each BUY row is one lot. SELLs are matched
against open lots on a FIFO basis. DRIP reinvestments are persisted as separate
lot rows with lot_source='drip'; discretionary trades carry lot_source='initial'.
"""
from datetime import date
from typing import Optional

import pandas as pd

from src.db import get_connection
from src.prices import get_prices, total_return_series

# IRS "more than one year" = strictly more than 365 calendar days.
# Day 366 is the first day of long-term treatment.
_LT_THRESHOLD_DAYS = 366

# Minimum per-lot loss magnitude (dollars) to count as a harvest candidate.
# Below this level, round-trip transaction friction typically exceeds the benefit.
HARVEST_MATERIALITY_THRESHOLD = 25.0


# ── Pure computation functions (no DB, fully testable) ────────────────────────

def compute_days_held(purchase_date: date, as_of: date) -> int:
    """Calendar days elapsed from purchase_date to as_of (exclusive of as_of)."""
    return (as_of - purchase_date).days


def compute_tax_status(days_held: int) -> str:
    """Return 'LT' if days_held >= 366 (more than one year), else 'ST'."""
    return "LT" if days_held >= _LT_THRESHOLD_DAYS else "ST"


def compute_days_to_lt(days_held: int) -> int:
    """Calendar days until LT qualification; 0 for lots already qualifying."""
    return max(0, _LT_THRESHOLD_DAYS - days_held)


def compute_unrealized_gl(shares: float, cost_per_share: float, current_price: float) -> float:
    """Unrealized gain/(loss) in dollars. Positive = gain, negative = loss."""
    return shares * (current_price - cost_per_share)


def apply_sleeve_filter(lots: pd.DataFrame, selected_sleeves: list[str]) -> pd.DataFrame:
    """Return lots filtered to selected_sleeves. Empty list means show all."""
    if not selected_sleeves:
        return lots
    return lots[lots["sleeve"].isin(selected_sleeves)]


def compute_harvest_pool(
    lots: pd.DataFrame,
    threshold: float = HARVEST_MATERIALITY_THRESHOLD,
) -> tuple[float, int]:
    """Return (pool_total, material_lot_count) where pool_total is the sum of
    per-lot losses whose magnitude exceeds threshold. Both values are ≥ 0 / ≥ 0."""
    if lots.empty:
        return 0.0, 0
    losses = lots[lots["unrealized_gl"] < -threshold]["unrealized_gl"]
    return float(losses.sum()), int(len(losses))


def lot_count_label(n: int) -> str:
    """Return 'N lot' or 'N lots' with correct plurality."""
    return f"{n} lot" if n == 1 else f"{n} lots"


def compute_unrealized_gl_pct(gl: float, cost_basis_total: float) -> float:
    """Unrealized G/L as a fraction of cost basis (e.g. 0.12 = 12%). Zero if cost is zero."""
    if cost_basis_total == 0:
        return 0.0
    return gl / cost_basis_total


# A share remainder this small is float noise, not shares: a lot relieved down to it is
# CLOSED (#364). Relief used to compare float share counts exactly, so selling exactly
# what an account held left lots of ~1e-16 shares open (0.3 - 0.1 is
# 0.19999999999999998, which is less than a 0.2-share lot) and Tax Lots rendered
# 0.000000-share rows instead of its empty state.
#
# Anchored to the data, measured 2026-09-24:
#   * the owner's ledger (Fidelity CSV) records at most 3 decimals; its smallest lot is
#     0.019 shares;
#   * the demo book's DRIP lots are computed quantities carried at full float precision,
#     and its smallest lot is 0.000932 shares, BELOW Fidelity's 0.001 grain. So the
#     floor to stay under is the smallest real lot in any book, not 0.001.
#   * float noise: holdings are at most ~10^3 shares, so one subtraction errs by up to
#     ~1e-13 and a hundred lots by up to ~1e-11; the slivers seen were 1e-17 to 2e-16.
# 1e-9 sits about six orders of magnitude below 0.000932 and at least two above
# accumulated noise.
# Not Decimal: quantities are stored as binary REAL, DRIP quantities sit on no decimal
# grid, and a sell can itself be a float sum, so exact decimal arithmetic on the stored
# values would still leave a remainder that needs a tolerance.
_SHARE_TOLERANCE = 1e-9


def _fifo_open_lots(buys: pd.DataFrame, total_sell_shares: float) -> pd.DataFrame:
    """
    Apply FIFO lot matching. Returns open lot rows with shares adjusted for partial closes.

    Oldest lots (by trade_date, then trade_id) are closed first. A lot may be
    partially closed if a sell exhausts part of its shares.

    Args:
        buys: rows from the trades table for BUY transactions on one ticker.
              Required columns: trade_id, trade_date, shares, price, lot_source.
        total_sell_shares: total shares sold for this ticker (all-time).

    Returns:
        DataFrame of remaining open lots. Empty DataFrame if all lots are closed.

    Share counts are never compared exactly: a remainder within _SHARE_TOLERANCE is
    zero, so a sale of exactly what is held closes every lot (#364).
    """
    if total_sell_shares <= _SHARE_TOLERANCE:
        return buys.copy().reset_index(drop=True)

    remaining = total_sell_shares
    open_rows = []

    for _, lot in buys.sort_values(["trade_date", "trade_id"]).iterrows():
        if remaining <= _SHARE_TOLERANCE:
            open_rows.append(lot.to_dict())
        elif remaining >= lot["shares"] - _SHARE_TOLERANCE:
            # Closed, including when the sale falls short of the lot by float noise.
            remaining -= lot["shares"]
        else:
            row = lot.to_dict()
            row["shares"] = lot["shares"] - remaining
            remaining = 0
            open_rows.append(row)

    if not open_rows:
        return pd.DataFrame(columns=buys.columns)
    return pd.DataFrame(open_rows).reset_index(drop=True)


def _open_lots(trades: pd.DataFrame) -> pd.DataFrame:
    """FIFO-relieve each (account, ticker)'s buys by that account's sales of that
    ticker; the lots left open.

    Relief is per account, ALWAYS (#362): a lot belongs to one account, so a sale in
    one account must never close another account's lot. Grouped by ticker alone, a
    sale in a second account relieved the first account's older lots.

    The ONE relief path. get_lot_inventory (the Tax Lots page) and
    open_lot_cost_basis (the Performance page's cost basis) both call it, so the two
    cannot disagree about which lots a sale closed (#357).

    ``trades``: rows with account_id, trade_id, ticker, trade_date, action, shares,
    price and lot_source. Returns the open lots with ``account_id`` and ``ticker``
    columns, empty if none.
    """
    if trades.empty:
        return pd.DataFrame()
    if "account_id" not in trades.columns:
        raise ValueError("_open_lots needs account_id: relief is per account (#362)")
    buys = trades[trades["action"].str.lower() == "buy"]
    sells = trades[trades["action"].str.lower() == "sell"]
    open_lots: list[pd.DataFrame] = []
    for (account_id, ticker), acct_buys in buys.groupby(["account_id", "ticker"]):
        sold = float(sells.loc[(sells["account_id"] == account_id)
                               & (sells["ticker"] == ticker), "shares"].sum())
        still_open = _fifo_open_lots(
            acct_buys[["trade_id", "trade_date", "shares", "price", "lot_source"]].copy(),
            sold,
        )
        if not still_open.empty:
            still_open["account_id"] = account_id
            still_open["ticker"] = ticker
            open_lots.append(still_open)
    return pd.concat(open_lots, ignore_index=True) if open_lots else pd.DataFrame()


def taxable_accounts() -> "list[dict]":
    """The Tax Lots page's scope (#362): every active TAXABLE account, with how many
    trades the ledger holds for it. A lot in an IRA, a workplace plan or an HSA carries
    no capital-gains consequence, so tax-advantaged accounts are not in scope. A
    taxable account with no ledger trades (the CSV-fed TOD book) IS in scope and is
    disclosed rather than silently absent: see unledgered_taxable_notice.

    Returns [{account_id, name, trades}], ordered by account_id.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT a.account_id,
                      COALESCE(a.display_name, a.name) AS name,
                      (SELECT COUNT(*) FROM trades t WHERE t.account_id = a.account_id) AS trades
               FROM accounts a
               WHERE a.tax_treatment = 'taxable' AND a.is_active = 1
               ORDER BY a.account_id"""
        ).fetchall()
    return [dict(r) for r in rows]


def unledgered_taxable_notice(names: "list[str]") -> "Optional[str]":
    """The disclosure for taxable accounts whose lots the page cannot show (#362).

    Their positions reach the app from the custodian's positions export, which carries
    no lots, so nothing about them is in the trade ledger. Omitted silently, such an
    account reads as "no lots, no harvestable losses": absence presenting as an
    all-clear. This says which accounts, why, and what the page leaves out.
    """
    if not names:
        return None
    if len(names) == 1:
        who, verb, its, holds = f"**{names[0]}**", "is a taxable account", "its", "it holds"
    else:
        who = "**" + "**, **".join(names[:-1]) + "** and **" + names[-1] + "**"
        verb, its, holds = "are taxable accounts", "their", "they hold"
    return (
        f"{who} {verb} whose lots are not shown. {its.capitalize()} per-lot history "
        f"(purchase dates and prices) is not in the trade ledger: {its} positions come "
        f"from the custodian's positions export, which carries no lots. So {its} holdings "
        f"are left out of the lot table, the short/long-term gain split, the sleeve "
        f"summary and the harvest candidates below. That absence does not mean {holds} "
        f"no lots or no harvestable losses."
    )


# ── DB-backed functions ───────────────────────────────────────────────────────

def get_lot_inventory(as_of: Optional[str] = None, *, account_ids: "list[int]") -> pd.DataFrame:
    """
    Return per-lot detail for all open positions as of as_of (ISO date string).

    Prices use adj_close (total-return basis), consistent with TWR and drift
    calculations throughout the system. SPAXX is valued at $1.00/share.

    Returns DataFrame with columns:
        trade_id, ticker, sleeve, trade_date, days_held, shares,
        cost_basis_per_share, cost_basis_total, current_price, market_value,
        unrealized_gl, unrealized_gl_pct, tax_status, days_to_lt, lot_source,
        account_id
    Returns empty DataFrame if no trades exist.

    ``account_ids`` is required (#362, the #139 contract): the Tax Lots page passes
    every taxable account (taxable_accounts). Reading every account mixed IRA lots in
    with taxable ones and let one account's sale relieve another's lots.
    """
    if account_ids is None:
        raise ValueError(
            "account_ids is required: the lot inventory is account-scoped and will not "
            "silently read every account. The Tax Lots page passes taxable_accounts().")
    account_ids = [int(a) for a in account_ids]
    if not account_ids:
        return pd.DataFrame()
    as_of_str = as_of or date.today().isoformat()
    as_of_date = date.fromisoformat(as_of_str)
    marks = ",".join("?" * len(account_ids))

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                t.account_id,
                t.trade_id,
                t.ticker,
                t.trade_date,
                t.action,
                t.shares,
                t.price,
                COALESCE(t.lot_source, 'initial') AS lot_source,
                ac.name AS sleeve
            FROM trades t
            JOIN securities s   ON t.ticker = s.ticker
            JOIN asset_classes ac ON s.asset_class_id = ac.asset_class_id
            WHERE t.trade_date <= ? AND t.account_id IN ({marks})
            ORDER BY t.ticker, t.trade_date, t.trade_id
            """.format(marks=marks),
            (as_of_str, *account_ids),
        ).fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame([dict(r) for r in rows])
    buys_all = df[df["action"].str.lower() == "buy"]

    # Sleeve lookup (first buy for each ticker carries the correct sleeve)
    sleeve_map = buys_all.groupby("ticker")["sleeve"].first().to_dict()

    lots = _open_lots(df)
    if lots.empty:
        return pd.DataFrame()
    lots["sleeve"] = lots["ticker"].map(sleeve_map)

    # Date / holding-period columns
    lots["purchase_date"] = pd.to_datetime(lots["trade_date"]).dt.date
    lots["days_held"] = lots["purchase_date"].apply(
        lambda d: compute_days_held(d, as_of_date)
    )
    lots["tax_status"] = lots["days_held"].apply(compute_tax_status)
    lots["days_to_lt"] = lots["days_held"].apply(compute_days_to_lt)

    # Cost basis
    lots["cost_basis_per_share"] = lots["price"]
    lots["cost_basis_total"] = lots["shares"] * lots["price"]

    # Current prices: look back 7 calendar days from the newest STORED price, not
    # from the calendar (#302), so a stale cache offline still prices the lots.
    from src.holdings import look_back_start
    current_prices: dict[str, float] = {}
    for ticker in lots["ticker"].unique():
        if ticker == "SPAXX":
            current_prices[ticker] = 1.0
        else:
            try:
                p = get_prices(ticker, look_back_start(ticker, as_of_str), as_of_str)
                if not p.empty:
                    series = total_return_series(p).dropna()
                    current_prices[ticker] = float(series.iloc[-1]) if not series.empty else 0.0
                else:
                    current_prices[ticker] = 0.0
            except Exception:
                current_prices[ticker] = 0.0

    lots["current_price"] = lots["ticker"].map(current_prices).fillna(0.0)
    lots["market_value"] = lots["shares"] * lots["current_price"]
    lots["unrealized_gl"] = lots.apply(
        lambda r: compute_unrealized_gl(
            r["shares"], r["cost_basis_per_share"], r["current_price"]
        ),
        axis=1,
    )
    lots["unrealized_gl_pct"] = lots.apply(
        lambda r: compute_unrealized_gl_pct(r["unrealized_gl"], r["cost_basis_total"]),
        axis=1,
    )

    return lots[
        [
            "trade_id", "ticker", "sleeve", "trade_date", "days_held",
            "shares", "cost_basis_per_share", "cost_basis_total",
            "current_price", "market_value", "unrealized_gl",
            "unrealized_gl_pct", "tax_status", "days_to_lt", "lot_source",
            "account_id",
        ]
    ].reset_index(drop=True)


def open_lot_cost_basis(*, account_id: int, as_of: Optional[str] = None) -> float:
    """What the lots still held in ONE account cost: each open lot's shares x price,
    DRIP lots included, sales relieving the oldest lots first. The Tax Lots page's
    method (_open_lots), not a second one.

    Account-scoped, and the account is required (#139's contract: a None account
    raises rather than reading every account). The Performance page's cost basis
    summed every buy in every account and never subtracted a sale (#357). No
    securities join: a cost basis needs no sleeve, and the caption's own query never
    dropped a ticker for lacking one.
    """
    from src.holdings import _require_account_id
    account_id = _require_account_id(account_id)
    as_of_str = as_of or date.today().isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT account_id, trade_id, ticker, trade_date, action, shares, price,
                      COALESCE(lot_source, 'initial') AS lot_source
               FROM trades
               WHERE account_id = ? AND trade_date <= ?
               ORDER BY ticker, trade_date, trade_id""",
            (account_id, as_of_str),
        ).fetchall()
    lots = _open_lots(pd.DataFrame([dict(r) for r in rows]))
    return float((lots["shares"] * lots["price"]).sum()) if not lots.empty else 0.0


def get_sleeve_rollup(lots: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate open lot data to sleeve level.

    Returns DataFrame with columns:
        Sleeve, Lot Count, Cost Basis, Market Value, Unrealized G/L,
        Unrealized G/L %, ST Gain, LT Gain, Unrealized Loss
    """
    if lots.empty:
        return pd.DataFrame()

    def _agg(grp: pd.DataFrame) -> pd.Series:
        cb = grp["cost_basis_total"].sum()
        mv = grp["market_value"].sum()
        gl = mv - cb
        return pd.Series(
            {
                "Lot Count":        len(grp),
                "Cost Basis":       cb,
                "Market Value":     mv,
                "Unrealized G/L":   gl,
                "Unrealized G/L %": compute_unrealized_gl_pct(gl, cb),
                "ST Gain":          grp.loc[
                    (grp["tax_status"] == "ST") & (grp["unrealized_gl"] > 0),
                    "unrealized_gl",
                ].sum(),
                "LT Gain":          grp.loc[
                    (grp["tax_status"] == "LT") & (grp["unrealized_gl"] > 0),
                    "unrealized_gl",
                ].sum(),
                "Unrealized Loss":  grp.loc[
                    grp["unrealized_gl"] < 0, "unrealized_gl"
                ].sum(),
            }
        )

    result = (
        lots.groupby("sleeve", sort=False)
        .apply(_agg, include_groups=False)
        .reset_index()
        .rename(columns={"sleeve": "Sleeve"})
        .sort_values("Market Value", ascending=False)
        .reset_index(drop=True)
    )
    return result


def summary_metrics(lots: pd.DataFrame) -> dict:
    """
    Portfolio-level summary across all open lots.

    Returns dict with keys:
        cost_basis_total, market_value_total, unrealized_gl_total,
        unrealized_gl_pct, unrealized_st_gain, unrealized_lt_gain,
        unrealized_loss
    """
    _zero: dict = {
        "cost_basis_total":   0.0,
        "market_value_total": 0.0,
        "unrealized_gl_total": 0.0,
        "unrealized_gl_pct":  0.0,
        "unrealized_st_gain": 0.0,
        "unrealized_lt_gain": 0.0,
        "unrealized_loss":    0.0,
    }
    if lots.empty:
        return _zero

    cb = lots["cost_basis_total"].sum()
    mv = lots["market_value"].sum()
    gl = mv - cb

    return {
        "cost_basis_total":   cb,
        "market_value_total": mv,
        "unrealized_gl_total": gl,
        "unrealized_gl_pct":  compute_unrealized_gl_pct(gl, cb),
        "unrealized_st_gain": lots.loc[
            (lots["tax_status"] == "ST") & (lots["unrealized_gl"] > 0), "unrealized_gl"
        ].sum(),
        "unrealized_lt_gain": lots.loc[
            (lots["tax_status"] == "LT") & (lots["unrealized_gl"] > 0), "unrealized_gl"
        ].sum(),
        "unrealized_loss": lots.loc[lots["unrealized_gl"] < 0, "unrealized_gl"].sum(),
    }


def discretionary_trade_count() -> int:
    """Count of non-DRIP trades (lot_source != 'drip') in the trades table."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE COALESCE(lot_source, 'initial') != 'drip'"
        ).fetchone()
    return int(row[0]) if row else 0


def drip_lot_count() -> int:
    """Count of DRIP lots (lot_source = 'drip') in the trades table."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE lot_source = 'drip'"
        ).fetchone()
    return int(row[0]) if row else 0
