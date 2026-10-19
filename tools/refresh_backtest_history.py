"""Write the policy backtest's history (#667) — the ONLY writer of
data/backtest/monthly_returns.csv.

One row per month from data/backtest/series.json's ``first_month`` through the last
complete month, one column per series: every policy fund, every proxy, every benchmark
leg no fund or proxy supplies (AGG, for the app's 60/40), and the 3-month bill series
src/risk_free.py averages (FRED's DGS3MO). Each cell is that month's total
return as a decimal, empty before a series begins. series.json names each fund's proxy,
the last month it stands in, why, and the issuers' citations; it is hand-kept.

ONE FETCH PER SERIES. Each fund's and proxy's whole daily history comes from one request
and its monthly returns are taken from that response's adjusted closes alone, so a split
or a re-anchored adjustment can never put two scales in one series (#304 is why the
price cache does not store the provider's adjusted close; a single response is
internally consistent). A month's return runs from the close on the previous month's
last session to the close on its own; the sessions are the reference series' (SPY's).
The bill's month is (1 + the month's average daily yield) ** (1/12) - 1, the average
risk_free.rate_over takes over a window, compounded as Asset Evaluation compounds it.

REFUSES, AND WRITES NOTHING, when:
  * a fetch fails;
  * a series has a GAP: a month inside its history whose last session it holds no close
    for (or, for the bill, a month with no observation);
  * a fund's proxy does not cover every month from ``backtest_from`` through its
    ``proxy_until``, or the fund does not cover every month after it. A fund that
    replaces its proxy at inception must begin exactly the month after;
  * a benchmark leg does not cover every month from ``backtest_from``.

Nothing at runtime writes these files (tests/test_market_data_immutability.py). Run it
with the network up and FRED_API_KEY set; review the diff and commit it.

Usage:
    python tools/refresh_backtest_history.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SPEC = ROOT / "data" / "backtest" / "series.json"
OUT = ROOT / "data" / "backtest" / "monthly_returns.csv"


class Refused(RuntimeError):
    """The history cannot be written as asked; nothing was written."""


def load_spec(path: Path = SPEC) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def series_names(spec: dict) -> "list[str]":
    """The file's columns after ``month``: the funds in series.json's order, then the
    proxies, then the benchmark legs, then the bill."""
    return [*spec["funds"], *spec["proxies"], *spec.get("benchmarks", {}),
            spec["bill"]["series"]]


def fetch_history(ticker: str) -> pd.Series:
    """The provider's adjusted closes for ``ticker``'s whole history, from ONE request,
    indexed by session date. Raises Refused when the request fails."""
    from src.prices import _SESSION, _YF_CHART_URL
    params = {"interval": "1d", "period1": 0, "period2": int(time.time()) + 86400,
              "events": "div,splits", "includeAdjustedClose": "true"}
    try:
        resp = _SESSION.get(_YF_CHART_URL.format(ticker=ticker), params=params, timeout=30)
        resp.raise_for_status()
        result = resp.json()["chart"]["result"][0]
        stamps = result["timestamp"]
        adj = result["indicators"]["adjclose"][0]["adjclose"]
    except Exception as exc:                                     # noqa: BLE001
        raise Refused(f"{ticker}: the fetch failed ({type(exc).__name__}: {exc})") from exc
    days = [datetime.fromtimestamp(ts, tz=timezone.utc).date() for ts in stamps]
    s = pd.Series(adj, index=pd.to_datetime(days), dtype=float).dropna()
    s = s[s > 0]
    return s[~s.index.duplicated(keep="last")].sort_index()


def fetch_bills(start: str) -> pd.Series:
    """FRED's daily DGS3MO from ``start``, as decimals (0.0421 for 4.21%). Raises
    Refused when the request fails."""
    from src.macro import fetch_fred_series
    try:
        s = fetch_fred_series("DGS3MO", start)
    except Exception as exc:                                     # noqa: BLE001
        raise Refused(f"DGS3MO: the fetch failed ({type(exc).__name__}: {exc})") from exc
    s = pd.to_numeric(s, errors="coerce").dropna() / 100.0
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def last_sessions(reference: pd.Series) -> pd.Series:
    """{month 'YYYY-MM': its last session} from the reference series' dates."""
    idx = reference.index
    return pd.Series(idx, index=idx.strftime("%Y-%m")).groupby(level=0).max()


def monthly_returns(ticker: str, closes: pd.Series, sessions: pd.Series,
                    first_month: str, last_month: str) -> pd.Series:
    """{month: total return} for one series, from ``first_month`` (or its first full
    month, when it begins later) through ``last_month``. A month the returns need, from
    the month of the series' first close or the one before ``first_month``, whichever
    is later, whose last session it holds no close for is a gap, and refuses."""
    base = (pd.Period(first_month, "M") - 1).strftime("%Y-%m")
    first = max(closes.index.min().strftime("%Y-%m"), base)
    months = [m for m in sessions.index if first <= m <= last_month]
    missing = [m for m in months if sessions[m] not in closes.index]
    if missing:
        raise Refused(f"{ticker}: no close on the last session of {', '.join(missing[:6])}"
                      f"{' ...' if len(missing) > 6 else ''} ({len(missing)} month(s))")
    month_end = pd.Series([closes[sessions[m]] for m in months], index=months)
    return (month_end / month_end.shift(1) - 1).iloc[1:]


def bill_returns(yields: pd.Series, months: "list[str]") -> pd.Series:
    """{month: (1 + average daily yield) ** (1/12) - 1}. A month with no observation
    refuses."""
    by_month = yields.groupby(yields.index.strftime("%Y-%m")).mean()
    missing = [m for m in months if m not in by_month.index]
    if missing:
        raise Refused(f"DGS3MO: no observation in {', '.join(missing)}")
    return (1 + by_month.reindex(months)) ** (1 / 12) - 1


def _next(month: str) -> str:
    return (pd.Period(month, "M") + 1).strftime("%Y-%m")


def _check_splices(spec: dict, frame: pd.DataFrame) -> None:
    """Every fund's months are covered: by its proxy from ``backtest_from`` through
    ``proxy_until``, by itself after that. A fund replacing its proxy at inception begins
    exactly the month after; one replacing it at an index change already has a record."""
    months = list(frame.index)
    start = spec["backtest_from"]

    def lacks(col: str, lo: str, hi: str) -> "list[str]":
        return [m for m in months if lo <= m <= hi and pd.isna(frame.at[m, col])]

    for fund, f in spec["funds"].items():
        own_from = _next(f["proxy_until"]) if f.get("proxy") else start
        if f.get("proxy"):
            gap = lacks(f["proxy"], start, f["proxy_until"])
            if gap:
                raise Refused(f"{fund}: its proxy {f['proxy']} has no return for "
                              f"{', '.join(gap)}")
            first = frame[fund].first_valid_index()
            if f["reason"] == "inception" and first != own_from:
                raise Refused(f"{fund}: replaces {f['proxy']} at inception from {own_from}, "
                              f"but its first return is {first}")
        gap = lacks(fund, own_from, months[-1])
        if gap:
            raise Refused(f"{fund}: no return for {', '.join(gap)}")
    for leg in spec.get("benchmarks", {}):
        gap = lacks(leg, start, months[-1])
        if gap:
            raise Refused(f"{leg}: the benchmark leg has no return for {', '.join(gap)}")


def build(spec: dict, last_month: str, *,
          fetch: "Callable[[str], pd.Series] | None" = None,
          fetch_bills: "Callable[[str], pd.Series] | None" = None) -> pd.DataFrame:
    """The whole file as a frame indexed by month, or Refused. ``fetch`` and
    ``fetch_bills`` default to this module's fetch_history and fetch_bills."""
    fetch = fetch or globals()["fetch_history"]
    fetch_bills = fetch_bills or globals()["fetch_bills"]
    first = spec["first_month"]
    if last_month < spec["backtest_from"]:
        raise Refused(f"the last complete month {last_month} is before the backtest's "
                      f"first, {spec['backtest_from']}")
    priced = [*spec["funds"], *spec["proxies"], *spec.get("benchmarks", {})]
    histories = {t: fetch(t) for t in dict.fromkeys([spec["reference"], *priced])}
    sessions = last_sessions(histories[spec["reference"]])
    if last_month not in sessions.index:
        raise Refused(f"{spec['reference']} has no session in {last_month}")
    months = [m for m in sessions.index if first <= m <= last_month]

    columns = {}
    for t in priced:
        columns[t] = monthly_returns(t, histories[t], sessions, first, last_month)
    bill = spec["bill"]["series"]
    start = (pd.Period(first, "M").start_time - timedelta(days=7)).date().isoformat()
    columns[bill] = bill_returns(fetch_bills(start), months)

    frame = pd.DataFrame({c: s.reindex(months) for c, s in columns.items()},
                         index=pd.Index(months, name="month"))[series_names(spec)]
    _check_splices(spec, frame)
    return frame


def last_complete_month(today: date) -> str:
    return (pd.Period(today, "M") - 1).strftime("%Y-%m")


def write(frame: pd.DataFrame, out: "Path | None" = None) -> None:
    """Write the frame whole: to a temporary file beside ``out`` (this module's OUT when
    omitted, read at call time), then over it."""
    out = Path(out or globals()["OUT"])
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=out.stem, suffix=".tmp", dir=out.parent)
    os.close(fd)
    try:
        frame.to_csv(tmp, float_format="%.10g", lineterminator="\n")
        os.replace(tmp, out)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def main() -> int:
    from src.asof import today_et
    spec = load_spec()
    last = last_complete_month(today_et())
    try:
        frame = build(spec, last)
    except Refused as exc:
        print(f"REFUSED, nothing written: {exc}")
        return 1
    write(frame, OUT)
    print(f"Wrote {OUT}: {len(frame)} months, {frame.index[0]} to "
          f"{frame.index[-1]}, {frame.shape[1]} series.")
    for col in frame.columns:
        print(f"  {col:<7} from {frame[col].first_valid_index()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
