"""An independent reference for the Performance page's portfolio returns (#349).

WHY THIS EXISTS. test_identity_bf_sum_reconciles_to_stage2 checked the page against a
copy of the page's own calculation, and passed while Stage 2 was off by 10,000 bps:
the copy made the same mistake. A reference built from the page's functions inherits
their mistakes, so nothing here calls src.holdings or src.returns. Positions and cash
flows are read straight from the trades table, the book is valued only where it must
be (a window's two ends and each flow date), and the time-weighted return chains the
sub-periods BETWEEN FLOWS rather than day by day.

SHARED WITH THE PAGE, deliberately, because they are inputs and conventions rather than
the calculation under test:
  * prices, from the price layer (get_prices' total-return close, #339);
  * the valuation conventions _portfolio_value_series_impl documents: non-DRIP shares
    at the total-return close on or before the day; SPAXX at BIL's total-return close,
    scaled to $1 on BIL's first close on or after inception;
  * the ledger's flow convention: a buy brings shares x price + fees into the book, a
    sell takes shares x price - fees out, DRIP lots are neither.

Must run with src.db pointed at the book under test (the frozen-book fixtures do it).
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pandas as pd

WINDOWS = ("1M", "3M", "YTD", "1Y", "SI")


def plant_deposit(db_path, day: str, ticker: str, dollars: float, account_id: int = 1) -> float:
    """Insert one external deposit: a buy of ``dollars`` of ``ticker`` at that day's
    close, with no matching sell. That is the ledger shape of a real deployment (the
    owner's book records them as 'Fidelity CSV' buys). Returns the shares bought."""
    con = sqlite3.connect(db_path)
    try:
        with con:
            close = con.execute(
                "SELECT close FROM prices WHERE ticker = ? AND price_date = ?",
                (ticker, day)).fetchone()[0]
            shares = dollars / close
            n = con.execute(
                "INSERT INTO trades (account_id, ticker, trade_date, action, shares, price, "
                "fees, notes, lot_source) VALUES (?, ?, ?, 'Buy', ?, ?, 0, ?, 'Fidelity CSV')",
                (account_id, ticker, day, shares, close, "#349 test deposit")).rowcount
    finally:
        con.close()
    assert n == 1
    return shares


class Book:
    """The portfolio account's book, read from the ledger, valued on demand."""

    def __init__(self, account_id: int = 1):
        from src.db import get_connection
        from src.prices import get_prices

        with get_connection() as conn:
            self._trades = [tuple(r) for r in conn.execute(
                "SELECT trade_date, ticker, LOWER(action), shares, price, COALESCE(fees, 0) "
                "FROM trades WHERE account_id = ? AND (lot_source IS NULL OR lot_source != 'drip') "
                "ORDER BY trade_date, trade_id", (account_id,)).fetchall()]
        self.inception = min(t[0] for t in self._trades)
        held = sorted({t[1] for t in self._trades})
        price_tickers = sorted({"BIL" if t == "SPAXX" else t for t in held})
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        with get_connection() as conn:
            q = ",".join("?" * len(price_tickers))
            # The window anchor: the newest real price across the holdings, never today.
            self.anchor = conn.execute(
                f"SELECT MAX(price_date) FROM prices WHERE ticker IN ({q}) "
                "AND price_date >= ? AND price_date <= ?",
                (*price_tickers, self.inception, yesterday)).fetchone()[0]
        self._px = {}
        for t in price_tickers:
            p = get_prices(t, self.inception, self.anchor)
            s = p["adj_close"].fillna(p["close"]).astype(float)
            s.index = [d.isoformat() for d in s.index]
            self._px[t] = s.sort_index()
        bil = self._px.get("BIL")
        self._bil0 = float(bil.iloc[0]) if bil is not None and len(bil) else None

    # ── ledger reads ──────────────────────────────────────────────────────────
    def positions(self, day: str) -> dict:
        pos: dict = {}
        for d, t, action, shares, _price, _fees in self._trades:
            if d <= day:
                pos[t] = pos.get(t, 0.0) + (shares if action == "buy" else -shares)
        return {t: max(s, 0.0) for t, s in pos.items()}

    def flows(self, after: str, through: str) -> dict:
        """External flows on each date in (after, through]: a flow ON the window's
        first day is part of its starting value, not a mid-window flow."""
        out: dict = {}
        for d, _t, action, shares, price, fees in self._trades:
            if after < d <= through:
                amt = shares * price + fees if action == "buy" else -(shares * price) + fees
                out[d] = out.get(d, 0.0) + amt
        return {d: a for d, a in out.items() if a != 0.0}

    # ── valuation ─────────────────────────────────────────────────────────────
    def _price(self, ticker: str, day: str) -> float:
        if ticker == "SPAXX":
            s = self._px["BIL"]
            return float(s[s.index <= day].iloc[-1]) / self._bil0
        s = self._px[ticker]
        s = s[s.index <= day]
        return float(s.iloc[-1]) if len(s) else 0.0

    def value(self, day: str) -> float:
        return sum(sh * self._price(t, day) for t, sh in self.positions(day).items() if sh)

    # ── windows and returns ───────────────────────────────────────────────────
    def window(self, label: str) -> "tuple[str, str]":
        end = date.fromisoformat(self.anchor)
        start = {
            "SI": date.fromisoformat(self.inception),
            "1Y": end - timedelta(days=365),
            "YTD": date(end.year, 1, 1),
            "3M": end - timedelta(days=90),
            "1M": end - timedelta(days=30),
        }[label]
        return max(start.isoformat(), self.inception), end.isoformat()

    def twr(self, start: str, end: str) -> float:
        """Chain the sub-periods between external flows. A flow is at the END of its
        day, so the sub-period ending that day earns (V(d) - flow) on V(prev)."""
        growth, v_prev = 1.0, self.value(start)
        for d, amt in sorted(self.flows(start, end).items()):
            v_d = self.value(d)
            growth *= (v_d - amt) / v_prev
            v_prev = v_d
        return growth * self.value(end) / v_prev - 1.0

    def value_ratio(self, start: str, end: str) -> float:
        """What the page used to report: end value over start value, flows and all."""
        return self.value(end) / self.value(start) - 1.0


# ── the blended SAA benchmark, rebuilt (#383) ─────────────────────────────────────
# The rule, not the product's code: $1 at the SAA target weights, bought at each
# calendar quarter's first base (the prior quarter's last close), held through the
# quarter, the quarters chained. Targets and benchmark specs straight from
# asset_classes; prices from the price layer (a shared input, as above).

def _quarter_ends_between(first: date, last: date) -> "list[date]":
    out, y = [], first.year - 1
    while True:
        for m, d in ((3, 31), (6, 30), (9, 30), (12, 31)):
            q = date(y, m, d)
            if q > last:
                return out
            if q >= first:
                out.append(q)
        y += 1


def _blend_weights() -> dict:
    import re
    from src.db import get_connection
    with get_connection() as conn:
        rows = conn.execute("SELECT target_weight, benchmark_ticker FROM asset_classes "
                            "WHERE parent_id IS NOT NULL").fetchall()
    weights: dict = {}
    for w, spec in rows:
        if not w:
            continue
        legs = re.findall(r"([A-Z]+)\s*\((\d+(?:\.\d+)?)%\)", spec or "")
        for ticker, pct in (legs or [(spec.strip(), "100")]):
            weights[ticker] = weights.get(ticker, 0.0) + w * float(pct) / 100
    return weights


def quarterly_blend_return(start: str, end: str) -> float:
    """The blended SAA benchmark's return over [start, end] under the quarterly rule."""
    from src.prices import get_prices
    s_d, e_d = date.fromisoformat(start), date.fromisoformat(end)
    anchor = max(q for q in _quarter_ends_between(s_d - timedelta(days=100), s_d))
    bases = [anchor] + [q for q in _quarter_ends_between(anchor + timedelta(days=1), e_d)
                        if q < e_d]
    weights = _blend_weights()
    px = {}
    for t in weights:
        p = get_prices(t, anchor.isoformat(), end)
        s = p["adj_close"].fillna(p["close"]).astype(float)
        s.index = [d.isoformat() for d in s.index]
        px[t] = s.sort_index()

    def close(t, day):
        s = px[t][px[t].index <= day.isoformat()]
        return float(s.iloc[-1])

    def level(day):
        """The chained index on ``day``: each quarter's basket grows from its base."""
        lvl = 1.0
        for i, base in enumerate(bases):
            nxt = bases[i + 1] if i + 1 < len(bases) else None
            stop = day if nxt is None or day <= nxt else nxt
            lvl *= sum(w * close(t, stop) / close(t, base) for t, w in weights.items())
            if nxt is None or day <= nxt:
                return lvl
        return lvl

    return level(e_d) / level(s_d) - 1.0
