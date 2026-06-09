"""Tests for src/market_snapshot.py — pure computation (synthetic) + gated live."""
from __future__ import annotations

import sys
import pathlib

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.market_snapshot import (
    SECTOR_ETFS,
    WINDOWS,
    rank_sectors,
    trailing_returns,
)


def _series(pairs):
    idx = pd.to_datetime([d for d, _ in pairs])
    return pd.Series([v for _, v in pairs], index=idx)


# ── trailing_returns ────────────────────────────────────────────────────────────

def test_trailing_returns_1d_and_ytd_known():
    s = _series([
        ("2025-12-31", 100.0),   # prior-year close → YTD base
        ("2026-01-02", 101.0),
        ("2026-05-01", 110.0),
        ("2026-06-01", 120.0),
        ("2026-06-04", 124.0),
        ("2026-06-05", 125.0),   # latest
    ])
    tr = trailing_returns({"X": s})
    assert tr.loc["X", "1D"] == pytest.approx(125.0 / 124.0 - 1)
    assert tr.loc["X", "YTD"] == pytest.approx(125.0 / 100.0 - 1)  # base = prior-year close
    # 1M cutoff = 2026-05-05 → first bar on/after is 2026-06-01 (120).
    assert tr.loc["X", "1M"] == pytest.approx(125.0 / 120.0 - 1)


def test_trailing_returns_1d_spans_weekend():
    # Last two bars are Friday then Monday → 1D spans the weekend gap.
    s = _series([
        ("2026-05-29", 100.0),   # Friday
        ("2026-06-01", 103.0),   # Monday
    ])
    tr = trailing_returns({"X": s}, windows=["1D"])
    assert tr.loc["X", "1D"] == pytest.approx(103.0 / 100.0 - 1)


def test_trailing_returns_1w_cutoff_on_nontrading_day_uses_next_bar():
    # last = Fri 2026-06-05; 1W cutoff = 2026-05-29 (Fri). Bars exist 05-28, 05-29,
    # 06-01, 06-05. First bar on/after the cutoff is 05-29 (value 100) → base 100.
    s = _series([
        ("2026-05-28", 99.0),
        ("2026-05-29", 100.0),
        ("2026-06-01", 101.0),
        ("2026-06-05", 105.0),
    ])
    tr = trailing_returns({"X": s}, windows=["1W"])
    assert tr.loc["X", "1W"] == pytest.approx(105.0 / 100.0 - 1)


def test_trailing_returns_preserves_window_order_and_index():
    s = _series([("2026-06-04", 100.0), ("2026-06-05", 101.0)])
    tr = trailing_returns({"A": s, "B": s})
    assert list(tr.columns) == WINDOWS
    assert set(tr.index) == {"A", "B"}


# ── rank_sectors ────────────────────────────────────────────────────────────────

def test_rank_sectors_orders_desc_with_best_worst():
    rt = pd.DataFrame(
        {"1M": [0.05, -0.03, 0.01]},
        index=["XLK", "XLU", "XLF"],
    )
    ranked = rank_sectors(rt, "1M")
    assert list(ranked["Ticker"]) == ["XLK", "XLF", "XLU"]          # descending
    assert ranked.iloc[0]["Sector"] == SECTOR_ETFS["XLK"]           # best
    assert ranked.iloc[-1]["Sector"] == SECTOR_ETFS["XLU"]          # worst
    assert ranked.iloc[0]["1M"] == pytest.approx(0.05)


def test_rank_sectors_ignores_non_sector_and_nan():
    rt = pd.DataFrame(
        {"1M": [0.05, float("nan"), 0.02]},
        index=["XLK", "XLE", "SPY"],   # SPY is not a sector; XLE is NaN
    )
    ranked = rank_sectors(rt, "1M")
    assert list(ranked["Ticker"]) == ["XLK"]


# ── live fetch (deselected by default) ──────────────────────────────────────────

@pytest.mark.live_data
def test_trailing_returns_live_sectors():
    from src.prices import get_prices
    pm = {}
    for t in list(SECTOR_ETFS):
        df = get_prices(t, "2025-04-01")
        pm[t] = pd.Series(df["adj_close"].values, index=pd.to_datetime(df.index))
    rt = trailing_returns(pm)
    assert list(rt.columns) == WINDOWS
    ranked = rank_sectors(rt, "1M")
    assert len(ranked) == len(SECTOR_ETFS)
    assert np.isfinite(ranked.iloc[0]["1M"])
