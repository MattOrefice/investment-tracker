"""The risk-free rate: the average 3-month Treasury bill rate over a window.

Sharpe, Sortino and Asset Evaluation's mean-variance analysis used one fixed 4.5% for
every window (2026-09-25 audit, item 15a). Applied to the demo's 17-month history, and
to Asset Evaluation's sample from 2018, which includes two years of near-zero bills, a
single "current" rate is the wrong measure, not only a stale one. The rate is now the
average of FRED's daily 3-month constant-maturity Treasury series (DGS3MO, the series
the Macro page already reads) over the window a ratio measures. Sortino's target is the
same rate: its shortfall is measured below it.

Where the series comes from, in order:
  * FRED, through src.macro.get_series (today's cache row, or a fetch). The request
    starts where the Macro page's does, so the two share one cache row.
  * FRED unavailable: the newest stored series in macro_cache, whatever day it was
    fetched, and its end date is stated, as prices state theirs.
  * Neither: RiskFreeUnavailable, and the ratios that need the rate are not shown.

No quarter lock holds the rate, because no locked report section reads it: the PDF's
Asset Evaluation section is built outside the lock by design (#368), and the factor and
benchmark regressions subtract French's own RF column, which the lock does hold.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import NamedTuple, Optional

import pandas as pd

SERIES_ID = "DGS3MO"
FETCH_START = "1990-01-01"      # the Macro page's request (pages/3_Macro.py)

FRED = "fred"
STORED = "stored"


class RiskFreeUnavailable(RuntimeError):
    """Neither FRED nor a stored series can supply the 3-month bill rate."""


class BillSeries(NamedTuple):
    """Daily 3-month bill yields as decimals (0.0421 for 4.21%), DatetimeIndex."""
    series: pd.Series
    basis: str                  # FRED or STORED

    @property
    def end(self) -> date:
        return self.series.index.max().date()


class RiskFreeRate(NamedTuple):
    annual: float               # decimal
    basis: str                  # FRED or STORED
    start: date                 # first observation averaged
    through: date               # last observation averaged
    series_end: date            # the series' last observation, averaged or not


def _decimal(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce").dropna() / 100.0
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def _stored() -> "pd.Series | None":
    """The newest DGS3MO row in macro_cache, whatever day it was fetched."""
    from src.db import get_connection
    with get_connection() as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND "
                              "name = 'macro_cache'").fetchone()
        if not exists:
            return None
        row = conn.execute("SELECT data FROM macro_cache WHERE series_id = ? "
                           "ORDER BY fetch_date DESC LIMIT 1", (SERIES_ID,)).fetchone()
    if row is None:
        return None
    payload = json.loads(row["data"])
    s = pd.Series([v if v is not None else float("nan") for v in payload["values"]],
                  index=pd.to_datetime(payload["dates"]), dtype=float)
    return _decimal(s)


def bill_series() -> BillSeries:
    """The 3-month bill series from FRED, else the newest stored one."""
    from src import macro
    try:
        s = _decimal(macro.get_series(SERIES_ID, FETCH_START))
        if not s.empty:
            return BillSeries(s, FRED)
    except Exception as exc:                          # noqa: BLE001
        logging.info("3-month bill series from FRED unavailable (%s: %s); using the "
                     "stored series", type(exc).__name__, exc)
    s = _stored()
    if s is not None and not s.empty:
        return BillSeries(s, STORED)
    raise RiskFreeUnavailable(
        "the 3-month Treasury bill rate could not be read: FRED is unavailable and no "
        "stored series is on file")


def rate_over(start, end, bills: "BillSeries | None" = None) -> RiskFreeRate:
    """The average bill rate over [start, end]. A window the series does not reach,
    the stored series' tail for instance, takes its last observation before ``end``;
    a window before the series begins raises RiskFreeUnavailable."""
    bills = bills or bill_series()
    s = bills.series
    lo, hi = pd.Timestamp(start), pd.Timestamp(end)
    win = s[(s.index >= lo) & (s.index <= hi)]
    if win.empty:
        win = s[s.index <= hi].iloc[-1:]
    if win.empty:
        raise RiskFreeUnavailable(
            f"the 3-month Treasury bill series starts {bills.series.index.min().date()}, "
            f"after the window ends {hi.date()}")
    return RiskFreeRate(float(win.mean()), bills.basis, win.index[0].date(),
                        win.index[-1].date(), bills.end)


def describe(rate: RiskFreeRate) -> str:
    """The rate and where it came from, for a caption: "4.21%, the average 3-month
    Treasury bill rate from May 1, 2025 to September 24, 2026"."""
    from src.asof import format_long_date as _long
    if rate.start == rate.through:
        text = f"{rate.annual:.2%}, the 3-month Treasury bill rate on {_long(rate.through)}"
    else:
        text = (f"{rate.annual:.2%}, the average 3-month Treasury bill rate from "
                f"{_long(rate.start)} to {_long(rate.through)}")
    if rate.basis == STORED:
        text += (f". FRED was unavailable, so the rate comes from the stored series, "
                 f"which ends {_long(rate.series_end)}")
    return text
