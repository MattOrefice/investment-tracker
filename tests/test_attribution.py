"""Tests for src/attribution.py Brinson-Fachler decomposition."""
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.attribution import brinson_fachler


def test_two_sleeve_hand_calculation():
    """
    Hand-constructed two-sleeve scenario:

    Sleeve A: w_p=0.60, w_b=0.50, r_p=0.12, r_b=0.10
    Sleeve B: w_p=0.40, w_b=0.50, r_p=0.05, r_b=0.08

    r_b_total = 0.50*0.10 + 0.50*0.08 = 0.09
    r_p_total = 0.60*0.12 + 0.40*0.05 = 0.072 + 0.020 = 0.092

    Sleeve A:
      alloc = (0.60 - 0.50) * (0.10 - 0.09) = 0.10 * 0.01 = 0.001
      sel   = 0.60 * (0.12 - 0.10) = 0.60 * 0.02 = 0.012
      total = 0.013

    Sleeve B:
      alloc = (0.40 - 0.50) * (0.08 - 0.09) = (-0.10) * (-0.01) = 0.001
      sel   = 0.40 * (0.05 - 0.08) = 0.40 * (-0.03) = -0.012
      total = -0.011

    Sum of effects = 0.013 + (-0.011) = 0.002
    Active return  = 0.092 - 0.090   = 0.002  ✓
    """
    pw = {"A": 0.60, "B": 0.40}
    bw = {"A": 0.50, "B": 0.50}
    pr = {"A": 0.12, "B": 0.05}
    br = {"A": 0.10, "B": 0.08}

    df = brinson_fachler(pw, bw, pr, br)

    row_a = df[df["sleeve"] == "A"].iloc[0]
    row_b = df[df["sleeve"] == "B"].iloc[0]

    assert row_a["allocation_effect"] == pytest.approx(0.001, abs=1e-10)
    assert row_a["selection_effect"]  == pytest.approx(0.012, abs=1e-10)
    assert row_a["total_effect"]      == pytest.approx(0.013, abs=1e-10)

    assert row_b["allocation_effect"] == pytest.approx(0.001, abs=1e-10)
    assert row_b["selection_effect"]  == pytest.approx(-0.012, abs=1e-10)
    assert row_b["total_effect"]      == pytest.approx(-0.011, abs=1e-10)


def test_algebra_check_passes():
    """Sum of BF effects must equal portfolio − benchmark return within 1 bp."""
    pw = {"Equity": 0.70, "Bonds": 0.30}
    bw = {"Equity": 0.60, "Bonds": 0.40}
    pr = {"Equity": 0.15, "Bonds": 0.03}
    br = {"Equity": 0.12, "Bonds": 0.04}

    df = brinson_fachler(pw, bw, pr, br)

    r_p = 0.70 * 0.15 + 0.30 * 0.03
    r_b = 0.60 * 0.12 + 0.40 * 0.04
    active = r_p - r_b

    assert df["total_effect"].sum() == pytest.approx(active, abs=0.0001)


def test_equal_weights_no_allocation_effect():
    """When portfolio weights equal benchmark weights, allocation effect = 0."""
    pw = {"X": 0.50, "Y": 0.50}
    bw = {"X": 0.50, "Y": 0.50}
    pr = {"X": 0.20, "Y": 0.10}
    br = {"X": 0.15, "Y": 0.05}

    df = brinson_fachler(pw, bw, pr, br)

    for _, row in df.iterrows():
        assert row["allocation_effect"] == pytest.approx(0.0, abs=1e-10)


def test_equal_returns_no_selection_effect():
    """When portfolio return equals benchmark return per sleeve, selection = 0."""
    pw = {"X": 0.60, "Y": 0.40}
    bw = {"X": 0.50, "Y": 0.50}
    pr = {"X": 0.10, "Y": 0.08}   # same as br
    br = {"X": 0.10, "Y": 0.08}

    df = brinson_fachler(pw, bw, pr, br)

    for _, row in df.iterrows():
        assert row["selection_effect"] == pytest.approx(0.0, abs=1e-10)
