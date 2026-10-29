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
# The FROZEN TEST BOOK, not the owner's tracker.db (#302 test half): every scratch
# copy below starts from tests/fixtures/frozen_book.db (public origin, #304 format),
# and today is pinned to the book's next day, so nothing here depends on the real
# book or the date, and the module runs in CI.
FROZEN_BOOK = ROOT / "tests" / "fixtures" / "frozen_book.db"
pytestmark = pytest.mark.usefixtures("frozen_clock_module")
# NOT a literal date. A full-suite run has network, so other tests advance
# tracker.db's price cache mid-run and every scratch copy taken afterwards inherits
# the newer frontier. Hardcoding the date made these tests pass alone and fail in
# the suite — the same environment coupling that bit the PR 1 acceptance test.
# Derive it from the scratch DB instead, by a GROUP BY rather than by asking the
# producer, so the marker is checked against the data and not against itself.
KILL = ("VOO", "VEA")
# The "leading" condition (#639, #643): VOO, held from inception, has no close before
# this date, so it is priced today and unpriced on the days it was held before it.
LEAD = ("VOO", "2025-09-03")

# Every coverage marker, on every page, carries this phrase. One shared token is
# what makes the absence assertion possible: "no marker rendered" has to be
# checkable without enumerating three pages' worth of prose.
MARKER_SIGNATURE = "no committed price"
SUPPRESSED_TILE = "—"


def _pages_dir() -> Path:
    return Path(os.environ.get("COVERAGE_MARKER_PAGES_DIR", ROOT / "pages"))


def _skip_without_personal_inputs():
    # The frozen test book is COMMITTED (#302 test half), so absent is a failure,
    # never a skip. These tests used to copy the owner's tracker.db and skip without
    # the positions CSV and account map, so they never ran in CI.
    assert FROZEN_BOOK.exists(), f"the frozen test book is missing: {FROZEN_BOOK}"


def _render(page: str, mode: str, tmp_path, monkeypatch):
    """Render one page against a scratch DB, network blocked. mode: control|partial|
    leading."""
    import requests

    import src.db
    import src.prices

    db = tmp_path / f"{mode}.db"
    shutil.copyfile(FROZEN_BOOK, db)
    os.chmod(db, 0o644)
    if mode == "partial":
        conn = sqlite3.connect(db)
        conn.execute(
            f"DELETE FROM prices WHERE ticker IN ({','.join('?' * len(KILL))})", KILL)
        conn.commit()
        conn.close()
    elif mode == "leading":
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM prices WHERE ticker = ? AND price_date < ?", LEAD)
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
    shutil.copyfile(FROZEN_BOOK, db)
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


# ── #643: every figure page 2 computes from the value series, withheld ────────
#
# Decided by the owner on October 17, 2026. With a holding unresolved, or one with no
# price on dates it was held (#639), the Performance page withholds every figure
# computed from the portfolio's value series: the summary line, the since-inception
# tiles, the Period Returns table's portfolio column, VaR and CVaR (and the rest of the
# portfolio and relative risk rows), the Stage 2 and Total tiles, the cumulative chart,
# the Brinson-Fachler reconciliation and the daily-vs-Modified-Dietz comparison.
# Benchmark figures still show. The series reader with no record raises a named error,
# and each surface that reads it prints not_computed_sentence with its reason.

def _series_figures(at) -> dict:
    """Every figure page 2 computes from the value series, and the benchmark figures
    beside them, read off the rendered elements."""
    import json
    pairs = [(str(m.label), str(m.value)) for m in at.metric]
    text = _all_text(at)
    table = getattr(at.dataframe[0].value, "data", at.dataframe[0].value)
    charts = [json.loads(c.proto.spec) for c in at.get("plotly_chart")]
    return {
        "pairs": pairs,
        "summary": next(t for t in text if "cumulative TWR" in t),
        "table": table,
        "lines": [tr.get("name") for tr in charts[0].get("data", [])],
        "bf": next(t for t in text if t.startswith("**BF decomposition:**")),
        "text": text,
    }


def _assert_series_withheld(at) -> None:
    f = _series_figures(at)
    vals: dict = {}
    for lbl, v in f["pairs"]:
        vals.setdefault(lbl, []).append(v)
    for lbl in ("vs. S&P 500 (since inception)", "vs. Custom Blended (since inception)",
                "Stage 2: Implementation", "Track. Err", "Info Ratio", "Beta",
                "Active Ret (ann.)"):
        assert vals[lbl] == [SUPPRESSED_TILE], (lbl, vals[lbl])
    assert [v for lbl, v in f["pairs"] if lbl.startswith("YTD return")] == [SUPPRESSED_TILE]
    assert [v for lbl, v in f["pairs"] if lbl.startswith("Total: Portfolio vs.")] == [
        SUPPRESSED_TILE]
    assert vals["Stage 1: SAA Design"] != [SUPPRESSED_TILE], "Stage 1 is two benchmarks"
    # The portfolio row of each risk metric, then the benchmark row.
    for lbl in ("Std Dev (ann.)", "Sharpe", "Sortino", "Max DD", "VaR (95%)", "CVaR (95%)"):
        assert vals[lbl][0] == SUPPRESSED_TILE, (lbl, vals[lbl])
    for lbl in ("Std Dev (ann.)", "Max DD", "VaR (95%)", "CVaR (95%)"):
        assert vals[lbl][1] != SUPPRESSED_TILE, f"the benchmark's {lbl} was withheld too"
    for words in ("cumulative TWR", "vs blended", "vs S&P 500"):
        assert f"**{SUPPRESSED_TILE}** {words}" in f["summary"], f["summary"]
    table = f["table"]
    for col in ("Portfolio", "vs S&P 500", "vs Blended"):
        assert set(table[col]) == {SUPPRESSED_TILE}, (col, list(table[col]))
    for col in ("S&P 500", "Custom Blended"):
        assert all(str(v).endswith("%") for v in table[col]), (col, list(table[col]))
    assert "Portfolio" not in f["lines"]
    assert {"S&P 500 (SPY)", "Custom Blended"} <= set(f["lines"])
    assert f["bf"].endswith("vs. Stage 2 on adjusted levels: withheld"), f["bf"]
    assert ("Reconciliation is withheld with Stage 2 and the total, which are computed "
            "from the portfolio's value.") in f["text"]
    for line in (f"**Daily-linked TWR (SI):** {SUPPRESSED_TILE}",
                 f"**Modified Dietz (SI):**   {SUPPRESSED_TILE}",
                 f"**Method spread:** {SUPPRESSED_TILE}"):
        assert line in f["text"], line


def test_partial_page2_withholds_every_figure_computed_from_the_value_series(
        tmp_path, monkeypatch):
    _skip_without_personal_inputs()
    at, _db = _render("2_Performance.py", "partial", tmp_path, monkeypatch)
    _assert_series_withheld(at)
    warning = next(str(w.value) for w in at.warning if "cannot be computed" in str(w.value))
    assert "counted as zero" not in warning, "this page withholds; it counts nothing as zero"
    assert "Value, returns and portfolio risk metrics are withheld" in warning


def test_a_leading_gap_is_withheld_the_same_way(tmp_path, monkeypatch):
    """VOO priced today and unpriced on the days it was held before September 3, 2025:
    the same figures are withheld, the marker names the holding and the dates, and the
    current value, which counts every holding at today's price, still shows."""
    _skip_without_personal_inputs()
    at, _db = _render("2_Performance.py", "leading", tmp_path, monkeypatch)
    _assert_series_withheld(at)
    err = _unpriced_error()
    (u,) = err.unpriced
    assert (u.ticker, u.first, u.last) == ("VOO", "2025-05-01", "2025-09-02")
    warning = next(str(w.value) for w in at.warning if "cannot be computed" in str(w.value))
    assert (f"VOO has no committed price on {u.sessions} sessions it was held "
            f"(2025-05-01 to 2025-09-02). Returns and portfolio risk metrics are withheld"
            ) in warning
    value = [str(m.value) for m in at.metric if "value" in str(m.label)]
    assert value and value[0].startswith("$"), value


def test_control_page2_still_renders_its_series_figures(tmp_path, monkeypatch):
    """Non-vacuity for the two tests above: withholding always would pass them."""
    _skip_without_personal_inputs()
    at, _db = _render("2_Performance.py", "control", tmp_path, monkeypatch)
    f = _series_figures(at)
    assert f"**{SUPPRESSED_TILE}** cumulative TWR" not in f["summary"]
    assert all(str(v).endswith("%") for v in f["table"]["Portfolio"])
    assert "Portfolio" in f["lines"]
    assert "vs. Stage 2 on adjusted levels: withheld" not in f["bf"]
    assert dict(f["pairs"])["Stage 2: Implementation"] != SUPPRESSED_TILE


def _unpriced_error():
    """The named error the value series raises on the rendered scratch book."""
    from datetime import date
    from src.holdings import (UnpricedHoldingError, get_inception_date,
                              get_portfolio_account_id, get_portfolio_value_series)
    acct = get_portfolio_account_id()
    with pytest.raises(UnpricedHoldingError) as err:
        get_portfolio_value_series(get_inception_date(account_id=acct),
                                   date.today().isoformat(), account_id=acct)
    return err.value


def test_the_named_error_reaches_the_risk_pages_sentence(tmp_path, monkeypatch):
    _skip_without_personal_inputs()
    from src.reports import not_computed_sentence
    at, _db = _render("7_Risk.py", "leading", tmp_path, monkeypatch)
    err = _unpriced_error()
    want = not_computed_sentence(
        "The portfolio factor decomposition", err,
        "no factor betas, fit statistics or scenario stress results are shown",
        reason=err.reason, where="this page")
    assert want in [str(i.value) for i in at.info]
    assert "could not be computed for this page (VOO has no price on" in want


def test_the_named_error_reaches_the_benchmark_attribution_pages_sentence(
        tmp_path, monkeypatch):
    _skip_without_personal_inputs()
    from src.reports import not_computed_sentence
    at, _db = _render("6_Benchmark_Attribution.py", "leading", tmp_path, monkeypatch)
    err = _unpriced_error()
    want = not_computed_sentence(
        "The benchmark attribution regression", err,
        "no alpha, loadings or fit statistics are shown",
        reason=err.reason, where="this page")
    assert want in [str(i.value) for i in at.info]
    assert not [m for m in at.metric if str(m.label) == "R²"], "no fit statistics"


def test_the_named_error_reaches_the_pdfs_sentences(tmp_path, monkeypatch):
    """A custom-range report, which reads live: the executive summary, the performance
    section and the benchmark attribution section each print the sentence in place of
    their figures."""
    _skip_without_personal_inputs()
    import markupsafe
    from src import reports
    from src.reports import not_computed_sentence
    _render("2_Performance.py", "leading", tmp_path, monkeypatch)
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    monkeypatch.setattr(reports, "_render_pdf", lambda html: html.encode("utf-8"))
    err = _unpriced_error()
    start, end = "2026-05-15", "2026-07-15"
    html = reports.generate_quarterly_report_bytes(start, end, is_demo=True).decode("utf-8")
    period = reports._format_period_label(start, end)
    for subject, consequence in (
            (f"The portfolio's return in {period}",
             "this summary states no portfolio return and no comparison with a benchmark"),
            ("The portfolio's period returns and cumulative return",
             "no period return table or cumulative return chart is shown"),
            ("The benchmark attribution regression",
             "no alpha, loadings or fit statistics are shown")):
        want = not_computed_sentence(subject, err, consequence, reason=err.reason)
        assert str(markupsafe.escape(want)) in html, subject
    assert "Period Returns (Daily-Linked TWR)" not in html
