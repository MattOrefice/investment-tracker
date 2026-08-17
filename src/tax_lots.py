"""Tax lot inventory derived from the trades ledger.

The trades table is the lot ledger. Each BUY row is one lot. SELLs are matched
against open lots on a FIFO basis. DRIP reinvestments are persisted as separate
lot rows with lot_source='drip'; discretionary trades carry lot_source='initial'.
"""
from datetime import date, timedelta
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
    """
    if total_sell_shares <= 0:
        return buys.copy().reset_index(drop=True)

    remaining = total_sell_shares
    open_rows = []

    for _, lot in buys.sort_values(["trade_date", "trade_id"]).iterrows():
        if remaining <= 0:
            open_rows.append(lot.to_dict())
        elif remaining >= lot["shares"]:
            remaining -= lot["shares"]
        else:
            row = lot.to_dict()
            row["shares"] = lot["shares"] - remaining
            remaining = 0
            open_rows.append(row)

    if not open_rows:
        return pd.DataFrame(columns=buys.columns)
    return pd.DataFrame(open_rows).reset_index(drop=True)


# ── DB-backed functions ───────────────────────────────────────────────────────

def get_lot_inventory(as_of: Optional[str] = None) -> pd.DataFrame:
    """
    Return per-lot detail for all open positions as of as_of (ISO date string).

    Prices use adj_close (total-return basis), consistent with TWR and drift
    calculations throughout the system. SPAXX is valued at $1.00/share.

    Returns DataFrame with columns:
        trade_id, ticker, sleeve, trade_date, days_held, shares,
        cost_basis_per_share, cost_basis_total, current_price, market_value,
        unrealized_gl, unrealized_gl_pct, tax_status, days_to_lt, lot_source
    Returns empty DataFrame if no trades exist.
    """
    as_of_str = as_of or date.today().isoformat()
    as_of_date = date.fromisoformat(as_of_str)

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
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
            WHERE t.trade_date <= ?
            ORDER BY t.ticker, t.trade_date, t.trade_id
            """,
            (as_of_str,),
        ).fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame([dict(r) for r in rows])
    buys_all = df[df["action"].str.lower() == "buy"].copy()
    sells_all = df[df["action"].str.lower() == "sell"].copy()

    # Sleeve lookup (first buy for each ticker carries the correct sleeve)
    sleeve_map = buys_all.groupby("ticker")["sleeve"].first().to_dict()

    open_lots: list[pd.DataFrame] = []
    for ticker, ticker_buys in buys_all.groupby("ticker"):
        sell_shares = sells_all.loc[sells_all["ticker"] == ticker, "shares"].sum()
        open = _fifo_open_lots(
            ticker_buys[["trade_id", "trade_date", "shares", "price", "lot_source"]].copy(),
            float(sell_shares),
        )
        if not open.empty:
            open["ticker"] = ticker
            open["sleeve"] = sleeve_map[ticker]
            open_lots.append(open)

    if not open_lots:
        return pd.DataFrame()

    lots = pd.concat(open_lots, ignore_index=True)

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

    # Current prices — look back 7 calendar days for last trading day
    look_back = (as_of_date - timedelta(days=7)).isoformat()
    current_prices: dict[str, float] = {}
    for ticker in lots["ticker"].unique():
        if ticker == "SPAXX":
            current_prices[ticker] = 1.0
        else:
            try:
                p = get_prices(ticker, look_back, as_of_str)
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
        ]
    ].reset_index(drop=True)


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
