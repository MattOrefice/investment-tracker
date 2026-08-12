"""Tests for src/trailing_pe.py — parser correctness, estimate-dagger handling."""
import pytest
import pandas as pd

from src.trailing_pe import _parse_multpl_pe, _parse_pe_value

# ── value parsing — the estimate dagger is the load-bearing case ──────────────
# multpl marks recent months with a leading dagger ("† 28.89") while the
# underlying earnings are preliminary. src/shiller.py's strict float() would
# drop those rows silently; the CAPE table carries no daggers so it never
# mattered there. Here the dagger rows ARE the current readings.


def test_parse_pe_value_plain():
    assert _parse_pe_value("28.89") == pytest.approx(28.89)


def test_parse_pe_value_estimate_dagger():
    assert _parse_pe_value("† 28.89") == pytest.approx(28.89)
    assert _parse_pe_value("†\xa028.89") == pytest.approx(28.89)


def test_parse_pe_value_thousands_comma():
    assert _parse_pe_value("1,028.89") == pytest.approx(1028.89)


def test_parse_pe_value_garbage_and_nonpositive():
    assert _parse_pe_value("n/a") is None
    assert _parse_pe_value("") is None
    assert _parse_pe_value("0") is None


# ── multpl.com HTML parser — 2026 format fixture ──────────────────────────────
# Mirrors the table structure as of Jul 2026: newest-first, a current
# partial-month top row, and dagger-marked recent months. Locks the parser
# against format drift — fails at CI time, not silently at render time.

_MULTPL_PE_FIXTURE_HTML = """
<html><body>
<table>
  <thead><tr><th>Date</th><th>Value</th></tr></thead>
  <tbody>
    <tr><td>Jul 21, 2026</td><td>† 28.89</td></tr>
    <tr><td>Jul 1, 2026</td><td>† 28.71</td></tr>
    <tr><td>Jun 1, 2026</td><td>† 28.47</td></tr>
    <tr><td>Jun 1, 2022</td><td>20.28</td></tr>
    <tr><td>Jan 1, 1871</td><td>11.10</td></tr>
  </tbody>
</table>
</body></html>
"""


def test_parse_pe_keeps_dagger_rows():
    """Estimate-marked rows must be parsed, not dropped — they are the
    current readings the Valuation page displays."""
    df = _parse_multpl_pe(_MULTPL_PE_FIXTURE_HTML)
    assert pd.Timestamp("2026-07-01") in set(df["date"])
    assert pd.Timestamp("2026-06-01") in set(df["date"])


def test_parse_pe_current_month_wins_collision():
    """'Jul 21, 2026' and 'Jul 1, 2026' both normalise to Jul-01; the fresher
    reading (the top row) must win, and its true observation date is kept."""
    df = _parse_multpl_pe(_MULTPL_PE_FIXTURE_HTML)
    jul = df[df["date"] == pd.Timestamp("2026-07-01")]
    assert len(jul) == 1
    assert jul.iloc[0]["pe"] == pytest.approx(28.89)
    assert jul.iloc[0]["obs_date"] == pd.Timestamp("2026-07-21")


def test_parse_pe_sorted_ascending_and_full_span():
    df = _parse_multpl_pe(_MULTPL_PE_FIXTURE_HTML)
    assert list(df["date"]) == sorted(df["date"])
    assert df.iloc[0]["date"] == pd.Timestamp("1871-01-01")
    assert df.iloc[0]["pe"] == pytest.approx(11.10)


def test_parse_pe_raises_on_tableless_html():
    with pytest.raises(RuntimeError):
        _parse_multpl_pe("<html><body><p>no table here</p></body></html>")


# ── live fetch (excluded from the default suite via pytest.ini) ───────────────

@pytest.mark.live_data
def test_live_trailing_pe_fetch_is_current():
    from src.trailing_pe import fetch_trailing_pe_dataframe

    df = fetch_trailing_pe_dataframe()
    assert len(df) > 1500  # monthly since 1871
    last = df.iloc[-1]
    assert 5.0 < float(last["pe"]) < 100.0
    staleness = (pd.Timestamp.today() - pd.Timestamp(last["obs_date"])).days
    assert staleness < 65, f"trailing P/E series is {staleness} days stale"
