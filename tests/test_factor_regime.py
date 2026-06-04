"""
Tests for src/factor_regime.py — trailing-12-month size/style factor series.

Pure-computation tests use synthetic input and run in the default suite.
Live-fetch tests (Ken French + Yahoo) are marked @pytest.mark.live_data and
are deselected by default per pytest.ini.
"""
from __future__ import annotations

import sys
import pathlib

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.macro import percentile as macro_percentile
from src.factor_regime import (
    build_size_factor_frame,
    build_style_factor_frame,
    factor_percentile,
    filter_window,
    interpret_factor,
    rolling_12m_etf_relative,
    rolling_12m_ff_premium,
)


# ── rolling_12m_ff_premium ──────────────────────────────────────────────────────

def test_ff_premium_compounds_twelve_one_percent_months():
    """12 monthly factor returns of +1% compound to (1.01**12 - 1) ≈ 12.68%."""
    idx = pd.date_range("2020-01-31", periods=12, freq="ME")
    ff = pd.DataFrame({"SMB": [0.01] * 12}, index=idx)

    out = rolling_12m_ff_premium(ff, "SMB", window=12)

    assert len(out) == 1  # only the 12th row has a full trailing window
    assert out.iloc[-1] == pytest.approx(12.6825, abs=1e-3)


def test_ff_premium_is_percent_and_drops_warmup():
    """Window of N over M>N rows yields M-N+1 points; output is in percent."""
    idx = pd.date_range("2020-01-31", periods=18, freq="ME")
    ff = pd.DataFrame({"HML": [0.005] * 18}, index=idx)

    out = rolling_12m_ff_premium(ff, "HML", window=12)

    assert len(out) == 18 - 12 + 1
    # 12 months of +0.5% → (1.005**12 - 1) * 100 ≈ 6.168%
    assert out.iloc[-1] == pytest.approx(6.1678, abs=1e-3)


def test_ff_premium_handles_mixed_signs():
    """A flat 0% year compounds to exactly 0%."""
    idx = pd.date_range("2020-01-31", periods=12, freq="ME")
    ff = pd.DataFrame({"SMB": [0.0] * 12}, index=idx)

    out = rolling_12m_ff_premium(ff, "SMB", window=12)

    assert out.iloc[-1] == pytest.approx(0.0, abs=1e-9)


# ── rolling_12m_etf_relative ────────────────────────────────────────────────────

def test_etf_relative_is_small_minus_large():
    """Small +20% vs large +10% over the window → relative = +10 pp."""
    idx = pd.date_range("2021-01-01", periods=13, freq="D")
    small = pd.Series(np.linspace(100.0, 120.0, 13), index=idx)  # +20%
    large = pd.Series(np.linspace(100.0, 110.0, 13), index=idx)  # +10%

    out = rolling_12m_etf_relative(small, large, window=12)

    assert len(out) == 1
    assert out.iloc[-1] == pytest.approx(10.0, abs=1e-6)


def test_etf_relative_negative_when_large_leads():
    """When the large/growth leg leads, the relative return is negative."""
    idx = pd.date_range("2021-01-01", periods=13, freq="D")
    small = pd.Series(np.linspace(100.0, 105.0, 13), index=idx)   # +5%
    large = pd.Series(np.linspace(100.0, 115.0, 13), index=idx)   # +15%

    out = rolling_12m_etf_relative(small, large, window=12)

    assert out.iloc[-1] == pytest.approx(-10.0, abs=1e-6)


def test_etf_relative_aligns_on_common_dates():
    """Misaligned indices are inner-joined before differencing."""
    idx_small = pd.date_range("2021-01-01", periods=14, freq="D")
    idx_large = pd.date_range("2021-01-02", periods=14, freq="D")  # offset by 1
    small = pd.Series(np.linspace(100.0, 126.0, 14), index=idx_small)
    large = pd.Series(np.linspace(100.0, 113.0, 14), index=idx_large)

    out = rolling_12m_etf_relative(small, large, window=12)

    assert not out.empty
    assert out.index.is_monotonic_increasing


# ── frame builders (injected synthetic fetch — pure, no network) ────────────────

def _synthetic_ff() -> pd.DataFrame:
    idx = pd.date_range("2018-01-31", periods=36, freq="ME")
    rng = np.linspace(-0.01, 0.01, 36)
    return pd.DataFrame({"SMB": rng, "HML": rng[::-1]}, index=idx)


def _synthetic_prices(start: str, periods: int, end_mult: float) -> pd.Series:
    idx = pd.date_range(start, periods=periods, freq="D")
    return pd.Series(np.linspace(100.0, 100.0 * end_mult, periods), index=idx)


def test_build_size_factor_frame_shape_injected():
    frame = build_size_factor_frame(
        window=12,
        history_start="1900-01-01",
        ff_df=_synthetic_ff(),
        iwm_prices=_synthetic_prices("2018-01-01", 60, 1.30),
        iwb_prices=_synthetic_prices("2018-01-01", 60, 1.15),
    )
    assert set(frame.columns) == {"ff_smb_12m", "etf_iwm_iwb_12m"}
    assert frame.index.is_monotonic_increasing
    assert isinstance(frame.index, pd.DatetimeIndex)
    assert not frame.empty


def test_build_style_factor_frame_shape_injected():
    frame = build_style_factor_frame(
        window=12,
        history_start="1900-01-01",
        ff_df=_synthetic_ff(),
        iwd_prices=_synthetic_prices("2018-01-01", 60, 1.20),
        iwf_prices=_synthetic_prices("2018-01-01", 60, 1.25),
    )
    assert set(frame.columns) == {"ff_hml_12m", "etf_iwd_iwf_12m"}
    assert frame.index.is_monotonic_increasing
    assert isinstance(frame.index, pd.DatetimeIndex)
    assert not frame.empty


# ── factor_percentile (reuses src.macro.percentile) ────────────────────────────

def test_factor_percentile_matches_macro_method():
    """Percentile reuses macro.percentile: fraction of obs <= current, ×100."""
    s = pd.Series([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0])
    # 6 of 10 values are <= 5.0 → 60th percentile.
    assert factor_percentile(s, 5.0) == pytest.approx(60.0)
    # Identical to the canonical macro method for the same inputs.
    assert factor_percentile(s, 5.0) == macro_percentile(s, 5.0)
    assert factor_percentile(s, 9.0) == pytest.approx(100.0)
    assert factor_percentile(s, -1.0) == pytest.approx(0.0)


# ── filter_window (display windowing only) ──────────────────────────────────────

def test_filter_window_trailing_three_years():
    idx = pd.date_range("2016-01-01", "2026-01-01", freq="MS")
    frame = pd.DataFrame({"x": np.arange(len(idx))}, index=idx)

    out = filter_window(frame, "3Y", today="2026-01-01")

    assert out.index.min() >= pd.Timestamp("2023-01-01")
    assert out.index.max() == pd.Timestamp("2026-01-01")
    # Every row in the 3Y view is within the trailing three years.
    assert (out.index >= pd.Timestamp("2023-01-01")).all()


def test_filter_window_max_returns_all():
    idx = pd.date_range("2016-01-01", "2026-01-01", freq="MS")
    frame = pd.DataFrame({"x": np.arange(len(idx))}, index=idx)

    out = filter_window(frame, "Max", today="2026-01-01")

    assert len(out) == len(frame)
    assert out.index.equals(frame.index)


def test_filter_window_one_year_subset_of_five_year():
    idx = pd.date_range("2016-01-01", "2026-01-01", freq="MS")
    frame = pd.DataFrame({"x": np.arange(len(idx))}, index=idx)

    one_y = filter_window(frame, "1Y", today="2026-01-01")
    five_y = filter_window(frame, "5Y", today="2026-01-01")

    assert len(one_y) < len(five_y) < len(frame)
    assert set(one_y.index).issubset(set(five_y.index))


# ── interpret_factor (sign × percentile branches) ───────────────────────────────

def test_interpret_positive_high_percentile():
    txt = interpret_factor(6.3, 82.0, 13.2, 90.0, "size")
    assert "outperformed" in txt
    assert "rewarded" in txt
    assert "above its typical strength" in txt
    # Fama-French value is cited before the ETF-proxy value (FF-led ordering).
    assert txt.index("+6.3%") < txt.index("+13.2%")
    # Both legs positive → no claim of disagreement.
    assert "mixed" not in txt and "opposite" not in txt


def test_interpret_positive_low_percentile():
    txt = interpret_factor(1.5, 12.0, 0.8, 18.0, "size")
    assert "outperformed" in txt
    assert "below its typical strength" in txt


def test_interpret_negative_is_headwind():
    txt = interpret_factor(-8.0, 14.0, -10.0, 20.0, "style")
    assert "lagged" in txt
    assert "headwind" in txt or "drag" in txt
    # FF value before ETF value.
    assert txt.index("-8.0%") < txt.index("-10.0%")


def test_interpret_agreement_vs_disagreement():
    agree = interpret_factor(5.0, 70.0, 9.0, 80.0, "size")
    assert "confirming the same direction" in agree
    assert "mixed" not in agree

    disagree = interpret_factor(3.0, 60.0, -1.0, 40.0, "style")
    # FF positive but ETF negative → must NOT claim clean agreement.
    assert "confirming the same direction" not in disagree
    assert "mixed" in disagree or "opposite" in disagree


def test_interpret_leads_with_fama_french():
    txt = interpret_factor(4.0, 55.0, 7.0, 65.0, "size")
    assert txt.index("Fama-French") < txt.index("proxy")


# ── live fetch (deselected by default) ──────────────────────────────────────────

@pytest.mark.live_data
def test_build_size_factor_frame_live():
    frame = build_size_factor_frame()
    assert set(frame.columns) == {"ff_smb_12m", "etf_iwm_iwb_12m"}
    assert frame.index.is_monotonic_increasing
    assert frame["ff_smb_12m"].dropna().shape[0] > 0
    assert frame["etf_iwm_iwb_12m"].dropna().shape[0] > 0
    assert np.isfinite(frame["etf_iwm_iwb_12m"].dropna().iloc[-1])


@pytest.mark.live_data
def test_build_style_factor_frame_live():
    frame = build_style_factor_frame()
    assert set(frame.columns) == {"ff_hml_12m", "etf_iwd_iwf_12m"}
    assert frame.index.is_monotonic_increasing
    assert frame["ff_hml_12m"].dropna().shape[0] > 0
    assert frame["etf_iwd_iwf_12m"].dropna().shape[0] > 0
    assert np.isfinite(frame["etf_iwd_iwf_12m"].dropna().iloc[-1])
