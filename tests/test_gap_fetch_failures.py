"""A failed gap fetch never reads as a quiet old date (#637).

Decided by the owner on October 17, 2026: the rule #368 set for the demo, wherever the
gap fetch runs, and the page stays up. get_prices's two gap fetches used to swallow
every exception, so a down network served the stored prices as if they were all there
were, the ticker's status read served-and-ok, and the banner said only "N trading days
behind". Now they catch only the fetch's own failures (the network, Yahoo's answer, a
read-only store) and record each with its cause, per ticker and window, for the demo's
retry of 30 minutes. A code error raises.

On the frozen book, offline, with the fetcher stubbed: its last close is July 20, 2026,
so a window through July 31 has a trailing gap to fetch.
"""
from __future__ import annotations

import datetime as dt
import sqlite3

import pandas as pd
import pytest
import requests

from tests.conftest import point_at_frozen_book

T0 = dt.datetime(2026, 7, 31, 18, 0, tzinfo=dt.timezone.utc)   # 2:00 PM ET
TODAY = dt.date(2026, 7, 31)
LAST = "2026-07-20"            # the frozen book's last close
START, END = "2026-07-01", "2026-07-31"


@pytest.fixture
def prices(tmp_path, monkeypatch):
    point_at_frozen_book(monkeypatch, tmp_path)
    import src.prices as prices
    monkeypatch.setattr(prices, "_now", lambda: T0)
    return prices


def _fetcher(calls: list, outcome):
    """A stand-in for prices.fetch_prices: raises ``outcome`` if it is an exception,
    else returns it."""
    def fetch(ticker, start, end=None, **_kwargs):
        calls.append((ticker, start, end))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome
    return fetch


def _banner() -> str:
    from src.asof import as_of_live_line
    return as_of_live_line(TODAY, frontier=dt.date(2026, 7, 20))


def test_a_failed_update_is_named_in_the_status_and_the_banner(prices, monkeypatch):
    from src.coverage import FetchFailure, coverage_from_statuses
    from src.holdings import _price_and_status
    monkeypatch.setattr(prices, "fetch_prices",
                        _fetcher([], requests.exceptions.ConnectionError("down")))
    frame, status = _price_and_status("VOO", START, END)
    assert str(max(frame.index)) == LAST, "the page stays up on the stored prices"
    assert status.resolved and status.served_through == LAST
    assert status.fetch_failure == "a network error: ConnectionError"
    cov = coverage_from_statuses([status], END)
    assert cov.fetch_failures == (FetchFailure("VOO", "a network error: ConnectionError"),)
    assert not cov.unresolved, "a stale price is not a missing one"
    assert _banner() == (
        "Prices through July 20, 2026 (settled closes), 8 trading days behind. The price "
        "update failed July 31, 2026 at 2:00 PM ET for 1 ticker (a network error: "
        "ConnectionError); serving the stored prices until it retries after July 31, "
        "2026 at 2:30 PM ET.")


def test_without_a_failure_the_banner_says_only_how_far_behind(prices):
    assert _banner() == "Prices through July 20, 2026 (settled closes), 8 trading days behind."


def test_a_second_read_within_half_an_hour_makes_no_fetch(prices, monkeypatch):
    from src.holdings import _price_and_status
    calls: list = []
    monkeypatch.setattr(prices, "fetch_prices",
                        _fetcher(calls, requests.exceptions.Timeout("slow")))
    prices.get_prices("VOO", START, END)
    prices.get_prices("VTV", START, END)
    assert [c[0] for c in calls] == ["VOO", "VTV"], "one attempt per ticker"
    monkeypatch.setattr(prices, "_now", lambda: T0 + dt.timedelta(minutes=29))
    again = prices.get_prices("VOO", START, END)
    _, status = _price_and_status("VOO", START, END)
    assert len(calls) == 2, "no fetch for a window that failed within the half hour"
    assert str(max(again.index)) == LAST
    assert status.fetch_failure == "a network error: Timeout", "still failed, still said"
    assert "The price update failed July 31, 2026 at 2:00 PM ET for 2 tickers" in _banner()


@pytest.mark.parametrize("error", [KeyError("indicators"), TypeError("NoneType"),
                                   ValueError("not Yahoo's answer")],
                         ids=["KeyError", "TypeError", "ValueError"])
def test_a_code_error_in_a_gap_fetch_raises(prices, monkeypatch, error):
    monkeypatch.setattr(prices, "fetch_prices", _fetcher([], error))
    with pytest.raises(type(error)):
        prices.get_prices("VOO", START, END)
    assert prices.gap_fetch_failures() == []


def test_a_success_after_the_half_hour_clears_the_failure(prices, monkeypatch):
    from src.holdings import _price_and_status
    calls: list = []
    monkeypatch.setattr(prices, "fetch_prices", _fetcher(
        calls, prices.YahooResponseError("Yahoo Finance returned HTTP 503 for 'VOO'.",
                                         "returned HTTP 503")))
    prices.get_prices("VOO", START, END)
    assert prices.gap_fetch_failure_for("VOO", START, END).cause == (
        "Yahoo Finance returned HTTP 503")
    fetched = pd.DataFrame({"close": [500.0], "adj_close": [500.0]}, index=[TODAY])
    monkeypatch.setattr(prices, "fetch_prices", _fetcher(calls, fetched))
    monkeypatch.setattr(prices, "_now", lambda: T0 + dt.timedelta(minutes=31))
    out = prices.get_prices("VOO", START, END)
    assert len(calls) == 2 and str(max(out.index)) == END
    assert prices.gap_fetch_failures() == []
    _, status = _price_and_status("VOO", START, END)
    assert status.fetch_failure is None and status.served_through == END
    assert "update failed" not in _banner()


def test_a_read_only_store_is_a_fetch_failure_with_its_cause(prices, monkeypatch):
    monkeypatch.setattr(prices, "fetch_prices", _fetcher(
        [], sqlite3.OperationalError("attempt to write a readonly database")))
    prices.get_prices("VOO", START, END)
    assert prices.gap_fetch_failure_for("VOO", START, END).cause == (
        "the price store is read-only")
    # Any other store error is not the fetch's own failure.
    monkeypatch.setattr(prices, "fetch_prices", _fetcher(
        [], sqlite3.OperationalError("no such table: prices")))
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        prices.get_prices("AGG", START, END)


def test_a_failed_leading_gap_is_recorded_too(prices, monkeypatch):
    from src.holdings import _price_and_status
    monkeypatch.setattr(prices, "fetch_prices",
                        _fetcher([], requests.exceptions.ConnectionError("down")))
    frame, status = _price_and_status("VOO", "2000-01-03", LAST)
    first = str(min(frame.index))
    assert first > "2000-01-03", "premise: the book's history starts later"
    failed = prices.gap_fetch_failure_for("VOO", "2000-01-03", LAST)
    assert (failed.start, failed.end) == ("2000-01-03", str(min(frame.index)
                                                              - dt.timedelta(days=1)))
    assert status.fetch_failure == "a network error: ConnectionError"


def test_a_failure_outside_the_book_is_not_the_banners(prices, monkeypatch):
    """As for the live mark: Asset Evaluation's BTC-USD is not a ticker the current
    value is priced from."""
    monkeypatch.setattr(prices, "fetch_prices",
                        _fetcher([], requests.exceptions.ConnectionError("down")))
    prices.get_prices("BTC-USD", START, END)
    assert prices.gap_fetch_failures(), "premise: the failure is recorded"
    assert "update failed" not in _banner()


class _Response:
    def __init__(self, status: int, body: "dict | None" = None):
        self.status_code, self._body = status, body or {}

    def json(self):
        return self._body


@pytest.mark.parametrize("response, reason", [
    (_Response(429), "rate-limited the request (HTTP 429)"),
    (_Response(503), "returned HTTP 503"),
    (_Response(200, {"chart": {"result": None, "error": "Not Found"}}), "returned no price data"),
    (_Response(200, {"chart": {"result": [{"timestamp": []}]}}), "returned no price data"),
], ids=["429", "503", "no result", "no timestamps"])
def test_yahoos_response_errors_have_their_own_class(prices, monkeypatch, response, reason):
    """Still ValueErrors, so a caller that catches ValueError still catches them."""
    monkeypatch.setattr(prices._SESSION, "get", lambda *a, **k: response)
    with pytest.raises(prices.YahooResponseError) as caught:
        prices.fetch_prices("VOO", START, END)
    assert isinstance(caught.value, ValueError) and caught.value.reason == reason
    assert prices.gap_fetch_cause(caught.value) == f"Yahoo Finance {reason}"


def test_the_half_hour_is_the_demos_retry(prices, monkeypatch):
    from src.demo_refresh import RETRY_AFTER
    monkeypatch.setattr(prices, "fetch_prices",
                        _fetcher([], requests.exceptions.ConnectionError("down")))
    prices.get_prices("VOO", START, END)
    failed = prices.gap_fetch_failure_for("VOO", START, END)
    assert failed.retry_after - failed.attempted_at == RETRY_AFTER == dt.timedelta(minutes=30)
