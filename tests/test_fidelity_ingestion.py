"""Tests for src/ingestion/fidelity.py — Fidelity CSV parser.

The parser pseudonymizes: it resolves the raw account number to a pseudonym via
account_map.json and drops the raw column. Real-export tests skip when the
personal-mode CSV is absent; the pseudonymization behavior is covered by
synthetic fixtures that use only fake, non-numeric identifiers.
"""
import json
import pathlib

import pytest
import pandas as pd

from src.ingestion.fidelity import parse_fidelity_csv
from src.household_data import find_latest_positions_csv

# Newest dated positions CSV in data/uploads/ (None if the personal file is absent).
SAMPLE_CSV = find_latest_positions_csv()

_HEADER = (
    "Account Number,Account Name,Symbol,Description,Quantity,"
    "Current Value,Cost Basis Total,Total Gain/Loss Dollar,Type"
)


def _df() -> pd.DataFrame:
    if SAMPLE_CSV is None or not SAMPLE_CSV.exists():
        pytest.skip("Sample CSV not present (personal-mode file, not committed)")
    return parse_fidelity_csv(str(SAMPLE_CSV))


def _write_positions_csv(tmp_path, data_rows) -> str:
    """Write a minimal Fidelity-format positions CSV; return its path."""
    path = tmp_path / "positions.csv"
    path.write_text("\n".join([_HEADER, *data_rows]) + "\n", encoding="utf-8")
    return str(path)


def _write_map(tmp_path, mapping) -> str:
    path = tmp_path / "account_map.json"
    path.write_text(json.dumps(mapping), encoding="utf-8")
    return str(path)


# ── Real-export tests (skip when the personal CSV is absent) ──────────────────

def test_row_count():
    # Row count verified against Jul 08 2026 Fidelity export: 97
    # holdings across 7 accounts, sum $221,930.85.
    df = _df()
    assert len(df) == 97, f"Expected 97 rows, got {len(df)}"


def test_total_current_value():
    df = _df()
    total = float(df["current_value"].sum())
    assert abs(total - 221_930.85) < 1.00, (
        f"Total current_value ${total:,.2f} not within $1 of $221,930.85"
    )


def test_no_nan_in_symbol_or_current_value():
    df = _df()
    assert df["symbol"].isna().sum() == 0, "NaN found in symbol column"
    assert df["current_value"].isna().sum() == 0, "NaN found in current_value column"


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
    # Jul-08 export: the self-directed sweep was largely deployed into SAA
    # tickers, leaving a $0.68 SPAXX residual (was $1,000.36 on May-27).
    assert abs(val - 0.68) < 1.00, f"SPAXX current_value ${val:.2f} unexpected"


# ── Pseudonymization behavior (synthetic; fake identifiers only) ──────────────

def test_leading_zero_account_number_resolves_through_map(tmp_path):
    """A leading-zero account number must be treated as a string (not coerced to
    int, which would drop the zero and miss the map) and resolve to its pseudonym.
    """
    csv_path = _write_positions_csv(tmp_path, [
        "01234567,Test Account,VOO,VANGUARD S&P 500,10,5000.00,4000.00,1000.00,Cash",
    ])
    map_path = _write_map(tmp_path, {"01234567": "TEST_ACCT_01"})
    df = parse_fidelity_csv(csv_path, account_map_path=map_path)
    assert df["pseudonym"].tolist() == ["TEST_ACCT_01"], (
        "Leading-zero account number was not resolved as a string through the map"
    )


def test_parsed_frame_has_no_account_number_column(tmp_path):
    csv_path = _write_positions_csv(tmp_path, [
        "01234567,Test Account,VOO,VANGUARD S&P 500,10,5000.00,4000.00,1000.00,Cash",
    ])
    map_path = _write_map(tmp_path, {"01234567": "TEST_ACCT_01"})
    df = parse_fidelity_csv(csv_path, account_map_path=map_path)
    assert "account_number" not in df.columns, "Raw account_number column must be dropped"
    assert "pseudonym" in df.columns


def test_unmapped_account_number_raises_and_keeps_row(tmp_path):
    """An account number absent from the map must raise — never silently drop the
    row, and never leak the raw number into the exception message.
    """
    csv_path = _write_positions_csv(tmp_path, [
        "01234567,Test Account,VOO,VANGUARD S&P 500,10,5000.00,4000.00,1000.00,Cash",
        "99999999,Other Account,BND,VANGUARD TOTAL BOND,5,500.00,500.00,0.00,Cash",
    ])
    map_path = _write_map(tmp_path, {"01234567": "TEST_ACCT_01"})  # 99999999 missing
    with pytest.raises(KeyError) as exc:
        parse_fidelity_csv(csv_path, account_map_path=map_path)
    assert "99999999" not in str(exc.value), "Raw account number leaked into the error message"


def test_missing_map_raises(tmp_path):
    csv_path = _write_positions_csv(tmp_path, [
        "01234567,Test Account,VOO,VANGUARD S&P 500,10,5000.00,4000.00,1000.00,Cash",
    ])
    with pytest.raises(FileNotFoundError):
        parse_fidelity_csv(csv_path, account_map_path=str(tmp_path / "nope.json"))
