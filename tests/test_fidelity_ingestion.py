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

def test_every_source_position_survives_the_parse():
    """Invariant, not a pinned count: the parsed frame carries exactly the source
    CSV's position rows — same number, same symbols.

    Replaces `len(df) == 97`. That pin encoded a fact about EXTERNALLY MANAGED
    holdings, so it went stale with no visible trigger: the advisor's Aug-2026
    HLIPX-to-JCPB swap (JCPB in, an FCASH residual in, HLIPX out) moved the export
    from 97 rows to 98, and nothing in this repo caused or saw it. The count had
    already been bumped 85 -> 97 for the same reason. aeeb5c8 established the right
    principle — "invariants instead of real dollar figures" — and applied it to the
    value sum three tests below while leaving this one pinned.

    Both sides are computed independently: the left through the full parser (header
    normalization, account-map resolution, numeric cleaning), the right by reading
    the CSV directly and applying only the row-selection rule. A parser bug that
    drops a row moves one side and not the other. `len(df) > 0` would verify
    nothing, having no independent right-hand side at all.

    SYMBOL-SET equality as well as count: a swap — one row dropped, one invented —
    leaves the count identical and the set different. Given that a ticker swap is
    exactly what made the old pin stale, that is the likeliest recurrence here.

    Honest limit: this is more DURABLE than the pin but slightly LESS SENSITIVE. If
    the row-selection rule itself is wrong in the same way on both sides — say a
    pending-activity row that should be excluded and is not — the test agrees with
    the bug, where a pinned count would have broken once and caught it. The
    value-sum invariant below covers value-level mis-routing this cannot see.
    """
    from src.ingestion.fidelity import _normalize_header
    df = _df()  # parsed (skips when the personal CSV is absent)
    raw = pd.read_csv(SAMPLE_CSV, encoding="utf-8-sig", index_col=False, dtype=str)
    raw = raw.rename(columns={c: _normalize_header(c) for c in raw.columns})
    # Same row-selection rule the parser applies (src/ingestion/fidelity.py:127) —
    # keep only rows carrying a symbol, dropping footer paragraphs and blanks.
    expected = raw[raw["symbol"].notna() & (raw["symbol"].str.strip() != "")]

    assert len(df) == len(expected), (
        f"parsed {len(df)} rows from a source CSV carrying {len(expected)} "
        "position rows — ingestion dropped or invented rows"
    )
    # Compared as sorted LISTS, not sets: a symbol legitimately recurs across
    # accounts (SPAXX in several), so multiset equality also catches a duplicated
    # or de-duplicated row. The source side strips Fidelity's money-market '*'
    # suffix exactly as the parser does at src/ingestion/fidelity.py:130
    # ("SPAXX**" -> "SPAXX"); without it every sweep position reads as a mismatch.
    parsed_syms = sorted(df["symbol"].astype(str).str.strip())
    source_syms = sorted(
        expected["symbol"].astype(str).str.replace(r"\*+$", "", regex=True).str.strip()
    )
    assert parsed_syms == source_syms, (
        "parsed symbols differ from the source CSV's symbols (a swap keeps the "
        f"count identical): missing={sorted(set(source_syms) - set(parsed_syms))}, "
        f"invented={sorted(set(parsed_syms) - set(source_syms))}"
    )


def test_total_current_value_matches_source_sum():
    """Invariant, not a pinned figure: the parsed total equals the Current value
    column summed straight from the source CSV, to the cent. Proves ingest
    completeness (no rows/values dropped or mis-routed) without hardcoding a real
    portfolio dollar amount in tracked source, and never goes stale."""
    df = _df()  # parsed (skips when the personal CSV is absent)
    from src.ingestion.fidelity import _clean_numeric, _normalize_header
    raw = pd.read_csv(SAMPLE_CSV, encoding="utf-8-sig", index_col=False, dtype=str)
    raw = raw.rename(columns={c: _normalize_header(c) for c in raw.columns})
    raw = raw[raw["symbol"].notna() & (raw["symbol"].str.strip() != "")]
    source_total = float(_clean_numeric(raw["current value"]).sum())
    parsed_total = float(df["current_value"].sum())
    assert abs(parsed_total - source_total) < 0.01, (
        f"parsed Current value sum ({parsed_total:.2f}) != source CSV sum ({source_total:.2f})"
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
    """The money-market sweep row parses and its '**' suffix is stripped to
    'SPAXX' — no pinned dollar value (it's a near-zero cash residual)."""
    df = _df()
    matches = df[df["symbol"] == "SPAXX"]
    assert len(matches) == 1, "SPAXX row not found (or '**' suffix not stripped)"
    assert float(matches["current_value"].iloc[0]) >= 0, "SPAXX value did not parse to a number"


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
