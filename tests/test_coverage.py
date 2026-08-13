"""Price-coverage substrate (PR 1) — tests written BEFORE the implementation.

Two independent proofs live here, and each is built to fail for a *specific*
reason rather than merely to fail.

1. INVARIANT PROOF (pure, runs in CI). A PriceCoverage record must be INCAPABLE
   of representing a silent gap. Every invariant is proved twice: once by
   constructing a violating record and requiring a raise, and once by a
   non-vacuity case that an implementation hardcoding the safe answer cannot
   pass. Without the second half, `is_complete = False` forever would be green.

2. ACCEPTANCE PROOF (personal-mode; skips where the CSV + tracker.db are absent).
   Against a scratch copy of tracker.db with two tickers' price rows deleted and
   the network blocked in-process, the record must name exactly those two — and
   the CONTROL run must report complete coverage, so a producer that always
   reports a miss fails just as loudly as one that never does.

The frontier assertion derives its right-hand side by SQL over the scratch DB,
never from the producer's own expression: the producer builds the frontier by
aggregating per-ticker statuses, the test builds it with a GROUP BY. If the
producer's aggregation is wrong, the two disagree.

Nothing here touches data/tracker.db or data/demo.db. Scratch copies go to
pytest's tmp_path via shutil.copyfile + an explicit os.chmod — never
shutil.copy, which would carry a read-only source bit onto the copy (#173).
"""
import os
import shutil
import socket
import sqlite3
import sys
from pathlib import Path

import pytest

from src.coverage import (
    CoverageInvariantError,
    DroppedBar,
    PriceCoverage,
    Substitution,
    Unresolved,
)

ROOT = Path(__file__).resolve().parent.parent
TRACKER_DB = ROOT / "data" / "tracker.db"
AS_OF = "2026-08-13"
KILL = ("VOO", "VEA")


# ── helpers ────────────────────────────────────────────────────────────────────

def _cov(**over):
    """A VALID record, with one field overridable per test. Every invariant test
    changes exactly one thing, so a failure names the invariant it broke."""
    base = dict(
        requested=("VOO", "VEA"),
        resolved=("VOO", "VEA"),
        unresolved=(),
        as_of_requested=AS_OF,
        frontier_served="2026-08-10",
        dropped_bars=(),
        substitutions=(),
    )
    base.update(over)
    return PriceCoverage(**base)


# ── invariant 1: the record cannot hide a ticker ───────────────────────────────

def test_requested_ticker_that_is_neither_resolved_nor_unresolved_is_rejected():
    """The defect this substrate exists to prevent, expressed as a type error: a
    ticker asked for and then never accounted for."""
    with pytest.raises(CoverageInvariantError):
        _cov(requested=("VOO", "VEA", "PDBC"), resolved=("VOO", "VEA"))


def test_resolved_ticker_absent_from_requested_is_rejected():
    with pytest.raises(CoverageInvariantError):
        _cov(requested=("VOO",), resolved=("VOO", "VEA"))


def test_unresolved_ticker_absent_from_requested_is_rejected():
    with pytest.raises(CoverageInvariantError):
        _cov(
            requested=("VOO",),
            resolved=("VOO",),
            unresolved=(Unresolved("VEA", "no_cached_rows"),),
        )


def test_a_valid_record_constructs():
    """Non-vacuity for the three rejections above: an implementation that raised
    on every construction would otherwise pass all of them."""
    cov = _cov()
    assert cov.requested == ("VOO", "VEA")


def test_unknown_unresolved_reason_is_rejected():
    """The reason code selects the downstream behavior — a delisted holding is a
    legitimate degradation, a failed fetch is #188 — so a typo must not become a
    silently unhandled category."""
    with pytest.raises(CoverageInvariantError):
        _cov(
            requested=("VOO", "VEA"),
            resolved=("VOO",),
            unresolved=(Unresolved("VEA", "netwrok_down"),),
        )


def test_every_documented_reason_is_accepted():
    """Non-vacuity for the reason vocabulary."""
    for reason in ("no_cached_rows", "fetch_failed", "empty_window",
                   "not_in_snapshot", "delisted"):
        cov = _cov(
            requested=("VOO", "VEA"),
            resolved=("VOO",),
            unresolved=(Unresolved("VEA", reason),),
        )
        assert cov.unresolved[0].reason == reason


# ── invariants 2 and 3: is_complete is a strict conjunction ────────────────────

def test_is_complete_false_when_a_ticker_is_unresolved():
    cov = _cov(
        requested=("VOO", "VEA"),
        resolved=("VOO",),
        unresolved=(Unresolved("VEA", "no_cached_rows"),),
    )
    assert cov.is_complete is False


def test_is_complete_false_when_a_bar_was_dropped():
    cov = _cov(dropped_bars=(DroppedBar("VOO", "2026-08-07", "null_close"),))
    assert cov.is_complete is False


def test_is_complete_false_when_a_substitution_is_live():
    """A resolved price on the wrong basis is not coverage. SPAXX priced off BIL
    and the locked snapshot serving adj_close in the close column (#193) are both
    RESOLVED — a requested/resolved/unresolved triple would report complete while
    the PDF's Market Value column is on the wrong basis. A record that can report
    complete through a live substitution is a false disclosure, which is worse
    than no disclosure."""
    cov = _cov(substitutions=(
        Substitution("SPAXX", "proxy_instrument", "priced off BIL"),))
    assert cov.is_complete is False


def test_is_complete_true_only_when_all_three_are_empty():
    """The non-vacuity guard for the three above. `is_complete = False` as a
    constant would pass every failure case and fail only here."""
    assert _cov().is_complete is True


def test_each_of_the_three_triggers_flips_is_complete_independently():
    """No trigger may rely on another being present."""
    assert _cov().is_complete is True
    assert _cov(requested=("VOO", "VEA"), resolved=("VOO",),
                unresolved=(Unresolved("VEA", "fetch_failed"),)).is_complete is False
    assert _cov(dropped_bars=(DroppedBar("VOO", "2026-08-07", "null_close"),)
                ).is_complete is False
    assert _cov(substitutions=(Substitution("VOO", "price_basis", "adj_close"),)
                ).is_complete is False


# ── vintage is separate from coverage ──────────────────────────────────────────

def test_stale_days_is_the_gap_between_the_request_and_what_was_served():
    """Coverage and vintage are different questions: a complete record can still
    be stale — every ticker resolved, but served from an earlier date because the
    trailing-gap fetch could not run. That is why #189 needs a staleness
    disclosure rather than a coverage marker. Fixed inputs, so this pins the
    arithmetic; whether any given machine's cache is behind is not a contract."""
    cov = _cov(as_of_requested="2026-08-13", frontier_served="2026-08-10")
    assert cov.stale_days == 3
    assert cov.is_complete is True


def test_stale_days_is_none_when_nothing_was_served():
    assert _cov(frontier_served=None).stale_days is None


def test_unresolved_tickers_is_a_set_of_names():
    cov = _cov(
        requested=("VOO", "VEA"),
        resolved=(),
        unresolved=(Unresolved("VOO", "no_cached_rows"),
                    Unresolved("VEA", "no_cached_rows")),
    )
    assert cov.unresolved_tickers() == frozenset({"VOO", "VEA"})


# ── acceptance proof, against real frames ──────────────────────────────────────

def _skip_without_personal_inputs():
    from src.household_data import find_latest_positions_csv
    if (find_latest_positions_csv() is None or not TRACKER_DB.exists()
            or TRACKER_DB.stat().st_size == 0):
        pytest.skip("personal-mode inputs absent")


def _scratch(tmp_path, kill=()):
    """A writable copy of tracker.db with `kill`'s price rows deleted."""
    dst = tmp_path / f"scratch_{'_'.join(kill) or 'control'}.db"
    shutil.copyfile(TRACKER_DB, dst)
    os.chmod(dst, 0o644)
    if kill:
        conn = sqlite3.connect(dst)
        marks = ",".join("?" * len(kill))
        conn.execute(f"DELETE FROM prices WHERE ticker IN ({marks})", kill)
        conn.commit()
        conn.close()
    return dst


def _block_network(monkeypatch):
    """Make every outbound price fetch fail, whatever else the suite has done.

    Patching sockets alone is NOT sufficient in a full-suite run: src.prices holds
    a module-level requests.Session, so an earlier test that made a real request
    leaves a live pooled connection, and reusing an already-open socket consults
    neither getaddrinfo nor create_connection. These tests then quietly re-fetched
    the very prices they had deleted and reported full coverage. The session patch
    is the one that actually holds; the socket patches stay as defence in depth for
    any other client.
    """
    import requests

    import src.prices

    def _dns(*_a, **_k):
        raise socket.gaierror("test harness: DNS blocked")

    def _conn(*_a, **_k):
        raise OSError("test harness: connect blocked")

    def _no_http(*_a, **_k):
        raise requests.ConnectionError("test harness: HTTP blocked")

    monkeypatch.setattr(socket, "getaddrinfo", _dns)
    monkeypatch.setattr(socket, "create_connection", _conn)
    monkeypatch.setattr(src.prices._SESSION, "get", _no_http)


def _point_at(monkeypatch, db):
    import src.db
    import src.prices
    monkeypatch.setattr(src.db, "DB_PATH", db)
    monkeypatch.setattr(src.db, "_migrated_paths", set())
    src.prices._reset_trailing_memo()


def _expected_frontier(db, resolved, as_of):
    """MIN over the resolved tickers of each one's MAX(price_date) at or before
    as_of, derived here by GROUP BY — deliberately a different algorithm from the
    producer's per-ticker-status aggregation."""
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    marks = ",".join("?" * len(resolved))
    row = conn.execute(
        f"SELECT MIN(mx) FROM (SELECT MAX(price_date) AS mx FROM prices "
        f"WHERE ticker IN ({marks}) AND price_date <= ? GROUP BY ticker)",
        (*resolved, as_of),
    ).fetchone()
    conn.close()
    return row[0]


def test_partial_condition_names_exactly_the_unpriced_tickers(tmp_path, monkeypatch):
    """The headline acceptance case. Two tickers' prices deleted, network down:
    the record must name VOO and VEA with reason no_cached_rows, report the
    frontier of what it did serve, and report itself incomplete."""
    _skip_without_personal_inputs()
    from src.holdings import sleeve_weights_with_coverage
    db = _scratch(tmp_path, KILL)
    _block_network(monkeypatch)
    _point_at(monkeypatch, db)

    frame, cov = sleeve_weights_with_coverage(AS_OF)

    assert cov.unresolved_tickers() == frozenset(KILL), (
        f"expected exactly {set(KILL)} unresolved, got {cov.unresolved_tickers()}"
    )
    assert {u.reason for u in cov.unresolved} == {"no_cached_rows"}
    assert cov.is_complete is False
    assert set(KILL).isdisjoint(cov.resolved)
    assert set(cov.requested) >= set(KILL), "an unpriced ticker must still be REQUESTED"
    assert cov.frontier_served == _expected_frontier(db, cov.resolved, AS_OF)
    assert not frame.empty, "the frame still renders; only the disclosure changes"


def test_control_condition_reports_complete_coverage(tmp_path, monkeypatch):
    """Non-vacuity for the case above: a producer that always reports a miss must
    fail here. Complete but STALE is the expected shape today — every ticker
    resolves, and the frontier sits behind the requested date because the
    trailing-gap fetch cannot reach the network."""
    _skip_without_personal_inputs()
    from src.holdings import sleeve_weights_with_coverage
    db = _scratch(tmp_path)
    _block_network(monkeypatch)
    _point_at(monkeypatch, db)

    _frame, cov = sleeve_weights_with_coverage(AS_OF)

    assert cov.unresolved == (), f"control must resolve every ticker: {cov.unresolved}"
    assert cov.is_complete is True
    assert cov.frontier_served == _expected_frontier(db, cov.resolved, AS_OF)
    # stale_days must be DERIVED from the two dates, not assumed positive: whether
    # this machine's cache happens to be behind is an environment property, and an
    # earlier version of this assertion encoded it as a contract and broke the
    # moment the suite's own network run brought the cache up to date.
    from datetime import date as _date
    assert cov.stale_days == (_date.fromisoformat(AS_OF)
                              - _date.fromisoformat(cov.frontier_served)).days
    assert cov.stale_days >= 0


def test_current_market_value_reports_the_same_gap(tmp_path, monkeypatch):
    """The float-returning producer had nowhere to carry a frame attribute even in
    principle, and it drives the Performance page header and the Tax Lots total —
    so its coverage must name the same holdings as the sleeve frame's."""
    _skip_without_personal_inputs()
    from src.holdings import current_market_value_with_coverage
    db = _scratch(tmp_path, KILL)
    _block_network(monkeypatch)
    _point_at(monkeypatch, db)

    value, cov = current_market_value_with_coverage(AS_OF)

    assert cov.unresolved_tickers() == frozenset(KILL)
    assert cov.is_complete is False
    assert value > 0, "the figure still renders; only the disclosure changes"


def test_value_series_reports_the_gap_that_skipna_hides(tmp_path, monkeypatch):
    """The hardest gap to see: an unpriceable holding becomes an all-NaN price
    column, and .sum(axis=1) defaults to skipna=True, so it contributes exactly
    zero dollars to every day with no NaN surfacing. The coverage record is the
    only signal that the series is short two holdings."""
    _skip_without_personal_inputs()
    from src.holdings import get_portfolio_account_id, portfolio_value_series_with_coverage
    db = _scratch(tmp_path, KILL)
    _block_network(monkeypatch)
    _point_at(monkeypatch, db)

    series, cov = portfolio_value_series_with_coverage(
        "2026-06-09", AS_OF, account_id=get_portfolio_account_id())

    assert cov.unresolved_tickers() == frozenset(KILL)
    assert cov.is_complete is False
    assert not series.empty and float(series.iloc[-1]) > 0, (
        "the series still has values — every one of them understated, with no NaN"
    )


def test_the_two_conditions_differ(tmp_path, monkeypatch):
    """A producer returning a constant record would pass both tests above only if
    they never compare. They do."""
    _skip_without_personal_inputs()
    from src.holdings import sleeve_weights_with_coverage

    _block_network(monkeypatch)
    _point_at(monkeypatch, _scratch(tmp_path))
    _f1, control = sleeve_weights_with_coverage(AS_OF)

    _point_at(monkeypatch, _scratch(tmp_path, KILL))
    _f2, partial = sleeve_weights_with_coverage(AS_OF)

    assert control.is_complete is not partial.is_complete
    assert control.unresolved_tickers() != partial.unresolved_tickers()
    assert len(control.resolved) == len(partial.resolved) + len(KILL)
