"""The cover says what a quarter's lock holds, and what the report computes at each
render (#478 section 3).

Every locked report's cover said "this quarter now locks every input its report reads,
not only prices". The same report labels its Macro Context and Asset Evaluation
sections "Live, not locked", and #478 lists 59 inputs a locked report reads that its
lock does not hold. What that sentence meant was narrower: on September 26, 2026 the
lock began to hold its report's provider inputs, and the executive summary's CAPE
reading moved to the quarter's last month.

Pinned here:
  * the cover names what the lock holds, read from the lock's own lists: its prices,
    the inputs it holds, and the inputs it is still waiting on;
  * it says Macro Context and Asset Evaluation are computed at each render, and those
    are exactly the sections the report builds outside the lock;
  * every committed lock's cover carries the same sentence, in both books; a lock taken
    later carries it too, where it got no such sentence before;
  * the restatement line keeps what it was for, the CAPE reading, and claims no more;
  * nothing claims every input is locked.

The locks' payloads are pinned whole by tests/test_style_box_withdrawn.py.
"""
from __future__ import annotations

import ast
import os
import shutil
from datetime import date
from pathlib import Path

import pytest
from markupsafe import escape

from tests.conftest import FROZEN_BOOK, pin_today, point_at_frozen_book, unpin_leftovers

ROOT = Path(__file__).resolve().parent.parent
BOOKS = [ROOT / "data" / "demo.db", FROZEN_BOOK]
COMMITTED = {"2025Q2": "June 2025", "2025Q3": "September 2025", "2025Q4": "December 2025",
             "2026Q1": "March 2026", "2026Q2": "June 2026"}
LIVE = ("Macro Context and Asset Evaluation are not locked: they are computed each time "
        "the report is generated.")
# What each committed lock holds: its seven inputs, less the ETF metadata, which holds no
# duration in a lock taken before #455.
HOLDS = ("This quarter's lock holds prices, dividends, CAPE, the Fama-French factors, the "
         "momentum factor and the HYG credit proxy. " + LIVE)


@pytest.fixture(params=BOOKS, ids=lambda p: p.name)
def book(request, tmp_path, monkeypatch):
    import src.db as db
    copy = tmp_path / request.param.name
    shutil.copyfile(request.param, copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    return copy


# ── the sentence is read from the lock ───────────────────────────────────────

def test_every_committed_locks_cover_says_what_it_holds_in_the_same_words(book):
    from src.cache import get_quarter_snapshot, lock_holds_note
    from src.input_lock import ALL_INPUTS
    for q in COMMITTED:
        snap = get_quarter_snapshot(q)[0]
        assert set(snap.inputs) == set(ALL_INPUTS) and not snap.inputs_pending, (
            q, "premise: the lock's list names all seven inputs and none waits")
        assert lock_holds_note(snap) == HOLDS, q


def test_the_sentence_follows_the_locks_own_lists(book):
    """The contrast: change what the lock holds, and the sentence changes with it."""
    from src.cache import get_quarter_snapshot, lock_holds_note
    from src.input_lock import (CAPE, DIVIDENDS, ETF_METADATA, FF5_DEVELOPED_EXUS, FF5_US,
                                HYG, UMD)
    snap = get_quarter_snapshot("2026Q2")[0]

    def held(*names):
        return {k: v for k, v in snap.inputs.items() if k in names}

    # An input the lock does not hold is not named.
    assert lock_holds_note(snap._replace(inputs=held(DIVIDENDS, CAPE, FF5_US,
                                                     FF5_DEVELOPED_EXUS, HYG))) == (
        "This quarter's lock holds prices, dividends, CAPE, the Fama-French factors and the "
        "HYG credit proxy. " + LIVE)
    # One it is waiting on is named as waiting: Q3 on October 1, before French
    # publishes September and before SCHP's duration is dated inside the quarter.
    waiting = snap._replace(
        inputs=held(DIVIDENDS, CAPE, HYG),
        inputs_pending={FF5_US: "2026-08-31", FF5_DEVELOPED_EXUS: "2026-08-31",
                        UMD: "2026-08-31", ETF_METADATA: "2026-06-30"})
    assert lock_holds_note(waiting) == (
        "This quarter's lock holds prices, dividends, CAPE and the HYG credit proxy, and is "
        "waiting on the Fama-French factors, the momentum factor and the fund durations. "
        + LIVE)
    # One French file without the other is named for what it is.
    assert "holds prices and the US Fama-French factors. " in lock_holds_note(
        snap._replace(inputs=held(FF5_US)))
    # The ETF metadata is named when it holds a duration, and only then.
    dated = dict(snap.inputs)
    dated[ETF_METADATA] = {"VGIT": {"duration_years": 4.9, "as_of": "2026-06-30"}}
    assert lock_holds_note(snap._replace(inputs=dated)) == HOLDS.replace(
        " and the HYG credit proxy.", ", the HYG credit proxy and the fund durations.")
    assert "fund durations" not in lock_holds_note(snap), (
        "a lock taken before #455 holds the file with no duration in it")
    # A lock from before inputs were locked holds prices and says so.
    assert lock_holds_note(snap._replace(inputs=None, inputs_pending=None)) == (
        "This quarter's lock holds prices. " + LIVE)
    # No lock, no sentence: a custom date range takes none.
    assert lock_holds_note(None) is None


def test_every_input_a_lock_can_hold_has_a_word_on_the_cover():
    from src.cache import _INPUT_WORDS
    from src.input_lock import ALL_INPUTS
    assert set(_INPUT_WORDS) == set(ALL_INPUTS)


# ── the sections it calls unlocked are the ones built outside the lock ───────

def _builders_by_lock() -> "tuple[set, set]":
    """(section builders called inside the report's lock block, those called outside
    it), read from generate_quarterly_report_bytes' syntax tree."""
    tree = ast.parse((ROOT / "src" / "reports.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
              and n.name == "generate_quarterly_report_bytes")
    block = next(n for n in ast.walk(fn) if isinstance(n, ast.With)
                 and any(isinstance(i.context_expr, ast.Name) and i.context_expr.id == "ctx"
                         for i in n.items))

    def builders(node):
        return {c.func.id for c in ast.walk(node) if isinstance(c, ast.Call)
                and isinstance(c.func, ast.Name) and c.func.id.startswith("_build_")}

    inside = builders(block)
    return inside, builders(fn) - inside


def test_the_sections_named_as_unlocked_are_the_ones_built_outside_the_lock():
    from src.cache import UNLOCKED_SECTIONS
    inside, outside = _builders_by_lock()
    assert inside == {"_build_executive_summary", "_build_holdings_section",
                      "_build_performance_section", "_build_attribution_section",
                      "_build_positioning_section", "_build_factor_section",
                      "_build_benchmark_section", "_build_thesis_section"}
    # Two sections, and the methodology page's own text (targets and the benchmark
    # basket, read from the book), which is not a section of figures.
    assert outside == {"_build_macro_section", "_build_asset_eval_section",
                       "_build_methodology_vars"}
    assert UNLOCKED_SECTIONS == ("Macro Context", "Asset Evaluation")
    # Each is the heading its section prints, so the reader can find what the cover names.
    template = (ROOT / "templates" / "quarterly_report.html").read_text(encoding="utf-8")
    for name in UNLOCKED_SECTIONS:
        assert f">{name}</h2>" in template or f">{name}:" in template, name


# ── the rendered cover ───────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def q2_cover(tmp_path_factory):
    """The cover of the frozen book's committed Q2 2026 report, as rendered text."""
    from src import reports
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
        mp.setattr(reports, "_render_pdf", lambda html: html.encode("utf-8"))
        pin_today(mp)
        point_at_frozen_book(mp, tmp_path_factory.mktemp("q2"))
        html = reports.generate_quarterly_report_bytes("2026-03-31", "2026-06-30",
                                                       is_demo=True).decode("utf-8")
    unpin_leftovers()
    flat = " ".join(html.split())
    return flat, flat.split("<h2>Executive Summary</h2>", 1)[0]


def test_the_cover_names_what_the_lock_holds_and_what_is_computed_at_each_render(q2_cover):
    report, cover = q2_cover
    assert cover.count(str(escape(HOLDS))) == 1
    assert report.count(str(escape(HOLDS))) == 1, "on the cover, and nowhere else"
    # The two sections say the same of themselves, where the reader meets them.
    assert report.count("Live, not locked to the report") == 2


def test_the_restatement_line_keeps_the_cape_reading_and_claims_no_more(q2_cover, book):
    from src.cache import get_quarter_snapshot, inputs_restatement_note
    _, cover = q2_cover
    for q, month in COMMITTED.items():
        assert inputs_restatement_note(q, get_quarter_snapshot(q)[0]) == (
            "Restated September 26, 2026: the lock's CAPE reading is the quarter's last "
            f"monthly observation, {month}; earlier versions of this report cited the "
            "latest CAPE on file when they were generated."), q
    assert str(escape("Restated September 26, 2026: the lock's CAPE reading is the quarter's "
                      "last monthly observation, June 2026;")) in cover


def test_nothing_claims_every_input_is_locked(q2_cover):
    report, _ = q2_cover
    for claim in ("locks every input", "every input its report reads"):
        assert claim not in report, claim
        for path in ("src/cache.py", "src/reports.py", "templates/quarterly_report.html"):
            assert claim not in (ROOT / path).read_text(encoding="utf-8"), (path, claim)


def test_a_lock_taken_now_carries_the_sentence_and_names_what_it_waits_on(tmp_path, monkeypatch):
    """Q3 on October 5: a quarter that closed after the inputs rule got no cover sentence
    at all. It now says what its lock holds and what it is still waiting on."""
    from src import reports
    from src.cache import (capture_quarter_snapshot, inputs_restatement_note,
                           lock_holds_note)
    from tests.test_cape_read_dates import _extend_prices_through
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    monkeypatch.setattr(reports, "_render_pdf", lambda html: html.encode("utf-8"))
    pin_today(monkeypatch, date(2026, 10, 5))
    try:
        frozen = point_at_frozen_book(monkeypatch, tmp_path)
        _extend_prices_through(frozen, "2026-09-30")
        snap, _ = capture_quarter_snapshot("2026Q3")
        note = lock_holds_note(snap)
        html = " ".join(reports.generate_quarterly_report_bytes(
            "2026-06-30", "2026-09-30", is_demo=True).decode("utf-8").split())
    finally:
        unpin_leftovers()
    assert snap.inputs_pending, "premise: French has not published September"
    assert note.startswith("This quarter's lock holds prices, dividends")
    assert ", and is waiting on " in note and "the Fama-French factors" in note.split(
        "waiting on ", 1)[1]
    assert note.endswith(LIVE)
    assert inputs_restatement_note("2026Q3", snap) is None, "never reported the old way"
    assert str(escape(note)) in html.split("<h2>Executive Summary</h2>", 1)[0]
    assert "Restated September 26, 2026" not in html
