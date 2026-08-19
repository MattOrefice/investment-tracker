"""#260 — the empty-book branch on pages/11 says WHICH condition emptied it.

Three fixture modes, one per empty return in `_sleeve_weights_impl`:

  total        every price deleted        -> `total == 0`     (the #260 condition)
  no_holdings  ledger dated into the future -> `holdings.empty`
  cash_only    only a cash-sleeve holding prices -> `invested <= 0`

`total` is the mode #260 asked for and #188's PR-2 harness never built. The other
two are here because the issue named the wrong two conditions: it described
`holdings.empty` and `invested <= 0` and the one that actually fires is the third.
A test covering only the reported condition would have inherited that mistake.

WHY THE FIXTURES ARE BUILT THIS WAY, since two obvious constructions do not work:

  - Deleting every trade does NOT produce `holdings.empty`. `get_portfolio_account`
    raises first (#139), so the page never reaches the branch. That is why
    no_holdings dates the ledger forward instead: the account still resolves, and
    nothing is open on the as-of date.
  - cash_only cannot be built by deleting prices alone. With no prices at all the
    total is zero and the FIRST return wins, so `invested <= 0` is unreachable
    without a holding that prices into the cash sleeve — hence the inserted BIL
    lot. BIL is the only ticker mapped to the cash sleeve.

The branch each mode reaches is pinned BEHAVIOURALLY, not by line number — a line
reference in a test decays exactly like one in an issue body, which is how #260
came to name the wrong two returns.

Personal-mode: needs tracker.db and the positions CSV, so these skip in CI. The
CI-visible half is tests/test_coverage_empty_book.py.
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
PAGE = "11_Capital_Deployment.py"

# The sentence being retired. It rendered for all three conditions below and was
# false in every one; the state it would have been right about (a database with no
# trades) raises upstream and never reaches this branch. Asserted absent in every
# mode — that "no true case" finding is what justifies rewriting both messages.
OLD_SENTENCE = "Seed the database first"

MODES = ["total", "no_holdings", "cash_only"]


@pytest.fixture(autouse=True)
def _skip_without_personal_inputs():
    """Autouse, not a helper each test remembers to call. Written as a helper
    first, and nothing failed — in CI the copyfile would have ERRORED instead of
    skipping, and locally a forgotten call is invisible because the inputs are
    present. An unclaimed guard is the same defect as an unclaimed fixture."""
    from src.household_data import find_latest_positions_csv
    if (find_latest_positions_csv() is None or not TRACKER_DB.exists()
            or not (ROOT / "private" / "account_map.json").exists()):
        pytest.skip("personal-mode inputs absent")


def _scratch(tmp_path, mode):
    db = tmp_path / f"{mode}.db"
    shutil.copyfile(TRACKER_DB, db)
    os.chmod(db, 0o644)
    conn = sqlite3.connect(db)

    if mode == "total":
        conn.execute("DELETE FROM prices")
    elif mode == "no_holdings":
        # Ledger survives (so the account resolves); nothing is open today.
        conn.execute("UPDATE trades SET trade_date = '2099-01-01'")
    elif mode == "cash_only":
        acct = conn.execute("SELECT account_id FROM trades LIMIT 1").fetchone()[0]
        when = conn.execute("SELECT MIN(trade_date) FROM trades").fetchone()[0]
        conn.execute(
            "INSERT INTO trades (account_id, ticker, trade_date, action, shares, "
            "price, fees, lot_source) VALUES (?, 'BIL', ?, 'Buy', 100, 91.5, 0.0, "
            "'test fixture')", (acct, when))
        # Everything except the cash holding loses its prices, so the non-cash
        # remainder values to zero while the total does not.
        conn.execute("DELETE FROM prices WHERE ticker != 'BIL'")
    else:  # pragma: no cover - guards a typo'd parametrisation
        raise AssertionError(f"unknown mode {mode!r}")

    conn.commit()
    conn.close()
    return db


def _render(page, db, monkeypatch):
    import requests

    import src.db
    import src.prices
    import streamlit as st

    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: (_ for _ in ()).throw(socket.gaierror("blocked")))
    monkeypatch.setattr(src.prices._SESSION, "get",
                        lambda *a, **k: (_ for _ in ()).throw(
                            requests.ConnectionError("blocked")))
    monkeypatch.setattr(src.db, "DB_PATH", db)
    monkeypatch.setattr(src.db, "_migrated_paths", set())
    src.prices._reset_trailing_memo()

    # Keyed on the as-of date only, not on the database behind it — without this
    # the first mode's data is read back by the next one.
    st.cache_data.clear()
    st.cache_resource.clear()

    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "pages" / page), default_timeout=300).run()
    assert not at.exception, f"{page} raised in this mode: {at.exception}"
    return at


def _texts(at):
    out = []
    for kind in ("info", "warning", "error", "success", "caption", "markdown",
                 "title", "header", "subheader"):
        out.extend(str(el.value) for el in getattr(at, kind, []))
    return out


def _coverage_for(db, monkeypatch):
    """The record the page branches on, read through the same producer the page
    uses rather than recomputed here."""
    import requests

    import src.db
    import src.prices
    import streamlit as st

    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: (_ for _ in ()).throw(socket.gaierror("blocked")))
    monkeypatch.setattr(src.prices._SESSION, "get",
                        lambda *a, **k: (_ for _ in ()).throw(
                            requests.ConnectionError("blocked")))
    monkeypatch.setattr(src.db, "DB_PATH", db)
    monkeypatch.setattr(src.db, "_migrated_paths", set())
    src.prices._reset_trailing_memo()
    st.cache_data.clear()

    from src.holdings import sleeve_weights_with_coverage
    return sleeve_weights_with_coverage(date.today().isoformat())


# ── the branch is reached, and by three different routes ──────────────────────

@pytest.mark.parametrize("mode", MODES)
def test_every_mode_empties_the_sleeve_frame(mode, tmp_path, monkeypatch):
    """Non-vacuity. If a fixture stopped emptying the frame the page would render
    normally and every message assertion below would pass by never running."""
    frame, _cov = _coverage_for(_scratch(tmp_path, mode), monkeypatch)
    assert frame.empty, f"{mode}: fixture no longer reaches the empty-book branch"


def test_the_three_modes_reach_three_different_states(tmp_path, monkeypatch):
    """The whole point of the change. Three fixtures collapsing onto one state
    would let a single hardcoded message satisfy every other test here."""
    from src.coverage import empty_book_state

    seen = {}
    for mode in MODES:
        _frame, cov = _coverage_for(_scratch(tmp_path, mode), monkeypatch)
        seen[mode] = empty_book_state(cov)
    assert len(set(seen.values())) == 3, f"modes collapsed onto one state: {seen}"


def test_cash_only_pins_the_invested_le_zero_return(tmp_path, monkeypatch):
    """#260 named `invested <= 0` as a cause and it is the one branch no test
    reached. Pinned by the combination only it can produce: something PRICED, and
    the frame still empty. `holdings.empty` yields no requests at all and
    `total == 0` yields no resolutions, so neither can present this way.
    """
    frame, cov = _coverage_for(_scratch(tmp_path, "cash_only"), monkeypatch)
    assert frame.empty
    assert cov.resolved, "nothing priced — this is the total==0 state, not cash_only"
    assert cov.requested, "nothing requested — this is the holdings.empty state"


# ── what the reader is told ───────────────────────────────────────────────────

@pytest.mark.parametrize("mode", MODES)
def test_no_mode_tells_the_reader_to_seed_the_database(mode, tmp_path, monkeypatch):
    at = _render(PAGE, _scratch(tmp_path, mode), monkeypatch)
    joined = " ".join(_texts(at))
    assert OLD_SENTENCE not in joined, (
        f"{mode}: the retired sentence still renders, and it is false here")


def test_total_names_the_holdings_it_will_not_show(tmp_path, monkeypatch):
    """The #260 condition. The reader must be able to tell a pricing failure from
    an empty portfolio, so the note carries the count, the names, and the reason.
    """
    db = _scratch(tmp_path, "total")
    _frame, cov = _coverage_for(db, monkeypatch)
    at = _render(PAGE, db, monkeypatch)
    joined = " ".join(_texts(at))

    assert str(len(cov.requested)) in joined, "must say how many positions are held"
    for ticker in sorted(cov.unresolved_tickers()):
        assert ticker in joined, f"{ticker} is held and unpriced; name it"
    assert "no_cached_rows" in joined, "must carry the reason"
    assert "not an empty or unseeded portfolio" in joined, (
        "the clause blocking the wrong inference is the load-bearing one")


def test_total_renders_as_a_warning_not_an_info(tmp_path, monkeypatch):
    """A failure to measure and a fact about the book are different kinds of
    statement, and the element type is the only part of that a skimming reader
    sees."""
    at = _render(PAGE, _scratch(tmp_path, "total"), monkeypatch)
    assert any("not an empty or unseeded portfolio" in str(el.value)
               for el in at.warning), "the pricing failure must render as a warning"


@pytest.mark.parametrize("mode", ["no_holdings", "cash_only"])
def test_non_failure_states_render_as_info(mode, tmp_path, monkeypatch):
    at = _render(PAGE, _scratch(tmp_path, mode), monkeypatch)
    assert at.info, f"{mode} is a fact about the book, not a failure"
    assert not at.warning, f"{mode} is not a failure and must not warn"


def test_no_holdings_states_a_fact_without_an_instruction(tmp_path, monkeypatch):
    at = _render(PAGE, _scratch(tmp_path, "no_holdings"), monkeypatch)
    joined = " ".join(_texts(at))
    assert "No open positions as of" in joined
    assert "ledger" in joined, "say the ledger exists — that is the distinguishing fact"


def test_cash_only_says_undefined_rather_than_zero(tmp_path, monkeypatch):
    at = _render(PAGE, _scratch(tmp_path, "cash_only"), monkeypatch)
    joined = " ".join(_texts(at))
    assert "entirely cash" in joined
    assert "undefined" in joined, "zero weights and undefined weights differ"


def test_the_page_stops_rather_than_rendering_a_partial_answer(tmp_path, monkeypatch):
    """Everything below the branch is sized from weights that do not exist.
    Holdings rendered under a failure banner read as a partial answer."""
    at = _render(PAGE, _scratch(tmp_path, "total"), monkeypatch)
    assert not at.dataframe, "no table may render once the book cannot be valued"
    assert not at.metric, "no metric may render once the book cannot be valued"
