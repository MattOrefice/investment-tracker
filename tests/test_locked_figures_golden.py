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
  * the reading itself: typed text is not a figure, a table cell is compared whole,
    words and their order are free;
  * a label is held by the class it states, not its words: every committed entry holds
    each section's labels, a moved 1% cut-off fails, and every label reworded passes;
  * a figure printed in words (an ordinal, a date's month, a number spelled out) is
    read whole beside the numbers (#541): every committed entry holds each section's,
    and a CAPE percentile moved by one rank fails on its ordinal alone;
  * a quarter's label ("Q2 2026") is read whole (#553): every committed entry holds
    each section's, and a summary that names the wrong quarter fails on its label alone;
  * the proof: a significance cut-off moved from 10% to 20% fails the comparison and
    the tool refuses it; the same change carried with a record in each lock passes;
  * the personal book (#418): its locks against data/private/locked_figures_golden.json,
    which git ignores, on the same rule. The check skips where data/tracker.db is
    absent, as in CI, and fails where the book is present and the file is not.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
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
def renders(tmp_path_factory):
    """Both books' locks, rendered once for the module: (their figures, their html), each
    {book: {quarter: ...}}. A test that only reads a render reads this one: each
    rendering of the frozen book's five locks took about 9 s of the CI step (#544)."""
    scratch = tmp_path_factory.mktemp("locked-figures")
    html = {name: {} for name in lf.BOOKS}
    figures = {name: lf.render_book(name, scratch, html_out=html[name]) for name in lf.BOOKS}
    return figures, html


@pytest.fixture(scope="module")
def rendered(renders):
    """Both books' locks' figures, from the module's one render."""
    return renders[0]


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
    assert lf.figures_of(section) == {"tables": [], "text": ["-12 bps", "12"],
                                      "labels": [], "in_words": [], "quarters": []}


def test_a_number_in_a_computed_sentence_is_a_figure_and_its_words_are_not():
    one = lf.figures_of("<p>" + _m("The sleeve loads on Mkt-RF at 0.97 (t = 45.12).") + "</p>")
    two = lf.figures_of("<p>" + _m("With t = 45.12, its market loading is 0.97, as found.")
                        + "</p><p>" + _m("A sentence with no number.") + "</p>")
    assert one == two == {"tables": [], "text": ["0.97", "45.12"], "labels": [],
                          "in_words": [], "quarters": []}


def test_a_significance_label_is_a_word_for_the_t_statistic_beside_it():
    """In plain text the label's words are set aside from the numbers. (In a marked
    render the label is found by its marks: the tests below.)"""
    from src.prose_helpers import significance_label
    labels = lf.significance_labels()
    assert significance_label(3.0) in labels and len(labels) == 4
    sentence = f"The HML loading of 0.083 (t = 2.34) is {significance_label(2.34)}."
    assert "5% level" in sentence
    assert lf.numbers_in(sentence) == ["0.083", "2.34"]


def test_a_label_is_read_as_its_class_and_its_words_are_not():
    """A label's class is what the comparison holds; its words, numbers in them
    included, are not figures, and the figure beside it still is."""
    def section(words_5, words_below):
        return ("<p>" + _m("The SMB loading of 0.083 (t = 2.34) is "
                           + lf.labelled("significance", "5%", words_5) + ".")
                + "</p><p>" + _m("Duration 5.9 yrs vs 6.1 yrs ("
                                 + lf.labelled("duration_vs_benchmark", "below", words_below)
                                 + " by 0.2 yrs).") + "</p>")
    one = lf.figures_of(section("statistically significant at the 5% level", "below benchmark"))
    two = lf.figures_of(section("significant at five percent, 95 in 100", "short of the Agg"))
    assert one == two == {"tables": [], "text": ["0.083", "0.2 yrs", "2.34", "5.9 yrs",
                                                 "6.1 yrs"],
                          "labels": ["duration_vs_benchmark=below", "significance=5%"],
                          "in_words": [], "quarters": []}
    other = lf.figures_of(section("statistically significant at the 5% level",
                                  "below benchmark").replace("significance=5%",
                                                             "significance=1%"))
    assert other["labels"] == ["duration_vs_benchmark=below", "significance=1%"]
    assert other["text"] == one["text"]


def test_a_label_prints_as_its_words():
    """Outside the harness label() returns the words; in the harness the marks around
    them come off whole, so a table cell holding a label is compared as printed."""
    from src.prose_helpers import label
    assert label("duration_vs_benchmark", "in_line", "in line with benchmark") == (
        "in line with benchmark")
    marked = _m("Duration (" + lf.labelled("duration_vs_benchmark", "in_line",
                                           "in line with benchmark") + ")")
    assert lf._unmarked(marked) == "Duration (in line with benchmark)"
    table = ("<table class='data-table'><thead><tr><th>Sleeve</th><th>Status</th></tr>"
             f"</thead><tbody><tr><td>{_m('US')}</td><td>{_m(lf.labelled('sign', 'zero', 'Flat'))}"
             "</td></tr></tbody></table>")
    assert lf.figures_of(table) == {"tables": [[["Flat"]]], "text": [], "labels": [],
                                    "in_words": [], "quarters": []}


def test_a_label_outside_a_computed_value_or_with_unbalanced_marks_fails():
    with pytest.raises(AssertionError, match="outside a computed value"):
        lf.figures_of("<p>Typed " + lf.labelled("sign", "positive", "positive") + "</p>")
    broken = _m("is " + lf.labelled("sign", "positive", "positive")[:-1] + " here")
    with pytest.raises(AssertionError, match="unbalanced"):
        lf.figures_of("<p>" + broken + "</p>")


def test_a_label_states_only_a_declared_class():
    from src.prose_helpers import LABEL_CLASSES, label
    with pytest.raises(ValueError, match="'in line' is not a class of the label family "
                                         "'duration_vs_benchmark'"):
        label("duration_vs_benchmark", "in line", "in line with benchmark")
    with pytest.raises(KeyError):
        label("duration", "in_line", "in line with benchmark")
    for family, classes in LABEL_CLASSES.items():
        assert classes and len(set(classes)) == len(classes), family


def _label_calls() -> "list[tuple[str, int, str, object]]":
    """Every call of label() in src/: (file, line, family, class or None when the class
    is computed)."""
    calls = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "label" and len(node.args) == 3):
                family = node.args[0]
                assert isinstance(family, ast.Constant), (path.name, node.lineno)
                cls = node.args[1].value if isinstance(node.args[1], ast.Constant) else None
                calls.append((path.name, node.lineno, family.value, cls))
    return calls


def test_every_label_family_is_declared_and_stated_somewhere():
    """Each label() call names a declared family and, when it is typed, a declared
    class; and every declared family is stated by some call, so the list in
    prose_helpers is the report's labels and nothing else."""
    from src.prose_helpers import LABEL_CLASSES
    calls = _label_calls()
    assert len(calls) >= 20, "premise: the report's labels go through label()"
    for name, line, family, cls in calls:
        assert family in LABEL_CLASSES, (name, line, family)
        assert cls is None or cls in LABEL_CLASSES[family], (name, line, family, cls)
    assert {family for _n, _l, family, _c in calls} == set(LABEL_CLASSES)


def test_every_committed_entry_holds_its_labels():
    """Each section of each committed entry carries its labels, so no entry is one
    written before labels were read; and the count the golden was written with."""
    from src.prose_helpers import LABEL_CLASSES
    stated = set()
    for book, quarter in CASES:
        entry = GOLDEN[book][quarter]
        assert lf.holds_labels(entry), (book, quarter)
        for s in entry["sections"].values():
            assert s["labels"] == sorted(s["labels"])
            for found in s["labels"]:
                family, cls = found.split("=")
                assert cls in LABEL_CLASSES[family], (book, quarter, found)
                stated.add(found)
    assert sum(lf.count_labels(GOLDEN[b][q]) for b, q in CASES) == 264, (
        "the count the golden was written with")
    # What the committed locks state. A label no lock states (a reading that would
    # contradict a tilt, a duration above the benchmark) is held from the first lock
    # that does.
    assert {"significance=1%", "significance=5%", "significance=10%", "significance=none",
            "sign=positive", "sign=negative", "versus_benchmark=out", "versus_benchmark=under",
            "cape_regime=elevated", "duration_vs_benchmark=in_line",
            "duration_vs_benchmark=below", "us_market_reading=near_full", "tilt_reading=shown",
            "term_reading=duration", "fi_alpha_reading=not_from_zero",
            "benchmark_beta=near_full", "active_return_reading=selection"} == stated


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
    assert lf.figures_of(table) == {"tables": [[["0.083", "**"], ["0.177", ""]]],
                                    "text": [], "labels": [], "in_words": [], "quarters": []}
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
    # A figure in words too (#541): an ordinal carries no number the scan reads.
    for unmarked in ("in the 99th percentile", "a reading for June 2026", "in Q2 2026"):
        with pytest.raises(AssertionError, match="reached the page unmarked"):
            lf.figures_of(f"<p>CAPE is {unmarked}</p>", typed_ok=typed)


def test_the_marks_change_nothing_else_in_the_render(renders, tmp_path):
    """The reading rests on this: the marked render is the report the app renders,
    character for character, with the two invisible marks around computed values and
    the class of each label carried around its words. Every lock of the frozen book,
    rendered both ways: the marked render is the module's (#544). A comparison the
    report makes on a label's words would make the two differ here."""
    with_marks, without = renders[1]["frozen_book.db"], {}
    assert lf.render_book("frozen_book.db", tmp_path, marked=False, html_out=without) == {}
    assert sorted(with_marks) == sorted(without) == sorted(GOLDEN["frozen_book.db"])
    for quarter, html in with_marks.items():
        assert html.count(lf.OPEN) == html.count(lf.CLOSE) > 400, quarter
        assert html.count(lf.LABEL_OPEN) == html.count(lf.LABEL_CLOSE) > 20, quarter
        assert not any(mark in without[quarter] for mark in lf.MARKS), quarter
        assert lf._unmarked(html) == without[quarter], quarter


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
    labels = now["sections"]["Performance"]["labels"]
    assert labels == ["duration_vs_benchmark=in_line"], "premise"
    labels[0] = "duration_vs_benchmark=below"
    in_words = now["sections"]["Executive Summary"]["in_words"]
    assert in_words[0] == "99th", "premise"
    in_words[0] = "98th"
    quarters = now["sections"]["Executive Summary"]["quarters"]
    assert quarters == ["Q2 2026", "Q2 2026"], "premise"
    quarters[0] = "Q3 2026"
    lines = lf.differences(was, now)
    assert lines[0] == "Performance tile alpha_sp_str: '-290 bps' -> '-289 bps'"
    assert any(line.startswith("Factor Profile, table 1, row 3: ") for line in lines)
    assert any(line.startswith("Performance, text: no longer printed ['1.5%']; now printed "
                               "['9.9%']") for line in lines)
    assert ("Performance, labels: no longer stated ['duration_vs_benchmark=in_line']; now "
            "stated ['duration_vs_benchmark=below']") in lines
    assert ("Executive Summary, in words: no longer printed ['99th']; now printed ['98th']"
            in lines)
    assert ("Executive Summary, quarters: no longer printed ['Q2 2026']; now printed "
            "['Q3 2026']") in lines
    assert len(lines) == 6
    # An entry written before labels, or figures in words, were read is compared on its
    # figures alone.
    before = json.loads(json.dumps(was))
    for s in before["sections"].values():
        del s["labels"]
        del s["in_words"]
        del s["quarters"]
    assert not lf.holds_labels(before) and not lf.holds_in_words(before)
    assert not lf.holds_quarters(before)
    figures_only = lf.differences(before, now)
    assert len(figures_only) == 3 and not any(
        ", labels: " in line or ", in words: " in line or ", quarters: " in line
        for line in figures_only)


# ── wording moves nothing ────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def reworded(tmp_path_factory):
    """The frozen book rendered once with both rewordings the two tests below make: the
    factor prose and the EM disclosure each reordered with words added, and every label's
    words replaced (the significance words in prose_helpers too). Each test expects no
    figure to move, so they share one render (#544). Returns (figures, html, the labels
    stated, the disclosures said).

    The disclosure is patched where the report calls it: src.reports imports
    em_disclosure by name, so a patch on src.factors never reached the report (#562)."""
    import src.prose_helpers as prose_helpers
    from src import reports
    prose, original, stated = reports.build_factor_prose, lf.labelled, []
    em, em_said = reports.em_disclosure, []

    def reworded_prose(*args, **kwargs):
        return ["In other words: " + s for s in reversed(prose(*args, **kwargs))]

    def reworded_em():
        said = "Put another way: " + " ".join(reversed(re.split(r"(?<=\.) ", em())))
        em_said.append(said)
        return said

    def reworded_label(family, cls, words):
        stated.append((family, cls))
        return original(family, cls, f"(said otherwise, 1 of 2 ways: {words.upper()})")

    html = {}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(reports, "build_factor_prose", reworded_prose)
        mp.setattr(reports, "em_disclosure", reworded_em)
        mp.setattr(prose_helpers, "SIGNIFICANCE_WORDS", {
            "1%": "significant at one percent", "5%": "significant at five percent",
            "10%": "significant only at ten percent", "none": "indistinguishable from nothing"})
        mp.setattr(lf, "labelled", reworded_label)
        figures = lf.render_book("frozen_book.db", tmp_path_factory.mktemp("reworded"),
                                 html_out=html)
    return figures, html, stated, em_said


def test_rewording_the_factor_prose_moves_no_figure(reworded):
    """Every sentence of the factor prose and of the EM disclosure reordered, with words
    added: the golden still holds, because the numbers the sentences print are the same.
    The disclosure keeps the one figure it types, "the one equity region", which the
    golden holds in words; a disclosure that dropped it would move the golden (#562)."""
    from html import unescape
    figures, html, _stated, em_said = reworded
    assert em_said and len(set(em_said)) == 1, (
        "premise: the report called the reworded EM disclosure, the same text each time")
    for quarter in GOLDEN["frozen_book.db"]:
        assert em_said[0] in unescape(html[quarter]), (
            f"premise: {quarter}'s report printed the reworded EM disclosure")
    assert any("In other words: " in h for h in html.values()), (
        "premise: the factor prose was reworded")
    for quarter, entry in GOLDEN["frozen_book.db"].items():
        assert lf.differences(entry, figures[quarter]) == [], quarter


# ── a label: its class is held, its words are free ──────────────────────────

def test_a_moved_one_percent_cut_off_fails_on_the_labels_it_moves(tmp_path, monkeypatch):
    """significance_label's 1% cut-off moved from 2.58 to 2.30: two labels in two locks
    now state 1% where they stated 5%, the comparison fails on those labels and on
    nothing else (no table cell reads this cut-off), and the writer refuses it."""
    import src.prose_helpers as prose_helpers
    tool, book = _tool(), "frozen_book.db"
    monkeypatch.setattr(prose_helpers, "SIGNIFICANCE_CUT_OFFS",
                        (("1%", 2.30), ("5%", 1.96), ("10%", 1.65)))
    moved_cut_off = lf.render_book(book, tmp_path)
    moved = {q: lf.differences(GOLDEN[book][q], moved_cut_off[q]) for q in GOLDEN[book]}
    assert {q: lines for q, lines in moved.items() if lines} == {
        "2025Q2": ["Benchmark Attribution, labels: no longer stated ['significance=5%']; "
                   "now stated ['significance=1%']"],
        "2026Q2": ["Factor Profile, labels: no longer stated ['significance=5%']; now "
                   "stated ['significance=1%']"]}
    with pytest.raises(SystemExit, match="figures moved in 2 lock.s. whose own records did "
                                         "not change"):
        tool.updated({book: GOLDEN[book]}, {book: moved_cut_off})
    with pytest.raises(SystemExit, match="figures moved in 2 lock"):
        tool.updated({book: GOLDEN[book]}, {book: moved_cut_off}, wording="the labels")


def test_every_label_reworded_moves_nothing(reworded):
    """The significance words rewritten in prose_helpers, and every label's words in
    every lock replaced as it is stated, each keeping its class: the golden holds."""
    figures, html, stated, _em_said = reworded
    assert len(stated) >= sum(lf.count_labels(GOLDEN["frozen_book.db"][q])
                              for q in GOLDEN["frozen_book.db"])
    assert any("(said otherwise, 1 of 2 ways: " in h for h in html.values()), (
        "premise: the labels were reworded")
    for quarter, entry in GOLDEN["frozen_book.db"].items():
        assert lf.differences(entry, figures[quarter]) == [], quarter


def test_the_allocation_commentary_states_which_side_of_its_benchmark_weight(monkeypatch):
    """allocation_weight is a label no committed lock states: no quarter's top
    allocation effect has passed the 15 bps the commentary needs. So the golden will
    hold it from the first lock that does, and its class is tested here."""
    import pandas as pd

    import src.prose_helpers as prose_helpers
    from src import reports
    monkeypatch.setattr(prose_helpers, "_label_reader", lf.labelled)
    monkeypatch.setattr(reports, "_chart_b64", lambda *a, **k: None)
    for w_p, w_b, cls in ((0.12, 0.08, "overweight"), (0.04, 0.08, "underweight")):
        bf = pd.DataFrame([{"sleeve": "US Small Cap", "w_p": w_p, "w_b": w_b, "r_p": 0.05,
                            "r_b": 0.04, "allocation_effect": 0.003, "selection_effect": 0.001,
                            "total_effect": 0.004}])
        bf.attrs.update(no_benchmark_sleeves=[], price_gaps=[], benchmark_gaps=[],
                        cash_drag=0.0)
        monkeypatch.setattr(reports, "brinson_fachler_period", lambda *a, **k: bf)
        text = reports._build_attribution_section("2026-01-01", "2026-03-31")[
            "alloc_commentary"]
        assert lf.labels_in(text) == [f"allocation_weight={cls}"]
        assert lf._unmarked(text).startswith(f"US Small Cap: {cls} vs benchmark")


def test_the_writer_records_labels_once_and_refuses_a_moved_class():
    tool, book = _tool(), "frozen_book.db"
    render = {q: dict(e, cover="") for q, e in json.loads(json.dumps(GOLDEN[book])).items()}
    # An entry from before labels were read gains them, with nothing else moving.
    before = json.loads(json.dumps(GOLDEN[book]))
    for entry in before.values():
        for s in entry["sections"].values():
            del s["labels"]
    new, report = tool.updated({book: before}, {book: render})
    assert new == {book: GOLDEN[book]}
    assert report[0] == ("frozen_book.db 2025Q2: 26 labels recorded for the first time, "
                         "no figure moved")
    assert len(report) == 5
    # The first recording of labels lets no figure through: an entry from before labels
    # were read whose figures moved too, under unchanged records, is refused.
    moved_too = json.loads(json.dumps(render))
    moved_too["2026Q2"]["tiles"]["alpha_sp_str"] = "-289 bps"
    with pytest.raises(SystemExit, match="figures moved in 1 lock"):
        tool.updated({book: before}, {book: moved_too})
    # A class that moves under unchanged records is refused, whatever is said.
    flipped = json.loads(json.dumps(render))
    flipped["2026Q2"]["sections"]["Performance"]["labels"] = ["duration_vs_benchmark=above"]
    with pytest.raises(SystemExit, match="figures moved in 1 lock"):
        tool.updated({book: GOLDEN[book]}, {book: flipped})
    with pytest.raises(SystemExit, match="figures moved in 1 lock"):
        tool.updated({book: GOLDEN[book]}, {book: flipped}, wording="the duration line")


# ── a figure printed in words (#541) ──────────────────────────────────────────

def test_a_figure_printed_in_words_is_read_whole_and_the_words_around_it_are_not():
    """The CAPE sentence as the report prints it: its percentile is an ordinal and its
    reading's month a word, so the number scan read "40.9x" and the years and nothing
    of either. Each is read whole now, and rewording around them moves nothing."""
    said = _m("CAPE stands at 40.9x, in the 99th percentile (June 2026, read October 13, "
              "2026 from multpl.com). The window ran May 2, 2025 to June 30, 2026.")
    again = _m("At 40.9x, CAPE ranks 99th on its history; read October 13, 2026, for June "
               "2026, over the window from May 2, 2025 to June 30, 2026.")
    one, two = lf.figures_of(f"<p>{said}</p>"), lf.figures_of(f"<p>{again}</p>")
    assert one == two == {"tables": [], "labels": [],
                          "text": ["13", "2", "2025", "2026", "2026", "2026", "30", "40.9x"],
                          "in_words": ["99th", "June 2026", "June 30, 2026", "May 2, 2025",
                                       "October 13, 2026"], "quarters": []}
    # A spelled-out number is one; a name holding a digit, "may" the verb and a
    # label's words are not.
    assert lf.in_words_in("Three sleeves in Q2 2026 on FF5 terms may move.") == ["Three"]
    assert lf.figures_of("<p>" + _m("is " + lf.labelled(
        "significance", "none", "not distinguishable from zero")) + "</p>")["in_words"] == []


def test_every_committed_entry_holds_its_figures_in_words():
    """Each section of each committed entry carries its figures in words, so no entry is
    one written before they were read; and the count the golden was written with. Every
    lock's executive summary prints the CAPE percentile as one ordinal."""
    ordinals = set()
    for book, quarter in CASES:
        entry = GOLDEN[book][quarter]
        assert lf.holds_in_words(entry), (book, quarter)
        for s in entry["sections"].values():
            assert s["in_words"] == sorted(s["in_words"])
        said = [w for w in entry["sections"]["Executive Summary"]["in_words"]
                if re.fullmatch(r"\d+(st|nd|rd|th)", w)]
        assert len(said) == 1, (book, quarter, said)
        ordinals.update(said)
    assert ordinals == {"97th", "98th", "99th"}
    assert sum(lf.count_in_words(GOLDEN[b][q]) for b, q in CASES) == 168, (
        "the count the golden was written with")


def test_a_cape_percentile_moved_by_one_rank_fails_on_its_ordinal_alone(
        tmp_path, monkeypatch):
    """The proof for #541: the CAPE percentile computed one rank lower. No number, cell,
    tile or label moves (97th to 99th less one stays elevated), so before the golden
    read figures in words this passed. It fails now, on each lock's ordinal and nothing
    else, and the writer refuses it."""
    from src import reports
    tool, book = _tool(), "frozen_book.db"
    ranked = reports.percentile
    monkeypatch.setattr(reports, "percentile", lambda series, value: ranked(series, value) - 1)
    lower = lf.render_book(book, tmp_path)
    moved = {q: lf.differences(GOLDEN[book][q], lower[q]) for q in GOLDEN[book]}
    for quarter, lines in moved.items():
        was = [w for w in GOLDEN[book][quarter]["sections"]["Executive Summary"]["in_words"]
               if w.endswith(("st", "nd", "rd", "th"))]
        now = f"{int(was[0][:-2]) - 1}th"
        assert lines == [f"Executive Summary, in words: no longer printed {was}; now "
                         f"printed [{now!r}]"], (quarter, lines)
    # The same move against entries without their figures in words: nothing differs,
    # which is what the golden held before #541.
    without = json.loads(json.dumps(GOLDEN[book]))
    for entry in without.values():
        for s in entry["sections"].values():
            del s["in_words"]
    assert all(lf.differences(without[q], lower[q]) == [] for q in without)
    with pytest.raises(SystemExit, match="figures moved in 5 lock.s. whose own records did "
                                         "not change"):
        tool.updated({book: GOLDEN[book]}, {book: lower})


def test_the_writer_records_figures_in_words_once_and_lets_a_reworded_one_through():
    tool, book = _tool(), "frozen_book.db"
    render = {q: dict(e, cover="") for q, e in json.loads(json.dumps(GOLDEN[book])).items()}
    # An entry from before figures in words were read gains them, nothing else moving.
    before = json.loads(json.dumps(GOLDEN[book]))
    for entry in before.values():
        for s in entry["sections"].values():
            del s["in_words"]
    new, report = tool.updated({book: before}, {book: render})
    assert new == {book: GOLDEN[book]}
    assert report[0] == ("frozen_book.db 2025Q2: 16 figures in words recorded for the first "
                         "time, no figure moved")
    assert len(report) == 5
    # The first recording lets no figure through.
    moved_too = json.loads(json.dumps(render))
    moved_too["2026Q2"]["tiles"]["alpha_sp_str"] = "-289 bps"
    with pytest.raises(SystemExit, match="figures moved in 1 lock"):
        tool.updated({book: before}, {book: moved_too})
    # A typed number word reworded away ("the one equity region") is running text: it is
    # refused, and goes through with --wording.
    reworded = json.loads(json.dumps(render))
    words = reworded["2026Q2"]["sections"]["Factor Profile"]["in_words"]
    assert "one" in words, "premise: the cap-weight sentence types 'one'"
    words.remove("one")
    with pytest.raises(SystemExit, match="figures moved in 1 lock"):
        tool.updated({book: GOLDEN[book]}, {book: reworded})
    new, report = tool.updated({book: GOLDEN[book]}, {book: reworded},
                               wording="the cap-weight sentence")
    assert report == ["frozen_book.db 2026Q2: 1 line(s) of running-text numbers moved by "
                      "rewording (the cap-weight sentence)"]


# ── a quarter's label (#553) ─────────────────────────────────────────────────

def test_a_quarter_label_is_read_whole_and_a_name_is_not():
    """"Q2 2026" was read as 2026 alone: the number scan takes "Q2" for a name, as it
    takes "FF5". The label is read whole now, beside the numbers, sorted, and rewording
    or reordering around it moves nothing; a quarter number with no year is not a label,
    and neither is one inside a label's words."""
    said = _m("Portfolio returned 11.71% in Q2 2026, after Q4 2025, on FF5 terms; Q2 was "
              "the quarter.")
    again = _m("After Q4 2025, the portfolio returned 11.71% in Q2 2026. FF5 terms, Q2's "
               "quarter.")
    one, two = lf.figures_of(f"<p>{said}</p>"), lf.figures_of(f"<p>{again}</p>")
    assert one == two == {"tables": [], "labels": [], "in_words": [],
                          "text": ["11.71%", "2025", "2026"],
                          "quarters": ["Q2 2026", "Q4 2025"]}
    assert lf.quarters_in("Q1 2026, Q4  2025, 2026Q2, Q5 2026, FY2026") == [
        "Q1 2026", "Q4 2025"]
    worded = _m("is " + lf.labelled("sign", "positive", "up since Q2 2026"))
    assert lf.figures_of(f"<p>{worded}</p>")["quarters"] == []


def test_a_quarter_label_outside_the_marks_is_refused_even_with_its_year_typed():
    """A computed label that reached the page unmarked is refused like a number, and on
    its own account: here the template is taken to type its year, so the number scan
    alone would let "Q2 2026" through."""
    typed = lf.template_numbers() | {"2026"}
    with pytest.raises(AssertionError, match=r"reached the page unmarked"):
        lf.figures_of("<p>Returns for Q2 2026</p>", typed_ok=typed)
    assert lf.figures_of("<p>Returns for 2026</p>", typed_ok=typed)["quarters"] == []


def test_every_committed_entry_holds_its_quarter_labels():
    """Each section of each committed entry carries its quarter labels, every one of them
    the lock's own quarter, and the count the golden was written with."""
    for book, quarter in CASES:
        entry = GOLDEN[book][quarter]
        assert lf.holds_quarters(entry), (book, quarter)
        own = f"Q{quarter[-1]} {quarter[:4]}"
        said = [q for s in entry["sections"].values() for q in s["quarters"]]
        assert said and set(said) == {own}, (book, quarter, said)
        assert own in entry["sections"]["Executive Summary"]["quarters"]
        for s in entry["sections"].values():
            assert s["quarters"] == sorted(s["quarters"])
    assert sum(lf.count_quarters(GOLDEN[b][q]) for b, q in CASES) == 41, (
        "the count the golden was written with")


def _next_quarter(label: str) -> str:
    q, year = label.split()
    return f"Q{int(q[1]) % 4 + 1} {year}"


def test_a_summary_that_names_the_wrong_quarter_fails_on_its_label_alone(
        tmp_path, monkeypatch):
    """The proof for #553: the executive summary names the next quarter of the same year
    (Q4 names Q1), while the report reads its own lock, so no number, cell, tile or
    label moves. Before the golden read quarter labels this passed. It fails now, on the
    labels alone, in every lock, and the writer refuses it."""
    import inspect

    from src import reports
    tool, book = _tool(), "frozen_book.db"
    real = reports._format_period_label

    def misnamed(start_date, end_date):
        label = real(start_date, end_date)
        caller = inspect.currentframe().f_back.f_code.co_name
        return _next_quarter(label) if caller == "_build_executive_summary" else label

    monkeypatch.setattr(reports, "_format_period_label", misnamed)
    wrong = lf.render_book(book, tmp_path)
    for quarter in GOLDEN[book]:
        lines = lf.differences(GOLDEN[book][quarter], wrong[quarter])
        own = f"Q{quarter[-1]} {quarter[:4]}"
        assert lines and all(", quarters: " in line for line in lines), (quarter, lines)
        assert any(line.startswith("Executive Summary, quarters: ") and
                   repr(_next_quarter(own)) in line for line in lines), (quarter, lines)
    without = json.loads(json.dumps(GOLDEN[book]))
    for entry in without.values():
        for s in entry["sections"].values():
            del s["quarters"]
    assert all(lf.differences(without[q], wrong[q]) == [] for q in without)
    with pytest.raises(SystemExit, match="figures moved in 5 lock.s. whose own records did "
                                         "not change"):
        tool.updated({book: GOLDEN[book]}, {book: wrong})


def test_the_writer_records_quarter_labels_once_and_refuses_a_moved_one():
    tool, book = _tool(), "frozen_book.db"
    render = {q: dict(e, cover="") for q, e in json.loads(json.dumps(GOLDEN[book])).items()}
    before = json.loads(json.dumps(GOLDEN[book]))
    for entry in before.values():
        for s in entry["sections"].values():
            del s["quarters"]
    new, report = tool.updated({book: before}, {book: render})
    assert new == {book: GOLDEN[book]}
    assert report[0] == ("frozen_book.db 2025Q2: 3 quarter labels recorded for the first "
                         "time, no figure moved")
    assert len(report) == 5
    moved = json.loads(json.dumps(render))
    moved["2026Q2"]["sections"]["Executive Summary"]["quarters"] = ["Q2 2026", "Q3 2026"]
    with pytest.raises(SystemExit, match="figures moved in 1 lock"):
        tool.updated({book: GOLDEN[book]}, {book: moved})


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


# ── the personal book (#418) ─────────────────────────────────────────────────

def test_every_figure_the_personal_books_locks_print_is_its_goldens(tmp_path):
    """The personal book's locks, rendered from a scratch copy of data/tracker.db,
    offline, against data/private/locked_figures_golden.json, on the committed books'
    rule (decided by the owner on October 17, 2026). Neither file is committed, so this
    skips in CI. With the book present and no file it fails: it would pass holding
    nothing."""
    if not lf.PERSONAL_BOOK.exists():
        pytest.skip("data/tracker.db is absent (CI, a fresh clone): the personal golden "
                    "is checked only where the book is")
    assert lf.PERSONAL_GOLDEN.exists(), (
        "data/tracker.db is present and there is no personal golden at "
        "data/private/locked_figures_golden.json: write it with python "
        "tools/update_locked_figures_golden.py")
    golden = lf.load_golden(lf.PERSONAL_GOLDEN)
    assert sorted(golden) == [lf.PERSONAL]
    golden = golden[lf.PERSONAL]
    rendered = lf.render_personal(tmp_path)[lf.PERSONAL]
    assert sorted(golden) == sorted(rendered), (
        "the personal golden's locks are not the book's. A lock taken since is added "
        "with tools/update_locked_figures_golden.py")
    states = {q: lf.lock_state(payload) for q, (_end, payload)
              in lf.locks_in(lf.PERSONAL_BOOK).items()}
    failed = []
    for quarter in sorted(golden):
        if golden[quarter]["state"] != states[quarter]:
            failed.append(f"{quarter}: its records changed since the golden was written. "
                          "Re-run tools/update_locked_figures_golden.py")
        moved = lf.differences(golden[quarter], rendered[quarter])
        if moved:
            failed.append(f"{quarter}: {len(moved)} line(s) of locked figures differ. "
                          "Golden -> rendered:\n    " + "\n    ".join(moved))
    assert not failed, ("the personal book's locked figures differ from its golden. A "
                        "locked figure moves only with a change in its lock's own records "
                        "(CLAUDE.md, 'Locked figures').\n  " + "\n  ".join(failed))


def test_the_personal_golden_is_never_committed():
    """data/private/ is ignored as a whole, so the personal golden is one `git add -A`
    from nowhere, and nothing under it is tracked."""
    def ignored(path: str) -> bool:
        return subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT).returncode == 0
    path = lf.PERSONAL_GOLDEN.relative_to(ROOT).as_posix()
    assert path == "data/private/locked_figures_golden.json"
    assert ignored(path)
    assert ignored("data/private/any_other_file.json")
    tracked = subprocess.run(["git", "ls-files", "data/private"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
    assert tracked == ""


def _stand_in(rendered, tmp_path, monkeypatch):
    """The frozen book standing in for the personal book, so the personal check and the
    writer's personal half run in CI, where data/tracker.db is absent: its renders as
    the personal render, its locks as the personal book's, and a personal golden under
    tmp_path. No personal figure is read."""
    render = json.loads(json.dumps(rendered["frozen_book.db"]))
    monkeypatch.setattr(lf, "PERSONAL_BOOK", lf.BOOKS["frozen_book.db"])
    monkeypatch.setattr(lf, "PERSONAL_GOLDEN", tmp_path / "private" / "golden.json")
    monkeypatch.setattr(lf, "render_personal", lambda scratch: {lf.PERSONAL: render})
    return render


def test_the_personal_check_compares_every_lock_and_fails_with_no_golden(
        rendered, tmp_path, monkeypatch):
    check = test_every_figure_the_personal_books_locks_print_is_its_goldens
    render = _stand_in(rendered, tmp_path, monkeypatch)
    with pytest.raises(AssertionError, match="there is no personal golden"):
        check(tmp_path)
    lf.PERSONAL_GOLDEN.parent.mkdir()
    _tool().write({lf.PERSONAL: {q: lf.golden_entry(e) for q, e in render.items()}},
                  lf.PERSONAL_GOLDEN)
    check(tmp_path)
    render["2026Q2"]["tiles"]["alpha_bl_str"] += "0"
    with pytest.raises(AssertionError, match=r"2026Q2: 1 line\(s\) of locked figures differ"):
        check(tmp_path)
    del render["2026Q2"]
    with pytest.raises(AssertionError, match="locks are not the book's"):
        check(tmp_path)
    monkeypatch.setattr(lf, "PERSONAL_BOOK", tmp_path / "tracker.db")
    with pytest.raises(pytest.skip.Exception):
        check(tmp_path)


def test_the_writer_holds_the_personal_book_to_the_same_rule(rendered, tmp_path, monkeypatch):
    """With a personal book present, the writer writes its locks to their own file, and
    refuses, writing neither file, when a lock's figures moved while its records did
    not. Absent, the personal golden is left as it is."""
    tool = _tool()
    committed = tmp_path / "committed.json"
    committed.write_bytes(lf.GOLDEN.read_bytes())
    monkeypatch.setattr(lf, "GOLDEN", committed)
    monkeypatch.setattr(lf, "render_all", lambda scratch: rendered)
    render = _stand_in(rendered, tmp_path, monkeypatch)
    expected = {q: lf.golden_entry(e) for q, e in render.items()}
    assert tool.main([]) == 0
    assert lf.load_golden(lf.PERSONAL_GOLDEN) == {lf.PERSONAL: expected}
    written, before = lf.PERSONAL_GOLDEN.read_bytes(), committed.read_bytes()
    assert tool.main([]) == 0 and lf.PERSONAL_GOLDEN.read_bytes() == written
    render["2026Q2"]["tiles"]["alpha_bl_str"] += "0"
    with pytest.raises(SystemExit, match=r"(?s)figures moved in 1 lock.*tracker\.db 2026Q2"):
        tool.main([])
    assert lf.PERSONAL_GOLDEN.read_bytes() == written and committed.read_bytes() == before
    render["2026Q2"]["state"]["records"]["input_corrections"] = {"stand-in": "a record"}
    assert tool.main([]) == 0
    assert lf.load_golden(lf.PERSONAL_GOLDEN)[lf.PERSONAL]["2026Q2"]["tiles"] == (
        render["2026Q2"]["tiles"])
    written = lf.PERSONAL_GOLDEN.read_bytes()
    monkeypatch.setattr(lf, "render_personal", lambda scratch: None)
    assert tool.main([]) == 0 and lf.PERSONAL_GOLDEN.read_bytes() == written
    assert committed.read_bytes() == before
