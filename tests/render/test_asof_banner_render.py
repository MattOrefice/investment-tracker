"""PR 3 — the banner is the ONLY thing that changes on any page.

PR 2 could lean on "the three control digests hold" as its guard: markers were
supposed to appear in one condition and not the other, so an unchanged control
render proved the disclosure was keyed on the gap. PR 3 has no such check —
the banner renders on every page in every condition, and today's state is 2 rather
than 1, so all six digests move. This file is the replacement guard.

It compares each page against ITSELF with the legacy clock-read banner patched
back in, and requires every differing element to be a banner element. No stored
baseline, no diff to read by eye, and it fails rather than reports.
"""
import os
import shutil
import socket
import sqlite3
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
TRACKER_DB = ROOT / "data" / "tracker.db"
KILL = ("VOO", "VEA")

# Any element carrying one of these is a banner element. The legacy render emits
# the first; the new one emits one of the rest.
BANNER_TOKENS = ("Live data as of", "Prices through", "No committed price data")

# Pages that render an as-of banner: the two that can now supply a coverage record,
# and one that cannot — the nine-page case must be covered too, since that is where
# a coverage claim would be fabricated.
PAGES = ["1_SAA.py", "2_Performance.py", "8_Research.py"]


# Pages whose banner is fed by a PriceCoverage record vs by the committed frontier.
# The two answer different questions and disagree by a day whenever today has a bar
# (see #265), which is why state 1 has to be derived per page rather than assumed.
_COVERAGE_PAGES = {"1_SAA.py", "2_Performance.py"}


def _in_state_one(page: str, db, today: date) -> bool:
    """Is this page's banner in as_of_live_line's state 1 — fully current AND
    complete — against `db`?

    State 1 renders the legacy sentence UNCHANGED, by design (#189, and the
    docstring at src/asof.py:299-309). So it is the one state where the legacy and
    current renders must be IDENTICAL, and asserting they differ there asserts
    something false. That was #241: `assert changed` failed whenever the cache was
    fully current, which is what a user with a fresh cache actually sees.

    Derived from the same source each page uses, never from a pinned date:

      1_SAA / 2_Performance  pass `coverage=`, so their frontier is
                             coverage.frontier_served, which INCLUDES today
                             (src/coverage.py:128-141 — excluding it would blank
                             the disclosure exactly when coverage is best).
      8_Research             calls as_of_banner() bare, so its frontier is
                             committed_price_frontier(), which excludes today by
                             construction (src/holdings.py:224-233) so a partial
                             mid-session bar cannot pose as settled.

    The consequence, and the reason this is a real asymmetry rather than a detail:
    a page without a coverage record can NEVER reach state 1. Pinning a past date
    in the fixture would hide that; deriving it encodes it.

    THE NETWORK MUST BE BLOCKED HERE, exactly as it is for the render. Without it
    this helper fetches, resolves the very tickers the render cannot, advances the
    scratch cache to today, and returns True for a book the page will render as
    stale — picking the wrong branch in every condition. Blocking at the SESSION
    layer as well as at socket: a pooled connection bypasses socket patching.
    """
    import requests
    import streamlit as st

    import src.db
    import src.prices

    saved = (src.db.DB_PATH, src.db._migrated_paths,
             socket.getaddrinfo, src.prices._SESSION.get)
    src.db.DB_PATH, src.db._migrated_paths = db, set()
    socket.getaddrinfo = lambda *a, **k: (_ for _ in ()).throw(
        socket.gaierror("blocked"))
    src.prices._SESSION.get = lambda *a, **k: (_ for _ in ()).throw(
        requests.ConnectionError("blocked"))
    src.prices._reset_trailing_memo()
    st.cache_data.clear()
    try:
        if page in _COVERAGE_PAGES:
            from src.holdings import sleeve_weights_with_coverage
            _frame, cov = sleeve_weights_with_coverage(today.isoformat())
            return cov.frontier_served == today.isoformat() and not cov.unresolved
        from src.holdings import committed_price_frontier
        return committed_price_frontier(today) == today.isoformat()
    finally:
        (src.db.DB_PATH, src.db._migrated_paths,
         socket.getaddrinfo, src.prices._SESSION.get) = saved
        src.prices._reset_trailing_memo()
        st.cache_data.clear()


def _skip_without_personal_inputs():
    from src.household_data import find_latest_positions_csv
    if (find_latest_positions_csv() is None or not TRACKER_DB.exists()
            or not (ROOT / "private" / "account_map.json").exists()):
        pytest.skip("personal-mode inputs absent")


def _scratch(tmp_path, mode):
    db = tmp_path / f"{mode}.db"
    shutil.copyfile(TRACKER_DB, db)
    os.chmod(db, 0o644)
    if mode == "partial":
        conn = sqlite3.connect(db)
        conn.execute(
            f"DELETE FROM prices WHERE ticker IN ({','.join('?' * len(KILL))})", KILL)
        conn.commit()
        conn.close()
    elif mode == "current":
        # Carry every ticker's latest bar forward to today, so the cache is fully
        # current and the coverage pages land in state 1 DETERMINISTICALLY.
        #
        # Without this the state-1 branch is only reached on a machine whose cache
        # happens to be up to date — i.e. the branch would go unexercised on CI and
        # on any clone, which is the "a fixture no consumer claims yields skips that
        # read as passes" failure. `control` cannot serve: whether it is in state 1
        # depends on when the suite last ran with the network up, which is #241.
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT OR REPLACE INTO prices (ticker, price_date, close, adj_close) "
            "SELECT p.ticker, ?, p.close, p.adj_close FROM prices p "
            "JOIN (SELECT ticker, MAX(price_date) mx FROM prices GROUP BY ticker) m "
            "  ON p.ticker = m.ticker AND p.price_date = m.mx",
            (date.today().isoformat(),))
        conn.commit()
        conn.close()
    return db


def _stream(page, db, monkeypatch, legacy: bool):
    """Render one page and return its text elements. With legacy=True the banner is
    the old pure clock read, so the two runs differ ONLY in the banner."""
    import requests

    import src.asof as asof
    import src.db
    import src.prices
    import streamlit as st

    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: (_ for _ in ()).throw(socket.gaierror("blocked")))
    monkeypatch.setattr(src.prices._SESSION, "get",
                        lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("blocked")))
    monkeypatch.setattr(src.db, "DB_PATH", db)
    monkeypatch.setattr(src.db, "_migrated_paths", set())
    src.prices._reset_trailing_memo()

    if legacy:
        monkeypatch.setattr(
            asof, "as_of_live_line",
            lambda today=None, **_kw: f"Live data as of {asof.format_long_date(today or date.today())}.")

    # st.cache_data outlives an AppTest instance and is keyed on the as-of date,
    # not on the database or the patched banner behind it.
    st.cache_data.clear()
    st.cache_resource.clear()

    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "pages" / page), default_timeout=300).run()
    assert not at.exception, f"{page} raised (legacy={legacy}): {at.exception}"

    out = []
    for kind in ("warning", "error", "info", "success", "caption", "markdown",
                 "subheader", "title", "header"):
        out.extend(f"{kind}|{el.value}" for el in getattr(at, kind, []))
    for m in at.metric:
        out.append(f"metric|{m.label}|{m.value}|{m.delta}")
    return out


@pytest.mark.parametrize("page", PAGES)
@pytest.mark.parametrize("mode", ["control", "partial", "current"])
def test_banner_is_the_only_element_that_changes(page, mode, tmp_path, monkeypatch):
    _skip_without_personal_inputs()
    import difflib

    db = _scratch(tmp_path, mode)
    state_one = _in_state_one(page, db, date.today())

    before = _stream(page, db, monkeypatch, legacy=True)
    monkeypatch.undo()
    after = _stream(page, _scratch(tmp_path, mode), monkeypatch, legacy=False)

    changed = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=before, b=after).get_opcodes():
        if tag == "equal":
            continue
        changed.extend(before[i1:i2] + after[j1:j2])

    if state_one:
        # The CONTRACT, not a tolerated exception. State 1 renders the legacy
        # sentence unchanged on purpose, so a difference here is a #189 regression
        # — and the old unconditional `assert changed` would have scored exactly
        # that as a pass while failing on the correct behaviour. Asserting the
        # equality is the coverage this direction adds.
        assert not changed, (
            f"{page}/{mode}: the book is fully current and complete, so the banner "
            f"must render the legacy sentence UNCHANGED (src/asof.py state 1). "
            f"It differed: {changed}"
        )
        return

    assert changed, (
        f"{page}/{mode}: the banner did not change at all, and this book is NOT in "
        f"state 1 — so legacy and current must differ. If they converged, either the "
        f"state-1 derivation above is wrong for this page or the banner stopped "
        f"reporting vintage."
    )
    for el in changed:
        assert any(tok in el for tok in BANNER_TOKENS), (
            f"{page}/{mode}: a NON-banner element changed — this PR must touch "
            f"nothing but the banner: {el[:200]!r}"
        )


@pytest.mark.parametrize("page", PAGES)
def test_partial_banner_differs_from_control_banner(page, tmp_path, monkeypatch):
    """Non-vacuity: a banner that renders the same text in both conditions would
    satisfy the test above (the banner "changed" versus legacy in each) while
    disclosing nothing. Pages with a coverage record must differ between
    conditions; the page without one must NOT, since it cannot see the gap."""
    _skip_without_personal_inputs()

    def banner_of(mode):
        stream = _stream(page, _scratch(tmp_path, mode), monkeypatch, legacy=False)
        monkeypatch.undo()
        return [e for e in stream if any(t in e for t in BANNER_TOKENS)]

    control, partial = banner_of("control"), banner_of("partial")
    assert control and partial, f"{page}: no banner element found"
    if page == "8_Research.py":
        assert control == partial, (
            "a page with no coverage record must report the same vintage in both "
            f"conditions — it cannot see the gap: {control} vs {partial}")
    else:
        assert control != partial, (
            f"{page}: the banner is identical with two holdings unpriced: {control}")


def test_state_one_is_reachable_on_coverage_pages_and_not_on_the_others(tmp_path):
    """Non-vacuity for the derivation itself, and the asymmetry #265 describes.

    `_in_state_one` returning False everywhere would silently disable the
    equality branch above and restore the old behaviour with no test failing. So
    pin both halves against a deterministically-current cache:

      1_SAA / 2_Performance  reach state 1 — their frontier is
                             coverage.frontier_served, which includes today
      8_Research             CANNOT — committed_price_frontier excludes today by
                             construction, so its banner reports a day behind on a
                             perfectly fresh cache (that cross-page contradiction
                             is #265, and is a product question, not this test's)

    Pinning a past date in the fixture instead would make this test impossible to
    write, which is the argument for deriving.
    """
    _skip_without_personal_inputs()
    db = _scratch(tmp_path, "current")
    today = date.today()

    assert _in_state_one("1_SAA.py", db, today), (
        "the cache was stamped current, so the coverage pages must be in state 1; "
        "if this fails the equality branch is unreachable and the fix is inert"
    )
    assert _in_state_one("2_Performance.py", db, today)
    assert not _in_state_one("8_Research.py", db, today), (
        "a page with no coverage record cannot reach state 1 — committed_price_"
        "frontier excludes today. If this ever passes, the two frontier definitions "
        "have converged and #265 is resolved; update this test deliberately."
    )


def test_state_one_derivation_tracks_completeness_not_just_the_date(tmp_path):
    """State 1 is `frontier == today AND nothing unresolved`. The second half is
    load-bearing: with holdings unpriced the banner must disclose, so the pages are
    NOT in state 1 even on a cache stamped current."""
    _skip_without_personal_inputs()
    db = _scratch(tmp_path, "current")
    conn = sqlite3.connect(db)
    conn.execute(
        f"DELETE FROM prices WHERE ticker IN ({','.join('?' * len(KILL))})", KILL)
    conn.commit()
    conn.close()

    assert not _in_state_one("1_SAA.py", db, date.today()), (
        "holdings are unresolved, so this is state 3 (incomplete), not state 1 — "
        "deriving on the date alone would wrongly demand an identical banner while "
        "the page is correctly disclosing a gap"
    )
