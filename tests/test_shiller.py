"""Tests for src/shiller.py — parser correctness and data freshness."""
import pytest
import pandas as pd

from src.shiller import _parse_multpl, _parse_shiller_date, current_cape, get_cape_series

# ── Fractional-year date parser ───────────────────────────────────────────────

def test_parse_shiller_date_january():
    ts = _parse_shiller_date(1881.01)
    assert ts == pd.Timestamp("1881-01-01")


def test_parse_shiller_date_december():
    ts = _parse_shiller_date(2023.12)
    assert ts == pd.Timestamp("2023-12-01")


def test_parse_shiller_date_midyear():
    ts = _parse_shiller_date(2000.06)
    assert ts == pd.Timestamp("2000-06-01")


def test_parse_shiller_date_invalid():
    assert _parse_shiller_date("not-a-date") is None
    assert _parse_shiller_date(None) is None


# ── multpl.com HTML parser — 2026 format fixture ──────────────────────────────

# Minimal HTML matching the multpl.com table structure as of May 2026.
# This fixture locks the parser against format changes — if multpl.com restructures
# their table, this test will fail at CI time rather than silently at render time.
_MULTPL_FIXTURE_HTML = """
<html><body>
<table>
  <thead><tr><th>Date</th><th>Value</th></tr></thead>
  <tbody>
    <tr><td>May 4, 2026</td><td>40.90</td></tr>
    <tr><td>Apr 1, 2026</td><td>38.34</td></tr>
    <tr><td>Mar 1, 2026</td><td>37.66</td></tr>
    <tr><td>Dec 1, 1999</td><td>44.20</td></tr>
    <tr><td>Jan 1, 1881</td><td>18.75</td></tr>
  </tbody>
</table>
</body></html>
"""


def test_parse_multpl_row_count():
    df = _parse_multpl(_MULTPL_FIXTURE_HTML)
    assert len(df) == 5


def test_parse_multpl_sorted_ascending():
    df = _parse_multpl(_MULTPL_FIXTURE_HTML)
    assert list(df["date"]) == sorted(df["date"])


def test_parse_multpl_normalises_to_month_start():
    """May 4, 2026 must be normalised to May 1, 2026."""
    df = _parse_multpl(_MULTPL_FIXTURE_HTML)
    latest = df.iloc[-1]
    assert latest["date"] == pd.Timestamp("2026-05-01")
    assert latest["cape"] == pytest.approx(40.90)


def test_parse_multpl_oldest_row():
    df = _parse_multpl(_MULTPL_FIXTURE_HTML)
    oldest = df.iloc[0]
    assert oldest["date"] == pd.Timestamp("1881-01-01")
    assert oldest["cape"] == pytest.approx(18.75)


def test_parse_multpl_cape_values():
    df = _parse_multpl(_MULTPL_FIXTURE_HTML)
    capes = list(df["cape"])
    assert pytest.approx(40.90) in capes
    assert pytest.approx(44.20) in capes
    assert pytest.approx(38.34) in capes


def test_parse_multpl_missing_columns_raises():
    bad_html = "<table><tr><th>Foo</th><th>Bar</th></tr><tr><td>x</td><td>1</td></tr></table>"
    with pytest.raises(RuntimeError, match="Unexpected"):
        _parse_multpl(bad_html)


def test_parse_multpl_no_tables_raises():
    with pytest.raises(RuntimeError, match="No tables"):
        _parse_multpl("<html><body>no table here</body></html>")


# ── Live data freshness smoke tests ───────────────────────────────────────────

def test_current_cape_is_positive():
    """Smoke test: current CAPE must be positive (data must have loaded)."""
    assert current_cape() > 0


def test_cape_series_extends_through_2025():
    """Data must extend at least through end of 2025 (catches silent fallback to stale cache)."""
    s = get_cape_series()
    assert s.index.max() >= pd.Timestamp("2025-12-01"), (
        f"CAPE data only extends to {s.index.max().date()} — "
        "multpl.com fetch may have failed and stale cache is being used"
    )


def test_current_cape_above_30():
    """
    Current CAPE (May 2026) must exceed 30. Value at multpl.com is ~40.
    If this fails, the data source has regressed to the stale Sep 2023 cache.
    """
    assert current_cape() > 30, (
        f"Current CAPE {current_cape():.1f} is unexpectedly low — "
        "likely serving the stale Sep 2023 Yale fallback instead of live multpl.com data"
    )
