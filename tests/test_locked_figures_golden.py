"""Every figure each committed quarter lock's report prints is the golden's (#525).

A lock holds prices, dividends and its provider inputs. It does not hold the method its
report is computed with, so a change to a method parameter re-renders every locked
quarter, and until now one test pinned three figures of one quarter
(test_q2_locked_headline_figures_golden). This is that idea for every figure and every
lock: each of the eleven committed locks (six in data/demo.db, five in the frozen book)
is rendered, and every figure in its six locked sections and in the Performance page's
locked tiles is compared with tests/fixtures/locked_figures_golden.json.

tests/locked_figures.py renders and reads the figures, and its docstring says how a
figure is told from wording. tools/update_locked_figures_golden.py is the golden's
only writer and refuses to move a lock's figures while the lock's own records are
unchanged. CLAUDE.md's "Locked figures" states the rule for a PR that moves one.

Pinned here:
  * the golden covers exactly the committed locks, and each lock's figures are its;
  * each entry was taken under the lock's own records, so a lock and the golden stay
    in step, and every restatement record a lock carries is said on its cover;
  * the reading itself: typed text is not a figure, a significance label is not, a
    table cell is compared whole, words and their order are free;
  * the proof: a significance cut-off moved from 10% to 20% fails the comparison and
    the tool refuses it; the same change carried with a record in each lock passes.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sqlite3
from pathlib import Path

import pytest

from tests import locked_figures as lf

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = lf.load_golden()
CASES = [(book, quarter) for book in GOLDEN for quarter in GOLDEN[book]]


def _tool():
    spec = importlib.util.spec_from_file_location(
        "update_locked_figures_golden", ROOT / "tools" / "update_locked_figures_golden.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    """Both books' locks, rendered once for the module."""
    return lf.render_all(tmp_path_factory.mktemp("locked-figures"))


# ── the golden and the committed locks ───────────────────────────────────────

def test_the_golden_covers_exactly_the_committed_locks():
    assert sorted(GOLDEN) == sorted(lf.BOOKS)
    for name, path in lf.BOOKS.items():
        assert sorted(GOLDEN[name]) == sorted(lf.locks_in(path)), (
            f"{name}: the golden's locks are not the book's. A lock taken since, or an "
            "input completed, is added with tools/update_locked_figures_golden.py")
    assert [len(GOLDEN[b]) for b in ("demo.db", "frozen_book.db")] == [6, 5]


@pytest.mark.parametrize("book, quarter", CASES)
def test_every_figure_the_locks_report_prints_is_the_goldens(book, quarter, rendered):
    moved = lf.differences(GOLDEN[book][quarter], rendered[book][quarter])
    assert not moved, (
        f"{book} {quarter}: {len(moved)} line(s) of locked figures differ from the golden. "
        "A locked figure moves only by a restatement its lock records and its cover names, "
        "or under a new rule recorded in the locks taken after it (CLAUDE.md, 'Locked "
        "figures'). Golden -> rendered:\n  " + "\n  ".join(moved))


@pytest.mark.parametrize("book, quarter", CASES)
def test_each_entry_was_taken_under_the_locks_own_records(book, quarter):
    """The golden names the records each lock carried when its figures were read. A
    lock restated without the golden being regenerated fails here."""
    _end, payload = lf.locks_in(lf.BOOKS[book])[quarter]
    assert GOLDEN[book][quarter]["state"] == lf.lock_state(payload)


def test_the_two_books_shared_locks_print_the_same_figures():
    """The frozen book was built from demo.db and holds the same five locks: one
    report, read from two books."""
    for quarter, entry in GOLDEN["frozen_book.db"].items():
        assert entry == GOLDEN["demo.db"][quarter], quarter


def test_no_entry_is_empty_and_the_pending_quarter_holds_fewer():
    counts = {(b, q): lf.count_figures(GOLDEN[b][q]) for b, q in CASES}
    assert min(counts.values()) > 200, counts
    assert sum(counts.values()) == 3804, "the count the golden was written with"
    # Q3 2026 waits on French's September, so its factor and benchmark sections print a
    # pending line and no table.
    q3 = GOLDEN["demo.db"]["2026Q3"]
    assert q3["state"]["waiting_on"] == ["ff5_developed_exus", "ff5_us", "umd"]
    assert q3["sections"]["Factor Profile"]["tables"] == []
    assert counts[("demo.db", "2026Q3")] < counts[("demo.db", "2026Q2")]


def test_the_tiles_are_the_keys_the_performance_page_prints():
    from src import reports
    page = (ROOT / "pages" / "2_Performance.py").read_text(encoding="utf-8")
    read = set(re.findall(r"_qf\[[\"'](\w+)[\"']\]", page))
    assert read == set(reports.QUARTER_TILE_KEYS)
    for book, quarter in CASES:
        assert set(GOLDEN[book][quarter]["tiles"]) == read, (book, quarter)


def test_the_locked_sections_are_the_ones_built_inside_the_lock():
    """The six headings compared are the template's sections less the two the cover
    names as not locked and the methodology page."""
    from src.cache import UNLOCKED_SECTIONS
    template = lf.TEMPLATE.read_text(encoding="utf-8")
    import html
    heads = [" ".join(html.unescape(re.sub(r"<[^>]+>", "", h)).split())
             for h in re.findall(r"<h2[^>]*>(.*?)</h2>", template, flags=re.S)]
    assert sorted(heads) == sorted(lf.LOCKED + lf.NOT_LOCKED)
    assert [h for h in lf.NOT_LOCKED if any(h.startswith(u) for u in UNLOCKED_SECTIONS)] == [
        "Macro Context", "Asset Evaluation: Bitcoin Case Study"]


# ── a restatement record is said on the cover ────────────────────────────────

# Each restatement record a lock can carry, and the function that says it on the cover.
# A record with no entry here fails: a restatement nobody is told about.
RULES = ("rule", "inputs_rule", "benchmark_rule")


def _notes():
    from src import cache
    from src.input_lock import ETF_METADATA, HYG
    return {
        ("lot_rebuild",): cache.lot_rebuild_note,
        ("price_corrections",): cache.filled_closes_note,
        ("gap_corrections",): cache.gap_corrections_note,
        ("input_corrections", HYG): cache.input_corrections_note,
        ("input_corrections", ETF_METADATA): cache.style_box_withdrawn_note,
    }


def _record_keys(records: dict) -> "list[tuple]":
    keys = []
    for name, value in records.items():
        if name in RULES:
            continue
        if name == "input_corrections":
            keys.extend((name, sub) for sub in value)
        else:
            keys.append((name,))
    return keys


@pytest.mark.parametrize("book", sorted(lf.BOOKS))
def test_every_restatement_record_a_lock_carries_is_said_on_its_cover(
        book, rendered, tmp_path, monkeypatch):
    import src.db as db
    from src.cache import get_quarter_snapshot
    copy = tmp_path / book
    shutil.copyfile(lf.BOOKS[book], copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    notes, said = _notes(), 0
    for quarter, entry in GOLDEN[book].items():
        snap = get_quarter_snapshot(quarter)[0]
        for key in _record_keys(entry["state"]["records"]):
            assert key in notes, (
                f"{book} {quarter} carries the record {'.'.join(key)} and no cover "
                "sentence is registered for it")
            sentence = notes[key](snap)
            assert sentence, (book, quarter, key)
            assert sentence in rendered[book][quarter]["cover"], (book, quarter, key)
            said += 1
    assert said >= 15, "premise: the committed locks carry restatement records"


def test_a_record_with_no_cover_sentence_is_caught():
    """The contrast for the check above: a record key nothing registers."""
    assert _record_keys({"rule": "quarter_end", "method_change": {"restated_on": "x"}}) == [
        ("method_change",)]
    assert ("method_change",) not in _notes()


# ── how a figure is told from wording ────────────────────────────────────────

def _m(text: str) -> str:
    return lf.OPEN + text + lf.CLOSE


def test_text_typed_in_the_template_is_not_a_figure():
    section = (f"<p>Sum of effects reconciles within 1 bp. Cash drag: <strong>{_m('-12 bps')}"
               f"</strong> across the {_m('12')} sleeves.</p>")
    assert lf.figures_of(section) == {"tables": [], "text": ["-12 bps", "12"]}


def test_a_number_in_a_computed_sentence_is_a_figure_and_its_words_are_not():
    one = lf.figures_of("<p>" + _m("The sleeve loads on Mkt-RF at 0.97 (t = 45.12).") + "</p>")
    two = lf.figures_of("<p>" + _m("With t = 45.12, its market loading is 0.97, as found.")
                        + "</p><p>" + _m("A sentence with no number.") + "</p>")
    assert one == two == {"tables": [], "text": ["0.97", "45.12"]}


def test_a_significance_label_is_a_word_for_the_t_statistic_beside_it():
    from src.prose_helpers import significance_label
    labels = lf.significance_labels()
    assert significance_label(3.0) in labels and len(labels) == 4
    sentence = f"The HML loading of 0.083 (t = 2.34) is {significance_label(2.34)}."
    assert "5% level" in sentence
    assert lf.numbers_in(sentence) == ["0.083", "2.34"]


def test_a_name_that_holds_a_number_is_not_a_figure():
    assert lf.numbers_in("The portfolio trailed the S&P 500 by 290 bps, in Q2 2026, on "
                         "FF5 and 10Y-2Y terms.") == ["290 bps", "2026"]


def test_a_number_keeps_its_sign_its_dollar_sign_and_its_unit():
    assert lf.numbers_in("+52 bps, −0.812, -64 bps, $1,358, 40.5x, 6.0 yrs, 12.23%, "
                         "June 30, 2026, 1999–2000") == [
        "+52 bps", "−0.812", "-64 bps", "$1,358", "40.5x", "6.0 yrs", "12.23%", "30",
        "2026", "1999", "2000"]


def test_a_table_cell_is_compared_whole_and_its_row_label_is_not():
    table = ("<table class='data-table'><thead><tr><th>Factor</th><th>Loading</th>"
             "<th>Sig.</th></tr></thead><tbody>"
             f"<tr><td>{_m('HML')}</td><td>{_m('0.083')}</td><td>{_m('**')}</td></tr>"
             f"<tr><td>{_m('SMB')}</td><td>{_m('0.177')}</td><td>{_m('')}</td></tr>"
             "</tbody></table>")
    assert lf.figures_of(table) == {"tables": [[["0.083", "**"], ["0.177", ""]]], "text": []}
    renamed = table.replace("HML", "Value").replace("Loading", "Beta")
    assert lf.figures_of(renamed) == lf.figures_of(table), "a label is a name"
    trades = ("<table class='data-table'><thead><tr><th>Date</th><th>Ticker</th></tr></thead>"
              f"<tbody><tr><td>{_m('2025-05-01')}</td><td>{_m('VOO')}</td></tr></tbody></table>")
    assert lf.figures_of(trades)["tables"] == [[["2025-05-01", "VOO"]]], "a trade is all data"


def test_a_table_whose_first_column_is_not_classed_fails():
    table = ("<table class='data-table'><thead><tr><th>Holding</th><th>Value</th></tr>"
             f"</thead><tbody><tr><td>{_m('VOO')}</td><td>{_m('$1')}</td></tr></tbody></table>")
    with pytest.raises(AssertionError, match="first column is 'Holding'"):
        lf.figures_of(table)


def test_a_computed_number_that_reaches_the_page_unmarked_fails():
    """The premise the reading rests on: every computed value is marked. One that a
    template filter rebuilt as plain text would read as wording; the harness refuses
    it, because the template does not type that number."""
    typed = lf.template_numbers()
    assert {"1985", "0.10", "0.05", "0.01"} <= typed, "premise: numbers the template types"
    assert lf.figures_of("<p>Brinson-Fachler 1985</p>", typed_ok=typed)["text"] == []
    with pytest.raises(AssertionError, match="reached the page unmarked"):
        lf.figures_of("<p>Portfolio return 12.23%</p>", typed_ok=typed)


def test_the_marks_change_nothing_else_in_the_render(tmp_path):
    """The reading rests on this: the marked render is the report the app renders,
    character for character, with the two invisible marks around computed values.
    Every lock of the frozen book, rendered both ways."""
    with_marks, without = {}, {}
    lf.render_book("frozen_book.db", tmp_path, html_out=with_marks)
    assert lf.render_book("frozen_book.db", tmp_path, marked=False, html_out=without) == {}
    assert sorted(with_marks) == sorted(without) == sorted(GOLDEN["frozen_book.db"])
    for quarter, html in with_marks.items():
        assert html.count(lf.OPEN) == html.count(lf.CLOSE) > 400, quarter
        assert lf.OPEN not in without[quarter] and lf.CLOSE not in without[quarter]
        assert html.replace(lf.OPEN, "").replace(lf.CLOSE, "") == without[quarter], quarter


def test_marking_leaves_a_value_its_comparisons_and_refuses_what_it_cannot_mark():
    marked = lf.mark({"bps": -12.5, "count": 2, "status": "failed", "ok": True, "none": None,
                      "rows": [{"beta": "0.083"}], "pair": ("a", 1)})
    assert marked["bps"] < 0 and marked["count"] > 1 and marked["status"] == "failed"
    assert marked["ok"] is True and marked["none"] is None
    assert marked["rows"][0]["beta"].__html__() == lf.OPEN + "0.083" + lf.CLOSE
    assert marked["count"].__html__() == lf.OPEN + "2" + lf.CLOSE
    assert marked["status"].__html__() == lf.OPEN + "failed" + lf.CLOSE
    assert lf.mark("a < b").__html__() == lf.OPEN + "a &lt; b" + lf.CLOSE
    with pytest.raises(TypeError, match="cannot mark"):
        lf.mark({"frame": object()})


def test_differences_names_each_figure_that_moved():
    was = GOLDEN["frozen_book.db"]["2026Q2"]
    now = json.loads(json.dumps(was))
    assert lf.differences(was, now) == []
    now["tiles"]["alpha_sp_str"] = "-289 bps"
    now["sections"]["Factor Profile"]["tables"][0][2][3] = "*"
    now["sections"]["Performance"]["text"][0] = "9.9%"
    lines = lf.differences(was, now)
    assert lines[0] == "Performance tile alpha_sp_str: '-290 bps' -> '-289 bps'"
    assert any(line.startswith("Factor Profile, table 1, row 3: ") for line in lines)
    assert any(line.startswith("Performance, text: no longer printed ['1.5%']; now printed "
                               "['9.9%']") for line in lines)
    assert len(lines) == 3


# ── wording moves nothing ────────────────────────────────────────────────────

def test_rewording_the_factor_prose_moves_no_figure(tmp_path, monkeypatch):
    """Every sentence of the factor prose reordered, with words added: the golden still
    holds, because the numbers the sentences print are the same."""
    import src.factors as factors
    from src import reports
    prose = reports.build_factor_prose

    def reworded(*args, **kwargs):
        return ["In other words: " + s for s in reversed(prose(*args, **kwargs))]

    monkeypatch.setattr(reports, "build_factor_prose", reworded)
    monkeypatch.setattr(factors, "em_disclosure", lambda: "Another sentence altogether.")
    again = lf.render_book("frozen_book.db", tmp_path)
    for quarter, entry in GOLDEN["frozen_book.db"].items():
        assert lf.differences(entry, again[quarter]) == [], quarter


# ── the proof: a method parameter, alone and carried as a restatement ────────

def _ten_percent_cut_off_moved_to_twenty(p: float) -> str:
    """factors.sig_marker with its one-star cut-off at 0.20 in place of 0.10."""
    return "***" if p < 0.01 else "**" if p < 0.05 else "*" if p < 0.20 else ""


def _with_a_record(source: Path, dest: Path, quarters) -> None:
    """A copy of a book whose named locks each carry one more restatement record."""
    shutil.copyfile(source, dest)
    os.chmod(dest, 0o644)
    con = sqlite3.connect(dest)
    try:
        with con:
            for quarter in quarters:
                (text,), = con.execute("SELECT snapshot_data FROM quarter_snapshots WHERE "
                                       "quarter_id = ?", (quarter,)).fetchall()
                blob = json.loads(text)
                blob["method_change"] = {"restated_on": "2026-10-14",
                                         "what": "the one-star cut-off"}
                assert con.execute("UPDATE quarter_snapshots SET snapshot_data = ? WHERE "
                                   "quarter_id = ?", (json.dumps(blob), quarter)).rowcount == 1
    finally:
        con.close()


def test_a_moved_cut_off_fails_and_the_same_change_carried_as_a_restatement_passes(
        tmp_path, monkeypatch):
    from src import reports
    tool, book = _tool(), "frozen_book.db"
    golden = {book: GOLDEN[book]}
    monkeypatch.setattr(reports, "sig_marker", _ten_percent_cut_off_moved_to_twenty)

    # 1. The method parameter alone: the comparison fails, and names star cells.
    alone = lf.render_book(book, tmp_path)
    moved = {q: lf.differences(golden[book][q], alone[q]) for q in golden[book]}
    reached = sorted(q for q, lines in moved.items() if lines)
    assert reached == ["2025Q3", "2025Q4", "2026Q1", "2026Q2"], (
        "premise: the cut-off reaches four locks; Q2 2025 prints no p-value between "
        "0.10 and 0.20, so nothing in it moves")
    assert all(re.match(r"(Factor Profile|Benchmark Attribution), table \d, row \d: ", line)
               for lines in moved.values() for line in lines), moved
    assert any("'0.165', '']" in line and "'0.165', '*']" in line
               for line in moved["2026Q2"]), moved["2026Q2"]

    # 2. The golden's writer refuses it: the locks' own records did not change.
    with pytest.raises(SystemExit, match="figures moved in 4 lock.s. whose own records did "
                                         "not change"):
        tool.updated(golden, {book: alone})

    # 3. Carried as a restatement: each lock it moves gains a record, the writer
    #    accepts, and the comparison passes against what it wrote. The lock it does
    #    not reach gains none and is as it was.
    restated = tmp_path / "restated.db"
    _with_a_record(lf.BOOKS[book], restated, reached)
    carried = lf.render_book(book, tmp_path, book=restated)
    still = {q: lf.differences(golden[book][q], carried[q]) for q in golden[book]}
    assert still == moved, "the record alone does not make the old golden pass"
    new, report = tool.updated(golden, {book: carried})
    assert len(report) == 4 and all("moved, with its records" in line for line in report)
    for quarter in golden[book]:
        assert lf.differences(new[book][quarter], carried[quarter]) == []
    for quarter in reached:
        assert new[book][quarter]["state"]["records"]["method_change"]["restated_on"] == (
            "2026-10-14")
        assert new[book][quarter]["state"] != golden[book][quarter]["state"]
    assert new[book]["2025Q2"] == golden[book]["2025Q2"]


def test_the_writer_accepts_a_new_lock_and_a_completed_input_and_refuses_a_missing_one():
    tool = _tool()
    book = "demo.db"
    render = {q: dict(e, cover="") for q, e in json.loads(json.dumps(GOLDEN[book])).items()}
    # Nothing moved: the same golden, nothing to report.
    new, report = tool.updated({book: GOLDEN[book]}, {book: render})
    assert new == {book: GOLDEN[book]} and report == []
    # A quarter locked since is added.
    earlier = {q: e for q, e in GOLDEN[book].items() if q != "2026Q3"}
    new, report = tool.updated({book: earlier}, {book: render})
    assert report == ["demo.db 2026Q3: new lock, 228 figures"] and "2026Q3" in new[book]
    # A pending input completed: the lock's records changed, so new figures are accepted.
    done = json.loads(json.dumps(render))
    done["2026Q3"]["state"]["waiting_on"] = []
    done["2026Q3"]["sections"]["Factor Profile"]["text"].append("0.97")
    new, report = tool.updated({book: GOLDEN[book]}, {book: done})
    assert report == ["demo.db 2026Q3: 1 line(s) of figures moved, with its records"]
    # A lock the golden holds is gone from the book.
    with pytest.raises(SystemExit, match=r"no longer holds the lock\(s\) \['2026Q3'\]"):
        tool.updated({book: GOLDEN[book]}, {book: {q: e for q, e in render.items()
                                                   if q != "2026Q3"}})


def test_rewording_a_typed_number_goes_through_only_when_no_table_or_tile_moved():
    """The one known false positive: a number a sentence types. With --wording the
    writer accepts a lock whose running-text numbers alone moved; a table cell or a
    tile that moved is refused whatever is said."""
    tool, book = _tool(), "frozen_book.db"
    golden = {book: GOLDEN[book]}
    reworded = {q: dict(e, cover="") for q, e in json.loads(json.dumps(GOLDEN[book])).items()}
    text = reworded["2026Q2"]["sections"]["Benchmark Attribution"]["text"]
    assert "95%" in text, "premise: the CI sentence types its level"
    text.remove("95%")
    with pytest.raises(SystemExit, match="figures moved in 1 lock"):
        tool.updated(golden, {book: reworded})
    new, report = tool.updated(golden, {book: reworded}, wording="the CI sentence")
    assert report == ["frozen_book.db 2026Q2: 1 line(s) of running-text numbers moved by "
                      "rewording (the CI sentence)"]
    assert "95%" not in new[book]["2026Q2"]["sections"]["Benchmark Attribution"]["text"]
    for cell_or_tile in ("table", "tile"):
        also = json.loads(json.dumps(reworded))
        if cell_or_tile == "table":
            also["2026Q2"]["sections"]["Benchmark Attribution"]["tables"][0][0][3] = "*"
        else:
            also["2026Q2"]["tiles"]["alpha_bl_str"] = "+53 bps"
        with pytest.raises(SystemExit, match="figures moved in 1 lock"):
            tool.updated(golden, {book: also}, wording="the CI sentence")


def test_the_committed_golden_is_what_the_writer_writes(rendered, tmp_path):
    """The file is the writer's output for the committed books, byte for byte."""
    tool = _tool()
    new, report = tool.updated(GOLDEN, rendered)
    assert report == [] and new == GOLDEN
    out = tmp_path / "golden.json"
    tool.write(new, out)
    assert out.read_bytes() == lf.GOLDEN.read_bytes().replace(b"\r\n", b"\n")
