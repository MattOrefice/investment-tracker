"""Tests for src/ingestion/fidelity.py — Fidelity CSV parser."""
import pathlib
import math

import pytest
import pandas as pd

from src.ingestion.fidelity import parse_fidelity_csv

SAMPLE_CSV = pathlib.Path(__file__).parent.parent / "data" / "uploads" / "Portfolio_Positions_May-27-2026.csv"


def _df() -> pd.DataFrame:
    if not SAMPLE_CSV.exists():
        pytest.skip("Sample CSV not present (personal-mode file, not committed)")
    return parse_fidelity_csv(str(SAMPLE_CSV))


def test_row_count():
    # Row count verified against May 27 2026 Fidelity export: 85
    # holdings across 7 accounts, sum $202,449.62.
    df = _df()
    assert len(df) == 85, f"Expected 85 rows, got {len(df)}"


def test_total_current_value():
    df = _df()
    total = float(df["current_value"].sum())
    assert abs(total - 202_449.62) < 1.00, (
        f"Total current_value ${total:,.2f} not within $1 of $202,449.62"
    )


def test_no_nan_in_symbol_or_current_value():
    df = _df()
    assert df["symbol"].isna().sum() == 0, "NaN found in symbol column"
    assert df["current_value"].isna().sum() == 0, "NaN found in current_value column"


def test_account_number_preserved_as_string():
    df = _df()
    expected = "acct_wkpl_02"
    matches = df[df["account_number"] == expected]
    assert len(matches) == 1, f"Plan account UUID not found as string: {expected}"


def test_cusip_symbol_present():
    df = _df()
    matches = df[df["symbol"] == "31564E540"]
    assert len(matches) == 1, "CUSIP '31564E540' not found in symbol column"
    desc = matches["description"].iloc[0].upper()
    assert "FRDM" in desc or "FREEDOM 2065" in desc, (
        f"Expected 'FRDM' or 'Freedom 2065' in description, got: {desc}"
    )


def test_spaxx_row():
    df = _df()
    matches = df[df["symbol"] == "SPAXX"]
    assert len(matches) == 1, "SPAXX row not found"
    val = float(matches["current_value"].iloc[0])
    assert abs(val - 1_000.36) < 1.00, f"SPAXX current_value ${val:.2f} unexpected"
