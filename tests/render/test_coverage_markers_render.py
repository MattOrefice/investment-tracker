"""PR 2 — the three render boundaries consume the coverage record.

Written BEFORE the implementation. The acceptance case is the C1 repro: with two
holdings unpriced, each page must be VISUALLY DISTINGUISHABLE from a correct
render. That has two halves and both must be able to fail:

  partial  a marker MUST render, naming the holdings and the vintage served
  control  NO marker may render — asserted as ELEMENT ABSENCE, not as a digest
           match. A hash comparison passes when a marker renders as an empty
           string, which is exactly the leak this half exists to catch.

The control half is the one that matters most. In the control condition every
holding resolves, so a marker there means the disclosure is keyed on the page
rather than on the gap — a page that cries wolf on healthy data teaches the reader
to ignore it, and then the partial-condition marker is worthless too.

These are personal-mode tests: they need the positions CSV, tracker.db and
private/account_map.json, and skip without them (so they skip in CI, which runs
demo). Every render goes through a scratch copy of the DB with the network blocked
at the SESSION layer — socket patching alone does not hold once anything in the
process has opened a pooled connection.

COVERAGE_MARKER_PAGES_DIR exists so the mutation harness can render a deliberately
broken COPY of a page (one that always marks, or never marks) without modifying
anything under pages/. Nothing in the product reads it.
"""
import os
import shutil
import socket
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
TRACKER_DB = ROOT / "data" / "tracker.db"
# NOT a literal date. A full-suite run has network, so other tests advance
# tracker.db's price cache mid-run and every scratch copy taken afterwards inherits
# the newer frontier. Hardcoding the date made these tests pass alone and fail in
# the suite — the same environment coupling that bit the PR 1 acceptance test.
# Derive it from the scratch DB instead, by a GROUP BY rather than by asking the
# producer, so the marker is checked against the data and not against itself.
KILL = ("VOO", "VEA")

# Every coverage marker, on every page, carries this phrase. One shared token is
# what makes the absence assertion possible: "no marker rendered" has to be
# checkable without enumerating three pages' worth of prose.
MARKER_SIGNATURE = "no committed price"
SUPPRESSED_TILE = "—"


def _pages_dir() -> Path:
    return Path(os.environ.get("COVERAGE_MARKER_PAGES_DIR", ROOT / "pages"))


def _skip_without_personal_inputs():
    from src.household_data import find_latest_positions_csv
    if (find_latest_positions_csv() is None or not TRACKER_DB.exists()
            or not (ROOT / "private" / "account_map.json").exists()):
        pytest.skip("personal-mode inputs absent")


def _render(page: str, mode: str, tmp_path, monkeypatch):
    """Render one page against a scratch DB, network blocked. mode: control|partial."""
    import requests

    import src.db
    import src.prices

    db = tmp_path / f"{mode}.db"
    shutil.copyfile(TRACKER_DB, db)
    os.chmod(db, 0o644)
    if mode == "partial":
        conn = sqlite3.connect(db)
        conn.execute(
            f"DELETE FROM prices WHERE ticker IN ({','.join('?' * len(KILL))})", KILL)
        conn.commit()
        conn.close()

    def _dns(*_a, **_k):
        raise socket.gaierror("test harness: DNS blocked")

    def _no_http(*_a, **_k):
        raise requests.ConnectionError("test harness: HTTP blocked")

    monkeypatch.setattr(socket, "getaddrinfo", _dns)
    monkeypatch.setattr(src.prices._SESSION, "get", _no_http)
    monkeypatch.setattr(src.db, "DB_PATH", db)
    monkeypatch.setattr(src.db, "_migrated_paths", set())
    src.prices._reset_trailing_memo()

    # st.cache_data persists ACROSS AppTest instances in one process, and pages 2
    # and 11 load through cached functions keyed only on the as-of date — not on
    # which database is behind it. Without this, the partial renders (first in file
    # order) seed the cache and the control renders read partial data back, so the
    # control half fails with markers on a fully-priced book. Page 1 has no cached
    # loader, which is exactly why it was the only one unaffected.
    import streamlit as st
    st.cache_data.clear()
    st.cache_resource.clear()

    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_pages_dir() / page), default_timeout=300).run()
    assert not at.exception, f"{page} raised in {mode}: {at.exception}"
    return at, db


def _served_frontier(db, as_of: str) -> str:
    """MIN over the still-priced holdings of each one's MAX(price_date) <= as_of."""
    conn = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    row = conn.execute(
        "SELECT MIN(mx) FROM (SELECT MAX(price_date) AS mx FROM prices "
        "WHERE ticker IN (SELECT DISTINCT ticker FROM trades) AND price_date <= ? "
        "GROUP BY ticker)", (as_of,)).fetchone()
    conn.close()
    return row[0]


def _all_text(at) -> list:
    """Every text-bearing element on the page.

    AN ABSENCE ASSERTION IS ONLY AS BROAD AS ITS COLLECTOR. Every "no marker
    rendered" check in this file is really "no marker rendered IN THE ELEMENT
    TYPES THIS FUNCTION COLLECTS". Add a page element of a type missing from this
    list and every absence check in the suite silently narrows — it keeps passing
    while checking less, which is the same quiet-failure shape the markers exist
    to remove. Anything that can carry a verdict or a marker belongs here.

    That is not hypothetical: `success` was missing from the first version, page 11
    renders its band verdict through st.success on a healthy book (st.warning only
    when something breaches), and the resulting failure read as a product bug for
    long enough to be investigated as one. It had also passed once, earlier, which
    is how quietly this fails.
    """
    out = []
    for kind in ("warning", "error", "info", "success", "caption", "markdown",
                 "subheader", "title", "header"):
        out.extend(str(el.value) for el in getattr(at, kind, []))
    return out


def _markers(at) -> list:
    return [t for t in _all_text(at) if MARKER_SIGNATURE in t]


# ── partial: the marker must render, and say enough to act on ─────────────────

@pytest.mark.parametrize("page", [
    "1_SAA.py", "2_Performance.py", "11_Capital_Deployment.py"])
def test_partial_renders_a_marker_naming_the_holdings_and_the_vintage(
        page, tmp_path, monkeypatch):
    _skip_without_personal_inputs()
    at, db = _render(page, "partial", tmp_path, monkeypatch)
    marks = _markers(at)
    assert marks, f"{page} rendered no coverage marker with two holdings unpriced"
    blob = " ".join(marks)
    for ticker in KILL:
        assert ticker in blob, f"{page} marker does not name {ticker}: {blob!r}"
    from datetime import date
    expected = _served_frontier(db, date.today().isoformat())
    assert expected and expected in blob, (
        f"{page} marker does not state the vintage served ({expected}) — a reader "
        f"told WHICH holdings failed asks AS OF WHEN in the same breath: {blob!r}"
    )


def test_partial_page1_withdraws_the_band_verdict(tmp_path, monkeypatch):
    """Band status is computed from weights that counted two holdings as zero, so
    neither "in band" nor "out of band" is a claim the data supports."""
    _skip_without_personal_inputs()
    blob = " ".join(_all_text(_render("1_SAA.py", "partial", tmp_path, monkeypatch)[0]))
    assert "within their tolerance" not in blob
    assert "sleeves out of band" not in blob


def test_partial_page2_suppresses_the_figures_it_cannot_compute(tmp_path, monkeypatch):
    """A return over a book missing two holdings is not a degraded measurement of
    this portfolio — it is an accurate measurement of a different one. "-59.97%,
    but incomplete" has no referent; "—, not priced" does."""
    _skip_without_personal_inputs()
    at, _db = _render("2_Performance.py", "partial", tmp_path, monkeypatch)
    # A LIST, not a dict: the portfolio and benchmark risk rows use identical
    # labels ("Sharpe", "Max DD", "Std Dev (ann.)"), so collapsing them into a
    # dict loses one of each and would silently assert against the wrong row.
    pairs = [(str(m.label), str(m.value)) for m in at.metric]

    for frag in ("value", "YTD return"):
        vals = [v for lbl, v in pairs if frag in lbl]
        assert vals, f"no tile matching {frag!r} found"
        assert all(v == SUPPRESSED_TILE for v in vals), (
            f"{frag!r} tile still renders {vals} over an incomplete book")

    # The DELTA is a second field on the same tile and was carrying the fabricated
    # figure after the value was suppressed — "— (-59.97% SI cumulative)". Caught by
    # an element-level digest diff, which a hash comparison would have hidden.
    deltas = [(str(m.label), str(m.delta)) for m in at.metric
              if any(f in str(m.label) for f in ("value", "YTD return"))]
    for lbl, delta in deltas:
        assert "%" not in delta, (
            f"{lbl!r} suppressed its value but its delta still asserts {delta!r}")

    # Portfolio row suppressed; BENCHMARK row must survive — a benchmark series is
    # unaffected by a portfolio holding that would not price, and suppressing it
    # would be its own fabrication (claiming we cannot compute something we can).
    for lbl in ("Std Dev (ann.)", "Sharpe", "Max DD"):
        vals = [v for l, v in pairs if l == lbl]
        if not vals:
            continue
        assert SUPPRESSED_TILE in vals, f"portfolio {lbl} not suppressed: {vals}"
        assert any(v != SUPPRESSED_TILE for v in vals), (
            f"benchmark {lbl} was suppressed too: {vals}")


def test_partial_page11_refuses_to_prescribe(tmp_path, monkeypatch):
    """The #188 buy instruction: two failed fetches produced "directing new
    contributions to" two sleeves whose prices merely did not resolve."""
    _skip_without_personal_inputs()
    blob = " ".join(_all_text(_render("11_Capital_Deployment.py", "partial",
                                      tmp_path, monkeypatch)[0]))
    assert "directing new contributions" not in blob
    assert "sleeves out of band" not in blob


# ── control: absence, asserted directly ───────────────────────────────────────

@pytest.mark.parametrize("page", [
    "1_SAA.py", "2_Performance.py", "11_Capital_Deployment.py"])
def test_control_renders_no_marker_at_all(page, tmp_path, monkeypatch):
    """Element ABSENCE, not hash equality. Every holding resolves here, so any
    marker is keyed on the page rather than on the gap."""
    _skip_without_personal_inputs()
    at, _db = _render(page, "control", tmp_path, monkeypatch)
    assert _markers(at) == [], (
        f"{page} rendered a coverage marker with every holding priced: {_markers(at)}")
    for ticker in KILL:
        assert not [t for t in _all_text(at)
                    if ticker in t and MARKER_SIGNATURE in t]


def test_control_page2_still_renders_its_figures(tmp_path, monkeypatch):
    """Non-vacuity for the suppression test: suppressing always would pass it."""
    _skip_without_personal_inputs()
    at, _db = _render("2_Performance.py", "control", tmp_path, monkeypatch)
    pairs = [(str(m.label), str(m.value)) for m in at.metric]
    for frag in ("value", "YTD return"):
        vals = [v for lbl, v in pairs if frag in lbl]
        assert vals, f"no tile matching {frag!r} found"
        assert all(v != SUPPRESSED_TILE for v in vals), (
            f"{frag!r} tile is suppressed on a fully-priced book: {vals}")


def test_control_page11_still_assesses_bands(tmp_path, monkeypatch):
    """Non-vacuity: the band verdict must survive on a complete book."""
    _skip_without_personal_inputs()
    blob = " ".join(_all_text(_render("11_Capital_Deployment.py", "control",
                                      tmp_path, monkeypatch)[0]))
    assert ("within their tolerance" in blob) or ("sleeves out of band" in blob), (
        "page 11 withdrew its band verdict on a fully-priced book")


# ── the three producers must agree about the same ten holdings ────────────────

def test_page2_three_producers_report_the_same_unresolved_set(tmp_path, monkeypatch):
    """Page 2 reads three producers over one account. Three records disagreeing
    about which holdings are missing is a real failure mode and a quiet one — the
    page would mark on one and compute on another."""
    _skip_without_personal_inputs()
    import src.db
    import src.prices
    import requests

    db = tmp_path / "agree.db"
    shutil.copyfile(TRACKER_DB, db)
    os.chmod(db, 0o644)
    conn = sqlite3.connect(db)
    conn.execute(f"DELETE FROM prices WHERE ticker IN ({','.join('?' * len(KILL))})", KILL)
    conn.commit()
    conn.close()

    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: (_ for _ in ()).throw(socket.gaierror("blocked")))
    monkeypatch.setattr(src.prices._SESSION, "get",
                        lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("blocked")))
    monkeypatch.setattr(src.db, "DB_PATH", db)
    monkeypatch.setattr(src.db, "_migrated_paths", set())
    src.prices._reset_trailing_memo()

    from src.holdings import (current_market_value_with_coverage,
                              get_portfolio_account_id, get_inception_date,
                              portfolio_value_series_with_coverage,
                              sleeve_weights_with_coverage)
    as_of = "2026-08-13"
    acct = get_portfolio_account_id()
    _f, sleeves = sleeve_weights_with_coverage(as_of)
    _v, mv = current_market_value_with_coverage(as_of)
    _s, series = portfolio_value_series_with_coverage(
        get_inception_date(account_id=acct), as_of, account_id=acct)

    assert sleeves.unresolved_tickers() == mv.unresolved_tickers() == \
        series.unresolved_tickers() == frozenset(KILL), (
        f"producers disagree — sleeves={sleeves.unresolved_tickers()} "
        f"market_value={mv.unresolved_tickers()} series={series.unresolved_tickers()}")
