"""A quarter lock's NON-PRICE inputs, served to every reader inside the lock (#382).

The quarter lock froze prices only (src.cache, #371). Every other input a locked
report section reads came from its file or table at render time: the Fama-French
factors, the momentum series, the HYG credit proxy, Shiller CAPE, the ETF metadata and
the dividend record. So a provider's revision restated a locked quarter: refreshing
French's data moved Q2's factor alpha from -33 to -27 bps/yr, and the executive
summary cited August's CAPE for a quarter that ended in June.

A lock now keeps each input as its section read it, through the quarter's last day.
Inside cache.snapshot_price_context, each reader below asks ``locked(name)`` first:

  * a value            -> the lock holds this input: serve it, never the file;
  * InputPending       -> the lock governs this input but its data did not cover the
                          quarter when locked (French publishes a month late), so the
                          section is pending: it must not read live data either;
  * None               -> no lock, or a lock that predates inputs: read as usual.

Same mechanism as src.prices._PRICE_LOCK: a ContextVar consulted inside the reader,
because consumers import the readers by name (#368's lesson).
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional

# The inputs a lock can hold. Each is named after its reader.
FF5_US = "ff5_us"                      # factors.load_factors("us")
FF5_DEVELOPED_EXUS = "ff5_developed_exus"  # factors.load_factors("developed_exus")
UMD = "umd"                            # factors.load_umd_factor()
HYG = "hyg"                            # factors.regress_fi_sleeve's credit proxy
CAPE = "cape"                          # shiller.get_cape_series()
ETF_METADATA = "etf_metadata"          # style_box._load_metadata()
DIVIDENDS = "dividends"                # prices.get_dividends(), per ticker

ALL_INPUTS = (FF5_US, FF5_DEVELOPED_EXUS, UMD, HYG, CAPE, ETF_METADATA, DIVIDENDS)


class InputPending(Exception):
    """A locked section asked for an input its quarter's lock is still waiting on."""

    def __init__(self, name: str, through: Optional[str], quarter_end: Optional[str]):
        self.name = name
        self.through = through
        self.quarter_end = quarter_end
        super().__init__(
            f"{name} is pending for this quarter: its data ends {through or 'nowhere'}, "
            f"before the quarter's end {quarter_end}")


class _Locked:
    def __init__(self, values: dict, pending: dict, quarter_end: Optional[str]):
        self.values = values
        self.pending = pending          # name -> the data's end date when locked
        self.quarter_end = quarter_end


_INPUT_LOCK: ContextVar[Optional[_Locked]] = ContextVar("input_lock", default=None)


def locked(name: str):
    """The locked value of ``name``, or None to read as usual. Raises InputPending
    for an input the lock governs but has not yet covered."""
    lock = _INPUT_LOCK.get()
    if lock is None:
        return None
    if name in lock.values:
        return lock.values[name]
    if name in lock.pending:
        raise InputPending(name, lock.pending[name], lock.quarter_end)
    return None


@contextmanager
def input_context(values: dict, pending: dict, quarter_end: Optional[str]):
    """Serve ``values`` to every reader in the block; ``pending`` names inputs that
    must not be read live. An empty lock (both empty) is the same as no lock."""
    token = _INPUT_LOCK.set(_Locked(values, pending, quarter_end) if (values or pending) else None)
    try:
        yield
    finally:
        _INPUT_LOCK.reset(token)
