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

# Fidelity positions header in three spellings. Real current exports ship the
# lowercase form; older exports used title case. The parser is casing- and
# whitespace-insensitive, so all three must resolve identically.
_HEADER_TITLE = (
    "Account Number,Account Name,Symbol,Description,Quantity,"
    "Current Value,Cost Basis Total,Total Gain/Loss Dollar,Type"
)
_HEADER_LOWER = (
    "account number,account name,symbol,description,quantity,"
    "current value,cost basis total,total gain/loss dollar,type"
)
_HEADER_SPACED = (  # a double space inside "Account  Number"
    "Account  Number,Account Name,Symbol,Description,Quantity,"
    "Current Value,Cost Basis Total,Total Gain/Loss Dollar,Type"
)
# Default fixtures use the real-world lowercase spelling so the suite exercises
# the format Fidelity actually ships today, not just the historical title case.
_HEADER = _HEADER_LOWER


def _df() -> pd.DataFrame:
    if SAMPLE_CSV is None or not SAMPLE_CSV.exists():
        pytest.skip("Sample CSV not present (personal-mode file, not committed)")
    return parse_fidelity_csv(str(SAMPLE_CSV))


def _write_positions_csv(tmp_path, data_rows, header=_HEADER, name="positions.csv") -> str:
    """Write a minimal Fidelity-format positions CSV; return its path."""
    path = tmp_path / name
    path.write_text("\n".join([header, *data_rows]) + "\n", encoding="utf-8")
    return str(path)


def _write_map(tmp_path, mapping) -> str:
    path = tmp_path / "account_map.json"
    path.write_text(json.dumps(mapping), encoding="utf-8")
    return str(path)


# ── Real-export tests (skip when the personal CSV is absent) ──────────────────

def test_row_count():
    # Row count verified against Jul 10 2026 Fidelity export: 97
    # holdings across 7 accounts, sum $224,584.68.
    df = _df()
    assert len(df) == 97, f"Expected 97 rows, got {len(df)}"


def test_total_current_value():
    # $ total tracks the current export (Jul 10 2026); the market moved it up
    # from the Jul-08 figure of $221,930.85. Re-pin when a newer CSV lands.
    df = _df()
    total = float(df["current_value"].sum())
    assert abs(total - 224_584.68) < 1.00, (
        f"Total current_value ${total:,.2f} not within $1 of $224,584.68"
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


# ── Header casing / whitespace tolerance (the format Fidelity changed) ─────────

_ROW = "01234567,Test Account,VOO,VANGUARD S&P 500,10,5000.00,4000.00,1000.00,Cash"


def test_lowercase_headers_match_title_case(tmp_path):
    """A lowercase-header export (Fidelity's current format) must parse to the
    exact same DataFrame as the historical title-case one — both must work."""
    m = _write_map(tmp_path, {"01234567": "TEST_ACCT_01"})
    lower = parse_fidelity_csv(
        _write_positions_csv(tmp_path, [_ROW], header=_HEADER_LOWER, name="lower.csv"),
        account_map_path=m,
    )
    title = parse_fidelity_csv(
        _write_positions_csv(tmp_path, [_ROW], header=_HEADER_TITLE, name="title.csv"),
        account_map_path=m,
    )
    pd.testing.assert_frame_equal(lower, title)


def test_title_case_headers_still_parse(tmp_path):
    """The historical title-case format must keep working (older saved exports)."""
    m = _write_map(tmp_path, {"01234567": "TEST_ACCT_01"})
    df = parse_fidelity_csv(
        _write_positions_csv(tmp_path, [_ROW], header=_HEADER_TITLE, name="title.csv"),
        account_map_path=m,
    )
    assert df["pseudonym"].tolist() == ["TEST_ACCT_01"]
    assert "account_number" not in df.columns


def test_extra_whitespace_in_header_resolves(tmp_path):
    """A header with a double space ("Account  Number") must still resolve —
    internal whitespace is collapsed during normalization."""
    m = _write_map(tmp_path, {"01234567": "TEST_ACCT_01"})
    df = parse_fidelity_csv(
        _write_positions_csv(tmp_path, [_ROW], header=_HEADER_SPACED, name="spaced.csv"),
        account_map_path=m,
    )
    assert df["pseudonym"].tolist() == ["TEST_ACCT_01"]
    assert {"account_name", "symbol", "current_value"} <= set(df.columns)


def test_missing_required_column_raises_naming_canonical(tmp_path):
    """A required column absent under ANY casing must still raise, naming the
    canonical column — never silently defaulted away."""
    header = (  # 'current value' dropped entirely, in any casing
        "account number,account name,symbol,description,quantity,"
        "cost basis total,total gain/loss dollar,type"
    )
    row = "01234567,Test Account,VOO,VANGUARD S&P 500,10,4000.00,1000.00,Cash"
    path = tmp_path / "missing.csv"
    path.write_text(header + "\n" + row + "\n", encoding="utf-8")
    m = _write_map(tmp_path, {"01234567": "TEST_ACCT_01"})
    with pytest.raises(KeyError) as exc:
        parse_fidelity_csv(str(path), account_map_path=m)
    assert "current_value" in str(exc.value), "error must name the missing canonical column"


def test_real_export_parses_without_keyerror():
    """The newest real positions CSV (if present) must parse without a header
    KeyError. Skips cleanly when the personal file is absent so CI stays green."""
    if SAMPLE_CSV is None or not SAMPLE_CSV.exists():
        pytest.skip("Sample CSV not present (personal-mode file, not committed)")
    df = parse_fidelity_csv(str(SAMPLE_CSV))
    assert len(df) > 0
    assert "pseudonym" in df.columns
    assert "account_number" not in df.columns
