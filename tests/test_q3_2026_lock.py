"""Q3 2026's lock is committed in data/demo.db (the quarterly close-out's last demo step).

Taken on October 13, 2026 by tools/lock_demo_quarters.py, from the committed book once
it held no gap (#491) and its Q3 dividends had been re-fetched and found unchanged.
Until then each demo container locked Q3 at runtime, from whatever it had fetched.
Pinned here:

  * what it holds: 26 price series from 2020 through September 30 with no gap inside a
    history, the dividends through the quarter, CAPE's September reading with the date
    it was read, and HYG through September 30. What it holds never moves again, so its
    frames and held inputs are pinned as hashes;
  * the fund durations, which it waited on when it was taken (SCHP's was dated June 30)
    and has held since SCHP's Q3 figure was entered and the lock completed in the
    committed book the same day (tools/complete_demo_lock_inputs.py). Nothing else in
    the payload moved: with the durations put back among the inputs it waits on, it is
    the lock as taken, byte for byte (tests/test_complete_demo_lock_inputs.py);
  * French's three files, which it waited on when it was taken (each ended August 31)
    and has held since French published September and the lock completed in the
    committed book on October 27, 2026, by the same tool. It waits on nothing now. With
    the three put back among the inputs it waits on, it is main's lock at 1855bb4, byte
    for byte;
  * TIP's two ex-dates in the quarter (#490);
  * its cover names every input it holds, waits on nothing and carries no restatement
    line, its factor and benchmark sections render from the factors it holds, and its
    duration line renders;
  * the five earlier locks are as #491's restatement left them, and the frozen book,
    whose prices end July 20, holds no Q3 lock.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from tests.conftest import (
    FROZEN_BOOK,
    Q3_2026_FRENCH_AS_TAKEN,
    before_the_gap_record,
    before_the_policy_was_held,
    q3_2026_before_french_was_completed,
)

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "data" / "demo.db"
Q3 = "2026Q3"
COVER = ("This quarter's lock holds prices, dividends, CAPE, the Fama-French factors, the "
         "momentum factor, the HYG credit proxy, the fund durations and the SAA policy. "
         "Macro Context and Asset Evaluation are not locked: they are computed each time the "
         "report is generated.")
# What the cover calls each of French's inputs, which the lock waited on until October 27.
COVER_WORDS = {"ff5_us": "the Fama-French factors", "ff5_developed_exus": "the Fama-French factors",
               "umd": "the momentum factor"}
# SHA-256 of the lock's stored payload on main at 1855bb4, before French was completed.
BEFORE_FRENCH = "38f4cd12028fd08c561578055aa1ad11d245a52f3fe5803a2648a3704a204e9d"
# The fund durations the lock holds, as (years, measure, as-of): data/etf_metadata.json as
# it stood on October 13, 2026, every figure dated inside the quarter.
DURATIONS = {
    "VGIT": (4.9, "average duration", "2026-08-31"),
    "SCHP": (6.4, "effective duration", "2026-08-31"),
    "IEF": (6.86, "effective duration", "2026-09-25"),
    "TIP": (6.2, "effective duration", "2026-09-25"),
    "BIL": (0.11, "option-adjusted duration", "2026-09-25"),
    "AGG": (5.7, "effective duration", "2026-09-25"),
}
# SHA-256 of json.dumps of each part the lock HOLDS, as locked. A held part never moves.
HELD = {
    "adj_close": "9edb3d6ce6856f640c29d3d94d419b0941304510e3eb1020185879246e6747a8",
    "close": "96c7adb617bac0ffd8e9cc8a058b8fe4986663c0a98376e0b5c0d2073bf51e49",
    "dividends": "2ce842a62b7b3ef343d5e6e28a207dafd07d0b73b4bebf50527595e87a53ee47",
    "cape": "3466fb99aced02d9e8676f764b676718009c584cc17fc3c3f999f228a3a2b24d",
    "hyg": "3cc7da9cacc3eac8925f1c9a4d566715cb86b8038b7506bb3d6d47de2173254a",
    "etf_metadata": "d3bcefa3ce390398e83a8a187c890c82a729f17771c92d66448ca38f8792bb6a",
    "ff5_us": "0f5f718cba19013ba90442bb28b622ae4d6191d4a19ed7c32aebcea9112c2a03",
    "ff5_developed_exus": "9dcc29c9574b12a838c7f8578b0a14ae52d93ace34f8aee530bef4186ac6c875",
    "umd": "730855791f820a438f6fa509e342cb32862c86f6844da6d3ba395ff8105753aa",
}
# Each earlier lock's stored payload as its SHA-256, as #491's restatement left them: the
# first four are unchanged since 10313a9, and Q2 2026 is the restated one.
EARLIER = {
    "2025Q2": "328256b86e80e21d4f3f5ffdd1d1c68513a9e55a208cbc1e2ebdcea1f4bef88c",
    "2025Q3": "d5bfda797eafba9b23e066c5f38878fc65a6145c78b7643c5345f5fe3f1cf8c9",
    "2025Q4": "75df988319b18ea2840bac6d7557a8eb81e1e883cb5b3796a02c92fbf57605cd",
    "2026Q1": "4e2a6fc7a4197d6dc9e09f37fe5686e40d63af8f4ecae39e3864c8a80c8d69c4",
    "2026Q2": "d82e4e8fe7ae725ac68db6c0c44767308f2d0d636ee564bb58c9f94a49de8995",
}


def _locks(book) -> "dict[str, tuple]":
    con = sqlite3.connect(f"file:{Path(book).as_posix()}?mode=ro", uri=True)
    try:
        return {r[0]: tuple(r[1:]) for r in con.execute(
            "SELECT quarter_id, snapshot_date, captured_at, snapshot_data FROM "
            "quarter_snapshots")}
    finally:
        con.close()


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture(scope="module")
def payload() -> dict:
    """The lock as it was taken and completed. The SAA policy recorded in it afterwards
    (#525) is undone first; tests/test_locks_hold_the_policy.py pins the undoing and the
    policy."""
    return json.loads(before_the_policy_was_held(_locks(DEMO)[Q3][2]))


@pytest.fixture
def snap(tmp_path, monkeypatch):
    """The committed lock as the report reads it, from a copy of the book."""
    import src.db as db
    from src.cache import get_quarter_snapshot
    copy = tmp_path / "demo.db"
    shutil.copyfile(DEMO, copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    return get_quarter_snapshot(Q3)[0]


def test_the_lock_holds_prices_through_the_quarters_last_session_with_no_gap(payload, snap):
    from src.benchmarks import BLENDED_RULE
    from src.cache import INPUTS_RULE, QUARTER_END_RULE, _interior_gaps
    assert _locks(DEMO)[Q3][0] == "2026-09-30"
    assert (payload["rule"], payload["inputs_rule"], payload["benchmark_rule"]) == (
        QUARTER_END_RULE, INPUTS_RULE, BLENDED_RULE)
    assert payload["gaps"] == []
    for basis in ("adj_close", "close"):
        frame = payload[basis]
        assert len(frame["columns"]) == 26, basis
        assert (frame["index"][0][:10], frame["index"][-1][:10]) == ("2020-01-02", "2026-09-30")
        assert None not in frame["data"][-1], f"{basis}: every series has the last close"
    assert _interior_gaps(snap.adj_close) == [] and _interior_gaps(snap.close) == []


def test_what_the_lock_holds_is_pinned(payload):
    assert sorted(payload["inputs"]) == ["cape", "dividends", "etf_metadata", "ff5_developed_exus",
                                         "ff5_us", "hyg", "umd"]
    assert payload["inputs_through"] == {"hyg": "2026-09-30", "cape": "2026-09-01",
                                         "etf_metadata": "2026-08-31", "ff5_us": "2026-09-30",
                                         "ff5_developed_exus": "2026-09-30", "umd": "2026-09-30"}
    parts = {"adj_close": payload["adj_close"], "close": payload["close"], **payload["inputs"]}
    assert {k: _digest(json.dumps(v)) for k, v in parts.items()} == HELD


def test_cape_is_septembers_reading_with_the_date_it_was_read(payload):
    """The reading made after September ended (#478 I06): 40.9, read October 4 from
    multpl. The September 25 reading, 41.48, could not lock."""
    cape = payload["inputs"]["cape"]
    assert cape["kind"] == "cape_readings"
    assert (cape["index"][-1][:10], cape["data"][-1], cape["read_on"][-1], cape["source"][-1]) == (
        "2026-09-01", 40.9, "2026-10-04", "multpl")


def test_tip_has_two_ex_dates_in_the_quarter(payload):
    """#490, settled: TIP has 2 Q3 2026 dividends against 3 in Q3 2025. iShares' own
    TIP page lists September 1, 2026 at $0.000000, a declared zero; the provider reports
    no event for it and the lock holds none. The quarter's 27 rows, across the lock's 26
    tickers, were re-fetched before the lock and matched the committed record."""
    held = payload["inputs"]["dividends"]["data"]
    in_q3 = {t: [(d, a) for d, a in zip(s["index"], s["data"]) if "2026-07-01" <= d <= "2026-09-30"]
             for t, s in held.items()}
    assert in_q3["TIP"] == [("2026-07-01", 1.058), ("2026-08-03", 0.784)]
    assert (len(held), sum(len(v) for v in in_q3.values())) == (26, 27)


def test_it_waits_on_nothing_and_holds_frenchs_files_through_the_quarter(payload):
    """French published September, and the three files it waited on were completed in
    the committed book on October 27, 2026: each held through September 30, as the
    files stood that day."""
    assert payload["inputs_pending"] == {}
    for name in Q3_2026_FRENCH_AS_TAKEN:
        held = payload["inputs"][name]
        assert held["index"][-1][:10] == "2026-09-30" == payload["inputs_through"][name], name


def test_with_french_put_back_it_is_mains_lock_before_the_completion():
    """Nothing else in the payload moved: the three put back among the inputs it waits
    on, with the date each ended when it locked, give main's lock at 1855bb4, byte for
    byte, SAA policy and all."""
    text = _locks(DEMO)[Q3][2]
    before = q3_2026_before_french_was_completed(text)
    assert before != text, "premise: the lock holds French's files"
    assert _digest(before) == BEFORE_FRENCH
    assert json.loads(before)["inputs_pending"] == Q3_2026_FRENCH_AS_TAKEN


def test_it_holds_every_fund_duration_dated_inside_the_quarter(payload):
    """SCHP's was dated June 30 when the lock was taken, so the durations waited. Its
    August 31 figure, Schwab's effective duration, was entered on October 13, and the
    lock took the file as it then stood."""
    held = payload["inputs"]["etf_metadata"]
    assert held["kind"] == "json"
    funds = {t: v for t, v in held["data"].items() if isinstance(v, dict)}
    assert {t: (v["duration_years"], v["duration_measure"], v["as_of"])
            for t, v in funds.items()} == DURATIONS
    assert all("2026-07-01" <= v["as_of"] <= "2026-09-30" for v in funds.values())
    assert all(v["duration_source"] for v in funds.values())
    # The oldest date in the file, which is what the lock records it as covering through.
    assert payload["inputs_through"]["etf_metadata"] == min(d for _y, _m, d in DURATIONS.values())


def test_its_cover_names_every_input_it_holds_and_claims_no_restatement(snap):
    from src import cache
    note = cache.lock_holds_note(snap)
    assert note == COVER
    assert snap.inputs_pending == {}
    assert "waiting on" not in note
    for name in Q3_2026_FRENCH_AS_TAKEN:
        assert COVER_WORDS[name] in note.split("lock holds")[1].split(".")[0], name
    assert cache.restatement_note(Q3, snap) is None
    assert cache.inputs_restatement_note(Q3, snap) is None
    for line in (cache.undated_durations_note, cache.input_corrections_note,
                 cache.lot_rebuild_note, cache.style_box_withdrawn_note,
                 cache.filled_closes_note, cache.benchmark_construction_note):
        assert line(snap) is None, line.__name__


def test_its_factor_and_benchmark_sections_render_from_the_factors_it_holds(snap, monkeypatch):
    """No longer pending: until October 27, 2026 both read "The Fama-French factor data
    on file ends August 31, 2026". Each regression now runs through September 30 on
    French's files as the lock holds them, whatever the files say afterwards. Its
    figures are held by the locked-figures golden."""
    from src import cache, factors, reports
    assert reports._pending_note(snap, "factor") is None
    assert reports._pending_note(snap, "benchmark") is None
    # The files are not read inside the lock: files that would raise prove it.
    for region in factors._FACTOR_CONFIG:
        monkeypatch.setitem(factors._FACTOR_CONFIG[region], "cache", Path("no-such-file.csv"))
    monkeypatch.setattr(factors, "_UMD_CACHE", Path("no-such-file.csv"))
    with cache.snapshot_price_context(snap):
        factor = reports._build_factor_section("2026-09-30")
        bench = reports._build_benchmark_section("2026-06-30", "2026-09-30")
    window = "May 2, 2025 to September 30, 2026"
    assert [s["label"] for s in factor["sleeves"]] == ["US Equity Sleeve",
                                                       "International Core Sleeve"]
    assert all(s["sample_window"] == window and s["rows"] for s in factor["sleeves"])
    assert bench["sample_window"] == window and bench["rows"]


def test_its_duration_line_renders_from_the_durations_it_holds(snap, monkeypatch):
    """No longer pending. The sleeve's figure is the lock's VGIT and SCHP durations
    weighted by the two sleeves, against AGG's, whatever the file says afterwards."""
    from src import cache, etf_metadata, reports
    from src.holdings import get_sleeve_weights_on_date
    assert reports._pending_note(snap, "positioning", subject="duration figure") is None
    # The file is not read inside the lock: one that would raise proves it.
    monkeypatch.setattr(etf_metadata, "META_PATH", Path("no-such-file.json"))
    with cache.snapshot_price_context(snap):
        line = reports._build_positioning_section("2026-09-30")["duration_line"]
        weights = get_sleeve_weights_on_date("2026-09-30")["Actual Weight"]
    core, tips = float(weights["Core Fixed Income"]), float(weights["TIPS"])
    years = (core * DURATIONS["VGIT"][0] + tips * DURATIONS["SCHP"][0]) / (core + tips)
    assert line.startswith(
        f"Fixed Income sleeve (Core FI + TIPS) effective duration: {round(years, 1)} yrs "
        f"vs {DURATIONS['AGG'][0]} yrs for AGG, the iShares ETF tracking the Bloomberg US Agg ")
    assert "Pending" not in line


def test_the_five_earlier_locks_are_as_the_restatement_left_them():
    # Each was later given a record of #469's rewrite, appended as its last key (#478,
    # pinned by tests/test_gap_rewrite_is_recorded.py). Less that, it is as it was.
    locks = _locks(DEMO)
    assert sorted(locks) == sorted(EARLIER) + [Q3]
    assert {q: _digest(before_the_gap_record(locks[q][2])) for q in EARLIER} == EARLIER


def test_the_frozen_book_holds_no_q3_lock():
    """Its prices end July 20, 2026, so it cannot cover the quarter, and its five locks
    are demo.db's five."""
    locks = _locks(FROZEN_BOOK)
    assert sorted(locks) == sorted(EARLIER)
    assert {q: _digest(before_the_gap_record(locks[q][2])) for q in EARLIER} == EARLIER
    assert {q: locks[q][2] for q in EARLIER} == {q: _locks(DEMO)[q][2] for q in EARLIER}
