"""Every figure a committed quarter lock's report prints, read off the render (#525).

A lock holds prices, dividends and its provider inputs. It does not hold the method the
report computes with: the benchmark construction, the regression's weights, a
significance cut-off. A change to one of those re-renders every locked quarter, and
until now nothing compared the result with what the quarter was reported as. This
module renders each committed lock's report and reads every figure off it;
tests/test_locked_figures_golden.py compares that with the committed golden
(tests/fixtures/locked_figures_golden.json), and tools/update_locked_figures_golden.py
rewrites the golden.

WHAT IS COMPARED: the six sections built inside the lock (reports builds them under
snapshot_price_context) and the Performance page's locked tiles. Macro Context and
Asset Evaluation are computed at each render and are not in it; neither are the cover
and the methodology page, which are text about the report.

HOW A FIGURE IS TOLD FROM WORDING. The report is rendered with every value its code
computed wrapped in two invisible marks; text typed in the template carries none.
Then, in each locked section:

  * a cell of a data table is compared whole, as printed: the number, its sign and
    unit, a significance star, a drift status, a trade's date and ticker. The row's
    label (the sleeve, period or factor in the first column) is a name and is not;
  * in everything else the code computed (a tile, a sentence, a caption) every NUMBER
    is compared, with its sign, dollar sign and unit, as a sorted list per section.
    The words around a number, and the order of the sentences, are free to change;
  * a LABEL, words that state a classification of a figure ("statistically significant
    at the 1% level", "in line with benchmark", "outperforming"), is compared by the
    CLASS it states and never by its words: the level, the side, the reading. The
    report declares each one where it chooses the words
    (prose_helpers.label(family, cls, words)), and the render made here carries the
    class in two more invisible marks around the words. So moving a cut-off moves a
    class and fails; rewording a label moves nothing. A number inside a label's words
    (the "1%" above) is part of the words, not a figure;
  * a figure printed IN WORDS (#541) is compared too, whole and as printed, as a sorted
    list per section beside the numbers: an ordinal (the CAPE's "99th percentile"), a
    date whose month is a word ("June 30, 2026", "June 2026"), a number spelled out
    ("three", "third"). The number scan reads a date's day and year but not its month,
    and no ordinal at all, so a moved percentile or month passed it. Words inside a
    label are the label's, as above;
  * a QUARTER'S LABEL ("Q2 2026") is compared whole, as printed, as a sorted list per
    section (#553). The number scan reads its year and takes "Q2" for a name, as it
    takes "FF5", so a report that named the wrong quarter of the same year passed it;
  * text typed in the template is never compared: it is wording by construction.

So a number a sentence types in its own source ("95% CI", or spelled out, "the one
equity region") counts as a figure: the render cannot tell it from a computed one.
Charts are images and are not read.

WHAT A LABEL IS, and the limit of this. A label is any word, clause or sentence the
report's code picks by testing a computed figure. The families are
prose_helpers.LABEL_CLASSES. A classification printed in a table cell (a significance
star, a drift status) is not read as a label: the cell is compared whole, words and
all. Nothing can find a word the code chooses without declaring it, so a new one
added without label() is wording to this comparison.
"""
from __future__ import annotations

import html as html_lib
import json
import os
import re
import shutil
import sqlite3
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOKS = {"demo.db": ROOT / "data" / "demo.db",
         "frozen_book.db": ROOT / "tests" / "fixtures" / "frozen_book.db"}
GOLDEN = ROOT / "tests" / "fixtures" / "locked_figures_golden.json"
TEMPLATE = ROOT / "templates" / "quarterly_report.html"

# The report's sections, by the heading each prints. LOCKED are built inside the lock
# (tests/test_cover_names_what_the_lock_holds.py ties the builders to the lock).
LOCKED = ("Executive Summary", "Holdings & Allocation", "Performance",
          "Brinson-Fachler Attribution", "Factor Profile", "Benchmark Attribution")
NOT_LOCKED = ("Macro Context", "Asset Evaluation: Bitcoin Case Study",
              "Methodology & Data Sources")
# A data table's first column: a name for the row, or ledger data like every other cell.
LABEL_FIRST = ("Sleeve", "Period", "Factor")
DATA_FIRST = ("Date",)

# Two invisible characters around every computed value in the marked render.
OPEN, CLOSE = "⁣", "⁤"
# Three more around every label: LABEL_OPEN family=class LABEL_SAYS the words LABEL_CLOSE.
LABEL_OPEN, LABEL_SAYS, LABEL_CLOSE = "\u2061", "\u2062", "\u2060"
MARKS = (OPEN, CLOSE, LABEL_OPEN, LABEL_SAYS, LABEL_CLOSE)
_LABEL = re.compile(LABEL_OPEN + "([^" + LABEL_SAYS + "]*)" + LABEL_SAYS + "(.*?)" + LABEL_CLOSE,
                    flags=re.S)

# A number as printed: sign, dollar sign, thousands separators, decimals, unit. Not part
# of a name ("FF5", "10Y", "Q2"), and a hyphen inside a date or a range is not a sign.
NUMBER = re.compile(
    r"(?<![\w.,$])[+\-−]?\$?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
    r"(?:%|\s?bps?\b|x\b|\s?yrs?\b)?(?![\w])")


def significance_labels() -> "list[str]":
    """Every label prose_helpers.significance_label can return: words for a t-statistic."""
    from src.prose_helpers import significance_label
    return sorted({significance_label(t) for t in (0.0, 1.0, 1.7, 2.0, 2.6, 3.0, 50.0)},
                  key=len, reverse=True)


# A name that holds a number, where a computed sentence spells it out.
NAMES = ("S&P 500",)


def numbers_in(text: str) -> "list[str]":
    """Every number in a computed text, as printed, with every label's words and the
    names above set aside. A label is found by its marks in a marked render, and a
    significance label by its words in plain text."""
    text = _LABEL.sub(" ", text)
    for words in significance_labels() + list(NAMES):
        text = text.replace(words, " ")
    return [" ".join(m.group(0).split()) for m in NUMBER.finditer(text)]


# A figure printed in words (#541): an ordinal in digits, a date with its month as a word
# (day optional, year required, so "may" the verb is not one), or a number spelled out.
_MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|"
           "December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec")
_SPELLED = ("zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
            "fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|"
            "sixty|seventy|eighty|ninety|hundred|thousand|million|billion|first|second|third|"
            "fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth")
IN_WORDS = re.compile(
    r"(?<![\w.,$])\d+(?:st|nd|rd|th)\b"
    rf"|\b(?:{_MONTHS})\.?(?:\s+\d{{1,2}},?)?\s+\d{{4}}\b"
    rf"|\b(?i:{_SPELLED})\b")


# A quarter's label as the report prints it (#553): "Q2 2026".
QUARTER = re.compile(r"\bQ[1-4]\s+\d{4}\b")


def quarters_in(text: str) -> "list[str]":
    """Every quarter's label in a computed text (#553), as printed, with every label's
    words set aside as numbers_in sets them aside."""
    text = _LABEL.sub(" ", text)
    return [" ".join(m.group(0).split()) for m in QUARTER.finditer(text)]


def in_words_in(text: str) -> "list[str]":
    """Every figure printed in words in a computed text (#541), as printed, with every
    label's words and the names above set aside as numbers_in sets them aside."""
    text = _LABEL.sub(" ", text)
    for words in significance_labels() + list(NAMES):
        text = text.replace(words, " ")
    return [" ".join(m.group(0).split()) for m in IN_WORDS.finditer(text)]


def labelled(family: str, cls: str, words: str) -> str:
    """What prose_helpers.label returns while this harness renders: the words, with
    the class they state carried in the marks around them."""
    return LABEL_OPEN + f"{family}={cls}" + LABEL_SAYS + words + LABEL_CLOSE


def labels_in(text: str) -> "list[str]":
    """Every label in a computed text, as "family=class", in the order printed."""
    found = [m.group(1) for m in _LABEL.finditer(text)]
    assert text.count(LABEL_OPEN) == text.count(LABEL_CLOSE) == len(found), (
        "a label's marks are unbalanced or nested: a label holds words only")
    return found


# ── the marked render ────────────────────────────────────────────────────────

def _marked(value: str) -> str:
    from markupsafe import escape
    return OPEN + str(escape(value)) + CLOSE


class _Text(str):
    def __html__(self) -> str:
        return _marked(str.__str__(self))


class _Int(int):
    def __html__(self) -> str:
        return _marked(str(int(self)))


class _Float(float):
    def __html__(self) -> str:
        return _marked(str(float(self)))


def mark(value):
    """``value`` with every string and number in it wrapped so the template prints it
    between the two marks. Comparisons, truth and arithmetic are the value's own."""
    import numbers
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, str):
        return _Text(value)
    if isinstance(value, numbers.Integral):
        return _Int(int(value))
    if isinstance(value, numbers.Real):
        return _Float(float(value))
    if type(value) is dict:
        return {k: mark(v) for k, v in value.items()}
    if type(value) in (list, tuple):
        return type(value)(mark(v) for v in value)
    raise TypeError(f"the report's template is given a {type(value).__name__}, which this "
                    "harness cannot mark: every computed value must be markable, or a "
                    "figure it prints would be read as wording")


class _MarkingTemplate:
    def __init__(self, template):
        self._template = template

    def render(self, **context):
        css = context.pop("css_content", "")
        return self._template.render(css_content=css, **{k: mark(v) for k, v in context.items()})


class _MarkingEnv:
    def __init__(self, env):
        self._env = env

    def get_template(self, name):
        return _MarkingTemplate(self._env.get_template(name))


# ── reading a render ─────────────────────────────────────────────────────────

class _Section(HTMLParser):
    """One section's tables and running text. A data table's cells are kept by row; a
    metric table's cards and everything outside tables is running text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: "list[dict]" = []
        self.text: "list[str]" = []
        self._table = None          # the data table being read
        self._cell = None           # the th/td being read: [tag, text parts]
        self._row = None
        self._skip = 0              # inside <style>/<script>

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class") or ""
        if tag in ("style", "script"):
            self._skip += 1
        elif tag == "table" and "data-table" in cls.split():
            self._table = {"headers": [], "rows": []}
        elif self._table is not None and tag == "tr":
            self._row = []
        elif self._table is not None and tag in ("th", "td"):
            self._cell = [tag, []]

    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            self._skip -= 1
        elif self._table is not None and tag in ("th", "td") and self._cell is not None:
            kind, parts = self._cell
            text = " ".join("".join(parts).split())
            if kind == "th":
                self._table["headers"].append(text)
            elif self._row is not None:
                self._row.append(text)
            self._cell = None
        elif self._table is not None and tag == "tr":
            if self._row:
                self._table["rows"].append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data):
        if self._skip:
            return
        if self._cell is not None:
            self._cell[1].append(data)
        elif self._table is None:
            self.text.append(data)


def _unmarked(text: str) -> str:
    """``text`` as the report prints it: no mark, and each label as its words."""
    text = _LABEL.sub(lambda m: m.group(2), text)
    assert LABEL_OPEN not in text and LABEL_SAYS not in text and LABEL_CLOSE not in text, (
        "a label's marks are unbalanced")
    return text.replace(OPEN, "").replace(CLOSE, "")


def _spans(text: str) -> "tuple[list[str], str]":
    """(the computed values in ``text``, the text typed around them)."""
    spans = re.findall(re.escape(OPEN) + "(.*?)" + re.escape(CLOSE), text, flags=re.S)
    typed = re.sub(re.escape(OPEN) + ".*?" + re.escape(CLOSE), " ", text, flags=re.S)
    return spans, typed


def sections_of(report_html: str) -> "dict[str, str]":
    """The rendered report cut at its <h2> headings: {heading: that section's html}."""
    body = re.sub(r"<style.*?</style>", "", report_html, flags=re.S)
    heads = list(re.finditer(r"<h2[^>]*>(.*?)</h2>", body, flags=re.S))
    out = {}
    for i, m in enumerate(heads):
        title = " ".join(_unmarked(html_lib.unescape(re.sub(r"<[^>]+>", "", m.group(1)))).split())
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        assert title not in out, f"two sections are headed {title!r}"
        out[title] = body[m.end():end]
    return out


def cover_of(report_html: str) -> str:
    """The cover as text: everything the report prints before its first section."""
    body = re.sub(r"<style.*?</style>", "", report_html, flags=re.S)
    cover = body.split("<h2", 1)[0]
    return " ".join(_unmarked(html_lib.unescape(re.sub(r"<[^>]+>", " ", cover))).split())


def _template_text() -> str:
    """The template's own typed text: Jinja tags, comments and markup removed."""
    src = TEMPLATE.read_text(encoding="utf-8")
    src = re.sub(r"\{#.*?#\}", " ", src, flags=re.S)
    src = re.sub(r"\{%.*?%\}", " ", src, flags=re.S)
    src = re.sub(r"\{\{.*?\}\}", " ", src, flags=re.S)
    src = re.sub(r"<style.*?</style>", " ", src, flags=re.S)
    return html_lib.unescape(re.sub(r"<[^>]+>", " ", src))


def template_numbers() -> "set[str]":
    """Every number TYPED in the template's own text, and every figure in words it
    types (#541). The harness checks that one it finds outside the marks is one of
    these, so a computed figure that reached the page unmarked cannot pass as wording."""
    text = _template_text()
    return set(numbers_in(text)) | set(in_words_in(text)) | set(quarters_in(text))


def figures_of(section_html: str, *, typed_ok: "set[str] | None" = None) -> dict:
    """One locked section's figures: {"tables": [[row, ...], ...], "text": [number, ...],
    "labels": ["family=class", ...], "in_words": [figure, ...], "quarters": [label, ...]}.
    Each table is its rows of cells as printed, the label column left out; "text" is every
    number in the section's other computed text, "labels" every class a label in that text
    states, "in_words" every figure that text prints in words (#541) and "quarters" every
    quarter's label it prints (#553), each sorted."""
    parser = _Section()
    parser.feed(section_html)
    parser.close()
    tables = []
    for table in parser.tables:
        first = _unmarked(table["headers"][0]) if table["headers"] else ""
        if first not in LABEL_FIRST + DATA_FIRST:
            raise AssertionError(f"a data table whose first column is {first!r} is not "
                                 "classed as a label column or a data column")
        skip = 1 if first in LABEL_FIRST else 0
        tables.append([[_unmarked(cell) for cell in row[skip:]] for row in table["rows"]])
    computed, typed = _spans(" ".join(parser.text))
    text = sorted(n for span in computed for n in numbers_in(html_lib.unescape(span)))
    labels = sorted(c for span in computed for c in labels_in(span))
    in_words = sorted(w for span in computed for w in in_words_in(html_lib.unescape(span)))
    quarters = sorted(q for span in computed for q in quarters_in(html_lib.unescape(span)))
    assert not labels_in(typed), "a label reached the page outside a computed value"
    if typed_ok is not None:
        stray = sorted((set(numbers_in(typed)) | set(in_words_in(typed))
                        | set(quarters_in(typed))) - typed_ok)
        assert not stray, (f"numbers printed outside the marks that the template does not "
                           f"type: {stray}. A computed value reached the page unmarked.")
    return {"tables": tables, "text": text, "labels": labels, "in_words": in_words,
            "quarters": quarters}


# ── a lock's own records ─────────────────────────────────────────────────────

# What a lock's payload holds besides its data: its rules and its restatement records.
_DATA_KEYS = ("adj_close", "close", "inputs", "inputs_through", "inputs_pending", "gaps")


def lock_state(payload_text: str) -> dict:
    """What a lock says about itself: every rule and restatement record it carries, and
    the inputs it is still waiting on. A figure the lock's report prints may move only
    together with this (CLAUDE.md, "Locked figures")."""
    blob = json.loads(payload_text)
    return {"records": {k: blob[k] for k in sorted(blob) if k not in _DATA_KEYS},
            "waiting_on": sorted(blob.get("inputs_pending") or {})}


def locks_in(book: Path) -> "dict[str, tuple[str, str]]":
    """{quarter_id: (the quarter's last day, the stored payload)} for a book's locks."""
    con = sqlite3.connect(f"file:{Path(book).as_posix()}?mode=ro", uri=True)
    try:
        return {q: (d, text) for q, d, text in con.execute(
            "SELECT quarter_id, snapshot_date, snapshot_data FROM quarter_snapshots "
            "ORDER BY quarter_id")}
    finally:
        con.close()


def _quarter_start(end: date) -> date:
    """The last day of the quarter before the one ending on ``end``: the report's start."""
    first = end.replace(month=end.month - 2, day=1)
    return first - timedelta(days=1)


def _day_after_last_close(book: Path) -> date:
    con = sqlite3.connect(f"file:{Path(book).as_posix()}?mode=ro", uri=True)
    try:
        last, = con.execute("SELECT MAX(price_date) FROM prices WHERE ticker = 'SPY'").fetchone()
    finally:
        con.close()
    return date.fromisoformat(last) + timedelta(days=1)


# ── rendering a book's locks ─────────────────────────────────────────────────

def render_book(name: str, scratch_dir: Path, *, book: "Path | None" = None,
                marked: bool = True, html_out: "dict | None" = None) -> dict:
    """Render every lock of the named committed book and read its figures.

    Runs on a scratch copy (never the committed file), offline, charts and the PDF step
    left out, with today pinned to the day after the book's last close. Returns
    {quarter_id: {"state", "sections", "tiles", "cover"}}: the first three are what the
    golden keeps (KEPT); the cover's text is for checks that read its sentences.

    ``html_out``, a dict, is filled with each report's html. ``marked=False`` renders
    without the marks and reads nothing: {} comes back, and the html is the report as
    the app renders it, for the check that the marks change nothing else."""
    import socket

    import pytest

    from tests.conftest import pin_today, unpin_leftovers

    source = Path(book) if book is not None else BOOKS[name]
    copy = Path(scratch_dir) / f"locked_figures_{name}"
    shutil.copyfile(source, copy)
    os.chmod(copy, 0o644)
    typed_ok = template_numbers()
    out = {}

    def _offline(*_a, **_k):
        raise OSError("network blocked: locked figures are rendered offline")

    with pytest.MonkeyPatch.context() as mp:
        import src.db as db
        import src.macro as macro
        import src.prices as prices
        import src.prose_helpers as prose_helpers
        from src import reports
        mp.setattr(socket, "getaddrinfo", _offline)
        mp.setattr(prices._SESSION, "get", _offline)
        mp.setattr(macro, "fetch_fred_series", _offline)
        mp.setattr(db, "DB_PATH", copy)
        mp.setattr(db, "_migrated_paths", set())
        mp.setattr(db, "_RUNTIME_CACHE", None)
        prices._reset_trailing_memo()
        mp.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
        mp.setattr(reports, "_render_pdf", lambda html: html.encode("utf-8"))
        make_env = reports._make_report_env
        if marked:
            mp.setattr(reports, "_make_report_env", lambda: _MarkingEnv(make_env()))
            mp.setattr(prose_helpers, "_label_reader", labelled)
        pin_today(mp, _day_after_last_close(copy))
        try:
            for quarter, (end_iso, payload) in locks_in(source).items():
                end = date.fromisoformat(end_iso)
                start = _quarter_start(end)
                html = reports.generate_quarterly_report_bytes(
                    start.isoformat(), end.isoformat(), is_demo=True).decode("utf-8")
                if html_out is not None:
                    html_out[quarter] = html
                if not marked:
                    continue
                found = sections_of(html)
                unknown = sorted(set(found) - set(LOCKED) - set(NOT_LOCKED))
                assert not unknown, f"{name} {quarter}: sections not classed: {unknown}"
                missing = sorted(set(LOCKED) - set(found))
                assert not missing, f"{name} {quarter}: locked sections not rendered: {missing}"
                label = f"Q{(end.month - 1) // 3 + 1} {end.year}"
                tiles = reports.locked_quarter_figures(label, start.isoformat(), end.isoformat())
                out[quarter] = {
                    "cover": cover_of(html),
                    "state": lock_state(payload),
                    "sections": {s: figures_of(found[s], typed_ok=typed_ok) for s in LOCKED},
                    "tiles": {k: tiles[k] for k in reports.QUARTER_TILE_KEYS},
                }
        finally:
            unpin_leftovers()
    return out


# What the golden keeps of each lock's render.
KEPT = ("state", "sections", "tiles")


def render_all(scratch_dir: Path) -> dict:
    return {name: render_book(name, scratch_dir) for name in BOOKS}


def golden_entry(rendered_lock: dict) -> dict:
    return {k: rendered_lock[k] for k in KEPT}


def count_figures(entry: dict) -> int:
    """How many figures one lock's entry holds: table cells, text numbers and tiles."""
    return (len(entry["tiles"])
            + sum(len(row) for s in entry["sections"].values()
                  for table in s["tables"] for row in table)
            + sum(len(s["text"]) for s in entry["sections"].values()))


def count_labels(entry: dict) -> int:
    """How many labels one lock's entry holds."""
    return sum(len(s.get("labels", ())) for s in entry["sections"].values())


def holds_labels(entry: dict) -> bool:
    """Whether an entry was written since labels were read (#525): every section
    carries its list, empty or not."""
    return all("labels" in s for s in entry["sections"].values())


def count_in_words(entry: dict) -> int:
    """How many figures printed in words one lock's entry holds (#541)."""
    return sum(len(s.get("in_words", ())) for s in entry["sections"].values())


def count_quarters(entry: dict) -> int:
    """How many quarter labels one lock's entry holds (#553)."""
    return sum(len(s.get("quarters", ())) for s in entry["sections"].values())


def holds_quarters(entry: dict) -> bool:
    """Whether an entry was written since quarter labels were read (#553): every section
    carries its list, empty or not."""
    return all("quarters" in s for s in entry["sections"].values())


def holds_in_words(entry: dict) -> bool:
    """Whether an entry was written since figures in words were read (#541): every
    section carries its list, empty or not."""
    return all("in_words" in s for s in entry["sections"].values())


def load_golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))["locks"]


def _moved(was: "list[str]", now: "list[str]") -> "tuple[list[str], list[str]]":
    """(what ``was`` holds and ``now`` does not, what ``now`` holds and ``was`` does
    not), each occurrence counted."""
    gone, new = list(was), []
    for item in now:
        if item in gone:
            gone.remove(item)
        else:
            new.append(item)
    return gone, new


def differences(golden: dict, rendered: dict) -> "list[str]":
    """Every figure and every label's class that differs between one lock's golden
    entry and its render, as lines a reader can act on. Empty when none does. An entry
    written before labels, figures in words or quarter labels were read holds none, and
    they are not compared: the golden's test requires every committed entry to hold
    them."""
    out = []
    for key, was in golden["tiles"].items():
        now = rendered["tiles"].get(key)
        if now != was:
            out.append(f"Performance tile {key}: {was!r} -> {now!r}")
    for section in LOCKED:
        g, r = golden["sections"][section], rendered["sections"][section]
        if len(g["tables"]) != len(r["tables"]):
            out.append(f"{section}: {len(g['tables'])} tables -> {len(r['tables'])}")
        for t, (gt, rt) in enumerate(zip(g["tables"], r["tables"]), 1):
            if len(gt) != len(rt):
                out.append(f"{section}, table {t}: {len(gt)} rows -> {len(rt)}")
            for i, (grow, rrow) in enumerate(zip(gt, rt), 1):
                if grow != rrow:
                    out.append(f"{section}, table {t}, row {i}: {grow} -> {rrow}")
        if g["text"] != r["text"]:
            gone, new = _moved(g["text"], r["text"])
            out.append(f"{section}, text: no longer printed {gone}; now printed {new}")
        if "labels" in g and g["labels"] != r["labels"]:
            gone, new = _moved(g["labels"], r["labels"])
            out.append(f"{section}, labels: no longer stated {gone}; now stated {new}")
        if "in_words" in g and g["in_words"] != r["in_words"]:
            gone, new = _moved(g["in_words"], r["in_words"])
            out.append(f"{section}, in words: no longer printed {gone}; now printed {new}")
        if "quarters" in g and g["quarters"] != r["quarters"]:
            gone, new = _moved(g["quarters"], r["quarters"])
            out.append(f"{section}, quarters: no longer printed {gone}; now printed {new}")
    return out
