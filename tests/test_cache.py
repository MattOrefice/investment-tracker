"""Tests for src/cache.py quarter-snapshot system."""
from datetime import date

import pandas as pd
import pytest

from src.cache import (
    _parse_quarter_end,
    is_quarter_complete,
    label_to_quarter_id,
    snapshot_price_context,
)


def test_parse_quarter_end_q1():
    assert _parse_quarter_end("2026Q1") == date(2026, 3, 31)


def test_parse_quarter_end_q2():
    assert _parse_quarter_end("2026Q2") == date(2026, 6, 30)


def test_parse_quarter_end_q3():
    assert _parse_quarter_end("2025Q3") == date(2025, 9, 30)


def test_parse_quarter_end_q4():
    assert _parse_quarter_end("2025Q4") == date(2025, 12, 31)


def test_parse_quarter_end_invalid():
    assert _parse_quarter_end("not-a-quarter") is None
    assert _parse_quarter_end("2026") is None
    assert _parse_quarter_end("") is None


def test_label_to_quarter_id_standard():
    assert label_to_quarter_id("Q1 2026") == "2026Q1"
    assert label_to_quarter_id("Q4 2025") == "2025Q4"
    assert label_to_quarter_id("Q2 2026") == "2026Q2"


def test_label_to_quarter_id_non_standard():
    assert label_to_quarter_id("Jan 1 to Mar 31, 2026") is None
    assert label_to_quarter_id("Feb 1 to Apr 30, 2026") is None


def test_is_quarter_complete_past():
    # Q1 2026 ended March 31, 2026; today is May 5, 2026
    assert is_quarter_complete("2026Q1") is True


def test_is_quarter_complete_past_q4():
    assert is_quarter_complete("2025Q4") is True


def test_is_quarter_complete_future():
    # Q1 2027 ends March 31, 2027; today is May 5, 2026
    assert is_quarter_complete("2027Q1") is False


def test_is_quarter_complete_invalid():
    assert is_quarter_complete("not-a-quarter") is False


def test_snapshot_price_context_returns_snapshot_values():
    """Values fetched inside context come from snap_df, not the live cache.

    The `close` half of this test used to assert `list(result["close"]) == [100.0,
    102.0]` — i.e. it pinned the aliasing as a contract: a single-basis snapshot
    handing its one adj_close series back under BOTH names. That is #193, so the
    assertion is inverted rather than dropped. The docstring's actual claim is
    proved by the adj_close line, which is unchanged.
    """
    import src.prices as prices_mod

    snap_df = pd.DataFrame(
        {"VOO": [100.0, 102.0]},
        index=[date(2026, 1, 2), date(2026, 1, 3)],
    )

    with snapshot_price_context(snap_df):
        result = prices_mod.get_prices("VOO", "2026-01-02", "2026-01-03")

    assert list(result["adj_close"]) == [100.0, 102.0]
    assert "close" not in result.columns, (
        "a single-basis snapshot served a close column it does not hold — the "
        "figures would be a total-return series under a 'Prices locked' cover"
    )


def test_snapshot_price_context_restores_original():
    """Original get_prices is restored after context exits."""
    import src.prices as prices_mod

    original = prices_mod.get_prices
    snap_df = pd.DataFrame({"VOO": [100.0]}, index=[date(2026, 1, 2)])

    with snapshot_price_context(snap_df):
        assert prices_mod.get_prices is not original

    assert prices_mod.get_prices is original


def test_snapshot_price_context_restores_on_exception():
    """get_prices is restored even when the body raises."""
    import src.prices as prices_mod

    original = prices_mod.get_prices
    snap_df = pd.DataFrame({"VOO": [100.0]}, index=[date(2026, 1, 2)])

    with pytest.raises(RuntimeError):
        with snapshot_price_context(snap_df):
            raise RuntimeError("boom")

    assert prices_mod.get_prices is original


def test_snapshot_price_context_lock_survives_mutation(monkeypatch):
    """Snapshot values are unchanged even when the underlying get_prices is replaced."""
    import src.prices as prices_mod

    snap_df = pd.DataFrame(
        {"VOO": [100.0, 102.0]},
        index=[date(2026, 1, 2), date(2026, 1, 3)],
    )

    def mutated_live(ticker, start, end=None):
        return pd.DataFrame(
            {"close": [999.0, 999.0], "adj_close": [999.0, 999.0]},
            index=[date(2026, 1, 2), date(2026, 1, 3)],
        )

    # Simulate live cache being mutated before entering snapshot context
    monkeypatch.setattr(prices_mod, "get_prices", mutated_live)

    with snapshot_price_context(snap_df):
        result = prices_mod.get_prices("VOO", "2026-01-02", "2026-01-03")

    # Snapshot (100, 102) must win over live cache (999, 999)
    assert list(result["adj_close"]) == [100.0, 102.0]


def test_snapshot_price_context_date_filtering():
    """Only rows within [start_date, end_date] are returned."""
    import src.prices as prices_mod

    snap_df = pd.DataFrame(
        {"VOO": [100.0, 101.0, 102.0, 103.0]},
        index=[date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)],
    )

    with snapshot_price_context(snap_df):
        result = prices_mod.get_prices("VOO", "2026-01-05", "2026-01-06")

    assert list(result["adj_close"]) == [101.0, 102.0]


def test_snapshot_two_consecutive_calls_identical():
    """Two calls to get_prices inside the same context return identical DataFrames."""
    import src.prices as prices_mod

    snap_df = pd.DataFrame(
        {"VOO": [100.0, 102.0], "SPHQ": [50.0, 51.0]},
        index=[date(2026, 1, 2), date(2026, 1, 3)],
    )

    with snapshot_price_context(snap_df):
        r1 = prices_mod.get_prices("VOO", "2026-01-02", "2026-01-03")
        r2 = prices_mod.get_prices("VOO", "2026-01-02", "2026-01-03")

    pd.testing.assert_frame_equal(r1, r2)
