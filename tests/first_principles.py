"""An independent reference for the Performance page's portfolio returns (#349).

WHY THIS EXISTS. test_identity_bf_sum_reconciles_to_stage2 checked the page against a
copy of the page's own calculation, and passed while Stage 2 was off by 10,000 bps:
the copy made the same mistake. A reference built from the page's functions inherits
their mistakes, so nothing here calls src.holdings or src.returns. Positions and cash
flows are read straight from the trades table, the book is valued only where it must
be (a window's two ends and each flow date), and the time-weighted return chains the
sub-periods BETWEEN FLOWS rather than day by day.

TWO VALUATION RULES (#369), as the page has. A Book takes the rule in force where it is
built (src.cache.valuation_in_force: the raw-close rule for a live read, a lock's own marker
inside it), or the one it is given.

THE RAW-CLOSE RULE is built from the trades table, the stored closes and the stored
dividends alone, read straight from the tables: it calls neither the price layer nor
src.holdings. SHARED WITH THE PAGE, deliberately, are the conventions the owner decided on
October 27 and 28, 2026:
  * every share held, DRIP lots included, at its close on or before the day;
  * SPAXX at BIL's total return, scaled to $1 on BIL's first close on or after inception,
    BIL's total return being its close with each dividend reinvested at the close before
    its ex-date (the provider's method); each SPAXX trade enters at its dollars, as units
    bought at that day's level;
  * each dividend is income on its ex-date, the shares held at the previous day's end x
    the amount. A fund the ledger holds DRIP lots for holds that income at its amount
    until a DRIP lot is paid from it (the lot's shares x price + fees); any other
    dividend buys SPAXX units on its ex-date;
  * flows are the ledger's dollars: a buy brings shares x price + fees into the book, a
    sell takes shares x price - fees out; DRIP lots are neither;
  * by sleeve (#369's PR 3, decided by the owner on October 28 and 29, 2026), each ticker
    in its asset class, read from the securities table: a dividend is income in its fund's
    sleeve on its ex-date; one a DRIP lot reinvests stays there until the lot, and any other
    moves to Cash / SPAXX at the end of its ex-date. A sleeve's return over a window with no
    external flow credits the fund's sleeve with what it moved to Cash / SPAXX, and Cash /
    SPAXX's return does not count it.

ADJUSTED LEVELS, every lock taken before the marker. SHARED WITH THE PAGE, deliberately,
because they are inputs and conventions rather than the calculation under test:
  * prices, from the price layer (get_prices' total-return close, #339);
  * the valuation conventions _portfolio_value_series_impl documents: non-DRIP shares
    at the total-return close on or before the day; SPAXX at BIL's total-return close,
    scaled to $1 on BIL's first close on or after inception;
  * the ledger's flow convention, in the book's total-return units (#687): a buy brings
    shares x price x f + fees into the book, a sell takes shares x price x f - fees out,
    f being the ticker's level over its close on the trade date, from the same prices
    (SPAXX's close is its $1 NAV, so its f is its level); DRIP lots are neither.

Must run with src.db pointed at the book under test (the frozen-book fixtures do it).
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pandas as pd

WINDOWS = ("1M", "3M", "YTD", "1Y", "SI")
CASH_SLEEVE = "Cash / SPAXX"


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

    def __init__(self, account_id: int = 1, rule: "str | None" = None):
        from src.cache import RAW_CLOSE_RULE, valuation_in_force
        self.rule = rule or valuation_in_force()[0]
        self.raw = self.rule == RAW_CLOSE_RULE
        if self.raw:
            self._read_raw(account_id)
        else:
            self._read_adjusted(account_id)

    def _read_raw(self, account_id: int) -> None:
        """The ledger, the stored closes and the stored dividends, from the tables."""
        from src.db import get_connection

        with get_connection() as conn:
            rows = [tuple(r) for r in conn.execute(
                "SELECT trade_date, ticker, LOWER(action), shares, price, COALESCE(fees, 0), "
                "COALESCE(lot_source, '') FROM trades WHERE account_id = ? "
                "ORDER BY trade_date, trade_id", (account_id,)).fetchall()]
        self._all = [r[:6] for r in rows]
        self._trades = [r[:6] for r in rows if r[6] != "drip"]
        self._lots = [r[:6] for r in rows if r[6] == "drip"]
        self._reinvested = {r[1] for r in self._lots}
        self.inception = min(r[0] for r in rows)
        held = sorted({r[1] for r in rows})
        funds = [t for t in held if t != "SPAXX"]
        price_tickers = sorted({"BIL" if t == "SPAXX" else t for t in held})
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        with get_connection() as conn:
            q = ",".join("?" * len(price_tickers))
            # The window anchor: the newest real price across the holdings, never today.
            self.anchor = conn.execute(
                f"SELECT MAX(price_date) FROM prices WHERE ticker IN ({q}) "
                "AND price_date >= ? AND price_date <= ?",
                (*price_tickers, self.inception, yesterday)).fetchone()[0]
            self._closes = {t: [tuple(r) for r in conn.execute(
                "SELECT price_date, close FROM prices WHERE ticker = ? AND price_date <= ? "
                "ORDER BY price_date", (t, self.anchor)).fetchall()]
                for t in sorted(set(funds) | {"BIL"})}
            self._divs = {t: [tuple(r) for r in conn.execute(
                "SELECT ex_date, amount FROM dividends WHERE ticker = ? AND ex_date <= ? "
                "ORDER BY ex_date", (t, self.anchor)).fetchall()] for t in funds}
            self._bil_divs = [tuple(r) for r in conn.execute(
                "SELECT ex_date, amount FROM dividends WHERE ticker = 'BIL' AND ex_date <= ? "
                "ORDER BY ex_date", (self.anchor,)).fetchall()]
        self._bil0 = next(d for d, _c in self._closes["BIL"] if d >= self.inception)
        with get_connection() as conn:
            self._sleeve = {t: s for t, s in conn.execute(
                "SELECT s.ticker, ac.name FROM securities s JOIN asset_classes ac "
                "ON s.asset_class_id = ac.asset_class_id").fetchall()}

    def _close_on(self, ticker: str, day: str) -> "tuple[str, float] | None":
        """The ticker's last stored close on or before ``day``, with its date."""
        found = [(d, c) for d, c in self._closes[ticker] if d <= day]
        return found[-1] if found else None

    def _bil_level(self, day: str) -> float:
        """SPAXX's level: BIL's total return from its first close on or after inception,
        each dividend reinvested at the last close before its ex-date."""
        d, close = self._close_on("BIL", day)
        level = close / dict(self._closes["BIL"])[self._bil0]
        for ex, amount in self._bil_divs:
            if self._bil0 < ex <= d:
                prev = self._close_on("BIL", (date.fromisoformat(ex) - timedelta(days=1)).isoformat())
                level /= 1.0 - amount / prev[1]
        return level

    def _income(self) -> "list[tuple[str, str, float]]":
        """(ex-date, ticker, income) for every dividend on a fund held the day before."""
        if getattr(self, "_income_rows", None) is not None:
            return self._income_rows
        out = []
        for t, divs in self._divs.items():
            for ex, amount in divs:
                before = (date.fromisoformat(ex) - timedelta(days=1)).isoformat()
                shares = self._positions(self._all, before).get(t, 0.0)
                if shares > 0:
                    out.append((ex, t, shares * amount))
        self._income_rows = out
        return out

    def _read_adjusted(self, account_id: int) -> None:
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
        self._px, self._close = {}, {}
        for t in price_tickers:
            p = get_prices(t, self.inception, self.anchor)
            s = p["adj_close"].fillna(p["close"]).astype(float)
            s.index = [d.isoformat() for d in s.index]
            self._px[t] = s.sort_index()
            c = p["close"].astype(float)
            c.index = [d.isoformat() for d in c.index]
            self._close[t] = c.sort_index()
        bil = self._px.get("BIL")
        self._bil0 = float(bil.iloc[0]) if bil is not None and len(bil) else None

    # ── ledger reads ──────────────────────────────────────────────────────────
    @staticmethod
    def _positions(trades, day: str) -> dict:
        pos: dict = {}
        for d, t, action, shares, _price, _fees in trades:
            if d <= day:
                pos[t] = pos.get(t, 0.0) + (shares if action == "buy" else -shares)
        return {t: max(s, 0.0) for t, s in pos.items()}

    def positions(self, day: str) -> dict:
        """Shares held at the end of ``day``: under the raw-close rule every share, DRIP
        lots included; under adjusted levels the non-DRIP shares."""
        return self._positions(self._all if self.raw else self._trades, day)

    def flows(self, after: str, through: str) -> dict:
        """External flows on each date in (after, through]: a flow ON the window's
        first day is part of its starting value, not a mid-window flow."""
        out: dict = {}
        for d, t, action, shares, price, fees in self._trades:
            if after < d <= through:
                dollars = shares * price * (1.0 if self.raw else self._factor(t, d))
                amt = dollars + fees if action == "buy" else -dollars + fees
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

    def _factor(self, ticker: str, day: str) -> float:
        """The ticker's level over its close on or before ``day``: what a dollar paid for
        it that day counts for in the book's total-return units (#687)."""
        if ticker == "SPAXX":
            return self._price(ticker, day)
        c = self._close[ticker]
        return self._price(ticker, day) / float(c[c.index <= day].iloc[-1])

    def value(self, day: str) -> float:
        if self.raw:
            return self._raw_value(day)
        return sum(sh * self._price(t, day) for t, sh in self.positions(day).items() if sh)

    def _raw_value(self, day: str) -> float:
        """Every share at its close, SPAXX's units at BIL's level, and the income line."""
        total, units, pending = 0.0, 0.0, 0.0
        for t, sh in self.positions(day).items():
            if sh and t != "SPAXX":
                found = self._close_on(t, day)
                total += sh * found[1] if found else 0.0
        for d, t, action, shares, price, _fees in self._all:
            if t == "SPAXX" and d <= day:
                units += (1 if action == "buy" else -1) * shares * price / self._bil_level(d)
        units = max(units, 0.0)
        for ex, t, income in self._income():
            if ex <= day:
                if t in self._reinvested:
                    pending += income
                else:
                    units += income / self._bil_level(ex)
        for d, _t, _action, shares, price, fees in self._lots:
            if d <= day:
                pending -= shares * price + fees
        return total + units * self._bil_level(day) + pending

    def current_value_parts(self, day: str) -> dict:
        """The raw-close value's parts on ``day``: the funds at their closes, SPAXX, and
        the income line (its SPAXX part at BIL's level)."""
        assert self.raw, "the parts are the raw-close rule's"
        whole = self._raw_value(day)
        funds = sum(sh * self._close_on(t, day)[1] for t, sh in self.positions(day).items()
                    if sh and t != "SPAXX" and self._close_on(t, day))
        trade_units = max(sum((1 if a == "buy" else -1) * n * px / self._bil_level(d)
                              for d, t, a, n, px, _f in self._all
                              if t == "SPAXX" and d <= day), 0.0)
        spaxx = trade_units * self._bil_level(day)
        return {"funds": funds, "spaxx": spaxx, "income": whole - funds - spaxx}

    def sleeve_values(self, day: str) -> dict:
        """The raw-close value on ``day`` by sleeve: each fund's shares at its close and its
        income a DRIP lot has yet to be paid from, in the fund's sleeve; the SPAXX units its
        trades bought in SPAXX's; and the units bought with income taken in cash in Cash /
        SPAXX, whatever sleeve the securities table gives SPAXX. Units at BIL's level."""
        assert self.raw, "the sleeves are the raw-close rule's"
        out: dict = {}

        def add(ticker: str, dollars: float) -> None:
            sleeve = self._sleeve.get(ticker, "Unknown")
            out[sleeve] = out.get(sleeve, 0.0) + dollars

        for t, sh in self.positions(day).items():
            if sh and t != "SPAXX":
                found = self._close_on(t, day)
                add(t, sh * found[1] if found else 0.0)
        units = max(sum((1 if a == "buy" else -1) * n * px / self._bil_level(d)
                        for d, t, a, n, px, _f in self._all if t == "SPAXX" and d <= day), 0.0)
        add("SPAXX", units * self._bil_level(day))
        income_units = 0.0
        for ex, t, income in self._income():
            if ex <= day:
                if t in self._reinvested:
                    add(t, income)
                else:
                    income_units += income / self._bil_level(ex)
        for d, t, _action, shares, price, fees in self._lots:
            if d <= day:
                add(t, -(shares * price + fees))
        out[CASH_SLEEVE] = out.get(CASH_SLEEVE, 0.0) + income_units * self._bil_level(day)
        return out

    def swept(self, after: str, through: str) -> dict:
        """By fund, the income taken in cash that moved to SPAXX in (after, through]."""
        out: dict = {}
        for ex, t, income in self._income():
            if t not in self._reinvested and after < ex <= through:
                out[t] = out.get(t, 0.0) + income
        return out

    def sleeve_returns(self, start: str, end: str) -> dict:
        """Each sleeve's return over a window with no external flow, each move to SPAXX
        credited to the fund's sleeve and not counted in SPAXX's."""
        v0, v1 = self.sleeve_values(start), self.sleeve_values(end)
        for t, dollars in self.swept(start, end).items():
            sleeve = self._sleeve.get(t, "Unknown")
            v1[sleeve] = v1.get(sleeve, 0.0) + dollars
            v1[CASH_SLEEVE] = v1.get(CASH_SLEEVE, 0.0) - dollars
        return {s: v1.get(s, 0.0) / v - 1.0 for s, v in v0.items() if v > 0}

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
