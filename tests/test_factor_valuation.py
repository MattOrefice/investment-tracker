"""
Tests for the Phase 30 value-spread feature.

Pure-computation tests (default suite) use a synthetic BE/ME breakpoints
fixture and injected data. Live-fetch tests are @pytest.mark.live_data and
excluded from the default suite per pytest.ini.
"""
from __future__ import annotations

import math
import sys
import pathlib

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.factors import _parse_beme_breakpoints_text, load_beme_breakpoints
from src.macro import percentile as macro_percentile
from src.factor_valuation import (
    build_value_spread_series,
    interpret_value_spread,
    valuation_percentile,
    value_spread,
)


# Synthetic fixture mirroring the real BE/ME breakpoints layout: a descriptive
# header, the partial "<= 0,>0" count-column header, two data rows (year + two
# firm-count columns + 20 breakpoints at p5..p100), and a copyright footer.
_SAMPLE_BEME = (
    "This file was created using the 202604 CRSP database.  "
    "It contains every 5th NYSE BEME percentile.\n"
    "\n"
    "  ,<= 0,>0\n"
    "1990,    10,   500,   0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, "
    "1.00, 1.10, 1.20, 1.30, 1.40, 1.50, 1.60, 1.70, 1.80, 1.90, 2.00\n"
    "1991,     8,   520,   0.20, 0.40, 0.60, 0.80, 1.00, 1.20, 1.40, 1.60, 1.80, "
    "2.00, 2.20, 2.40, 2.60, 2.80, 3.00, 3.20, 3.40, 3.60, 3.80, 4.00\n"
    "Copyright 2026 Eugene F. Fama and Kenneth R. French\n"
)


# ── parser ──────────────────────────────────────────────────────────────────────

def test_beme_parser_shape_and_index():
    df = _parse_beme_breakpoints_text(_SAMPLE_BEME)
    # Two data rows only (header + copyright footer excluded).
    assert len(df) == 2
    assert list(df.index) == [pd.Timestamp("1990-06-30"), pd.Timestamp("1991-06-30")]
    # 20 breakpoint columns, p5..p100.
    assert len(df.columns) == 20
    assert df.columns[0] == "p5" and df.columns[-1] == "p100"
    assert "p30" in df.columns and "p70" in df.columns


def test_beme_parser_values():
    df = _parse_beme_breakpoints_text(_SAMPLE_BEME)
    # p30 is the 6th breakpoint, p70 the 14th (mapping 5,10,...,100).
    assert df.loc["1990-06-30", "p30"] == pytest.approx(0.60)
    assert df.loc["1990-06-30", "p70"] == pytest.approx(1.40)
    assert df.loc["1990-06-30", "p5"] == pytest.approx(0.10)
    assert df.loc["1990-06-30", "p100"] == pytest.approx(2.00)
    assert df.loc["1991-06-30", "p30"] == pytest.approx(1.20)
    assert df.loc["1991-06-30", "p70"] == pytest.approx(2.80)


def test_beme_parser_rejects_empty():
    with pytest.raises(ValueError):
        _parse_beme_breakpoints_text("header only\nno data here\n")


# ── value_spread / build_value_spread_series ─────────────────────────────────────

def test_value_spread_is_log_ratio():
    assert value_spread(2.0, 0.5) == pytest.approx(math.log(4.0))
    assert value_spread(1.0, 1.0) == pytest.approx(0.0)


def test_build_value_spread_series_from_breakpoints():
    df = _parse_beme_breakpoints_text(_SAMPLE_BEME)
    s = build_value_spread_series(bp_df=df)
    assert s.name == "value_spread"
    assert s.index.is_monotonic_increasing
    # 1990: log(p70/p30) = log(1.40/0.60); 1991: log(2.80/1.20).
    assert s.loc["1990-06-30"] == pytest.approx(math.log(1.40 / 0.60))
    assert s.loc["1991-06-30"] == pytest.approx(math.log(2.80 / 1.20))


def test_build_value_spread_series_injected_columns():
    idx = pd.DatetimeIndex([pd.Timestamp(y, 6, 30) for y in (2020, 2021, 2022)])
    bp = pd.DataFrame({"p30": [0.30, 0.25, 0.20], "p70": [0.90, 0.75, 0.80]}, index=idx)
    s = build_value_spread_series(bp_df=bp)
    assert len(s) == 3
    assert s.iloc[0] == pytest.approx(math.log(0.90 / 0.30))


# ── percentile (reuses macro.percentile) ────────────────────────────────────────

def test_valuation_percentile_matches_macro_method():
    s = pd.Series([0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8])
    # 6 of 10 values are <= 1.0 → 60th percentile.
    assert valuation_percentile(s, 1.0) == pytest.approx(60.0)
    assert valuation_percentile(s, 1.0) == macro_percentile(s, 1.0)
    assert valuation_percentile(s, 1.8) == pytest.approx(100.0)


# ── interpret_value_spread (banded prose) ───────────────────────────────────────

def test_interpret_high_percentile_value_cheap():
    txt = interpret_value_spread(0.98, 0.92)
    assert "cheap" in txt
    assert "precede value outperformance" in txt
    assert "92nd percentile" in txt


def test_interpret_above_median_cheap():
    txt = interpret_value_spread(0.75, 0.78)
    assert "cheap" in txt
    assert "tailwind" in txt


def test_interpret_mid_percentile_neutral():
    txt = interpret_value_spread(0.66, 0.50)
    assert "neutral" in txt
    assert "median" in txt


def test_interpret_low_percentile_richly_priced():
    txt = interpret_value_spread(0.50, 0.05)
    assert "richly priced" in txt
    assert "tailwind" in txt
    assert "absent" in txt


# ── live fetch (deselected by default) ──────────────────────────────────────────

@pytest.mark.live_data
def test_load_beme_breakpoints_live():
    df = load_beme_breakpoints()
    assert "p30" in df.columns and "p70" in df.columns
    assert len(df.columns) == 20
    assert df.index.is_monotonic_increasing
    assert df.index.min().year <= 1930  # history back to the 1920s
    assert (df["p70"] > df["p30"]).all()  # value boundary above growth boundary


@pytest.mark.live_data
def test_build_value_spread_series_live():
    s = build_value_spread_series()
    assert s.name == "value_spread"
    assert not s.empty
    assert s.index.is_monotonic_increasing
    assert np.isfinite(s.iloc[-1])
    assert (s > 0).all()  # value boundary always above growth boundary → positive log
