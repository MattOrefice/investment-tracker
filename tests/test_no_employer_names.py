"""No committed text names an employer or a workplace-plan provider, or ties a ticker to an
employer (#725, decided by the owner on November 18, 2026).

The names to look for are private. They are read from private/personal_profile.json
(``withheld_names``), so this file names none of them, and the scan of the tree runs in
personal mode only. In CI the profile is absent and that scan skips; the scanner itself is
tested below on invented names, so CI still holds the machinery.

What counts:
  * a NAME: each ``withheld_names.names`` entry, matched case-insensitively as a whole
    word, with or without a possessive ('s), and with or without spaces, hyphens, dots or
    apostrophes between its letters ("Sample Firm's", "SAMPLE FIRM", "Sample-Firm");
  * a TIE: an ``employer_stock_tickers`` entry (case-sensitive, a whole word) within two
    lines of a word beginning "employ". The ticker itself may stand: the seed files hold
    it, and tests use it as a ticker. It may not stand beside "employer".

Every tracked text file is read. A tracked SQLite book is read cell by cell (every text
value of every table), because its bytes are not text. Other binaries (the two README
images) are not read.

In place of the names, the seeds carry placeholders, written only when a row is first
created and never over an existing label (#743, tests/test_placeholder_never_overwrites.py).
The personal book keeps its real labels. ``withheld_names.account_labels`` and
``security_names`` name them, and only this census reads them: each holds a withheld
name, each is what the personal book holds, and each sits where the seed carries a
placeholder.
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TRACKER_DB = ROOT / "data" / "tracker.db"
_SEP = r"[\s'’.\-]*"
_EMPLOY = re.compile(r"(?<![A-Za-z])employ", re.I)
_TIE_WINDOW = 2


def name_pattern(name: str) -> "re.Pattern[str]":
    """A name as a whole word, any case, possessive or not, with or without separators
    between its letters."""
    stem = re.sub(r"['’]s$", "", name.strip(), flags=re.I)
    chars = [c for c in stem if c.isalnum()]
    assert chars, f"a withheld name has no letters: {name!r}"
    return re.compile(r"(?<![A-Za-z0-9])" + _SEP.join(re.escape(c) for c in chars)
                      + r"(?:['’]?s)?(?![A-Za-z0-9])", re.I)


def ticker_pattern(ticker: str) -> "re.Pattern[str]":
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(ticker) + r"(?![A-Za-z0-9_])")


def names_in(text: str, names: "list[str]") -> "list[int]":
    """1-based line numbers of ``text`` that hold any of ``names``."""
    pats = [name_pattern(n) for n in names]
    return [i for i, line in enumerate(text.splitlines(), 1)
            if any(p.search(line) for p in pats)]


def ties_in(text: str, tickers: "list[str]") -> "list[int]":
    """1-based line numbers holding one of ``tickers`` within _TIE_WINDOW lines of a word
    beginning "employ"."""
    lines = text.splitlines()
    pats = [ticker_pattern(t) for t in tickers]
    out = []
    for i, line in enumerate(lines):
        if any(p.search(line) for p in pats):
            window = lines[max(0, i - _TIE_WINDOW):i + _TIE_WINDOW + 1]
            if any(_EMPLOY.search(w) for w in window):
                out.append(i + 1)
    return out


def book_text_cells(db: Path) -> "list[tuple[str, str, str]]":
    """(table, column, text) for every text value of every table in ``db``."""
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        out = []
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        for t in tables:
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
            for row in con.execute(f'SELECT * FROM "{t}"'):
                out += [(t, c, v) for c, v in zip(cols, row) if isinstance(v, str)]
        return out
    finally:
        con.close()


def _tracked() -> "list[str]":
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                         check=True).stdout.decode("utf-8")
    return [p for p in out.split("\0") if p]


def _is_book(data: bytes) -> bool:
    return data.startswith(b"SQLite format 3\0")


def _head(path: Path) -> bytes:
    with open(path, "rb") as f:
        return f.read(16)


# ── the scanner, on invented names (runs everywhere) ──────────────────────────

INVENTED = ["Sample Firm's", "Plan Admin"]


@pytest.mark.parametrize("text", [
    "Sample Firm", "SAMPLE FIRM'S plan", "Sample Firm’s", "Sample-Firm", "SampleFirm's",
    "sample firm", "the Sample.Firm account", "PLAN ADMIN 401(k)", "PlanAdmin",
])
def test_the_scanner_finds_every_spelling_of_a_name(text):
    assert names_in(f"before\n{text}\nafter", INVENTED) == [2], text


@pytest.mark.parametrize("text", [
    "Sample Firmware", "Countersample Firm", "Samples Firm", "a plan administrator",
    "PlanAdministration",
])
def test_the_scanner_passes_a_word_that_only_contains_a_name(text):
    assert names_in(text, INVENTED) == [], text


def test_the_scanner_finds_a_ticker_beside_an_employer_and_only_there():
    lines = ["XYZ is the employer stock",          # 1: same line
             "XYZ",                                # 2: one line from line 1's word
             "-", "-", "-",
             "XYZ is a single stock",              # 6: three lines from any "employ"
             "-", "-",
             "the former employer's plan",         # 9
             "-",
             "XYZW and AXYZ",                      # 11: not the ticker
             "XYZ"]                                # 12: three lines from line 9
    assert ties_in("\n".join(lines), ["XYZ"]) == [1, 2]
    assert ties_in("an Employee's XYZ", ["XYZ"]) == [1]
    assert ties_in("XYZ\nxyz employer", ["XYZ"]) == [1], "the ticker is case-sensitive"


def test_the_scanner_reads_every_text_cell_of_a_book(tmp_path):
    book = tmp_path / "book.db"
    con = sqlite3.connect(book)
    con.execute("CREATE TABLE a (id INTEGER, label TEXT, value REAL)")
    con.execute("CREATE TABLE b (note TEXT)")
    con.executemany("INSERT INTO a VALUES (?, ?, ?)",
                    [(1, "a clean label", 1.0), (2, "the Sample Firm plan", 2.0)])
    con.execute("INSERT INTO b VALUES ('plain')")
    con.commit()
    con.close()
    assert _is_book(book.read_bytes())
    hits = [(t, c) for t, c, v in book_text_cells(book) if names_in(v, INVENTED)]
    assert hits == [("a", "label")]


# ── the tree and the books, against the private names (personal mode) ─────────

def _withheld() -> dict:
    from src.config import IS_DEMO
    from src.personal_profile import _PROFILE_PATH, load_withheld_names
    if IS_DEMO:
        pytest.skip("the withheld names are private: personal mode only")
    if not _PROFILE_PATH.exists():
        pytest.skip("no private profile on this machine")
    w = load_withheld_names(is_demo=False)
    assert w["problem"] is None, w["problem"]
    assert w["names"], "withheld_names.names is empty"
    return w


def test_no_tracked_file_names_an_employer_or_ties_its_stock():
    w = _withheld()
    named, tied, read = [], [], 0
    for rel in _tracked():
        path = ROOT / rel
        if not path.is_file():
            continue
        data = path.read_bytes()
        if _is_book(data):
            continue                     # read cell by cell below
        if b"\0" in data[:8192]:
            assert path.suffix.lower() in {".png"}, f"an unread binary: {rel}"
            continue
        text = data.decode("utf-8", errors="replace")
        read += 1
        named += [f"{rel}:{n}" for n in names_in(text, w["names"])]
        tied += [f"{rel}:{n}" for n in ties_in(text, w["employer_stock_tickers"])]
    assert read > 400, f"the scan read only {read} files"
    # Locations only: the message names no name.
    assert named == [], f"lines naming a withheld name: {named}"
    assert tied == [], f"lines tying an employer's stock to an employer: {tied}"


def test_no_committed_book_names_an_employer():
    w = _withheld()
    books = [ROOT / rel for rel in _tracked()
             if (ROOT / rel).is_file() and _is_book(_head(ROOT / rel))]
    assert {b.name for b in books} >= {"demo.db", "frozen_book.db"}, books
    for book in books:
        cells = book_text_cells(book)
        assert len(cells) > 1000, (book.name, len(cells))
        hits = sorted({(t, c) for t, c, v in cells if names_in(v, w["names"])})
        assert hits == [], (book.name, hits)


def test_every_private_label_holds_a_withheld_name():
    """The overlay's labels are what the seeds withhold, so each must be one the scan
    looks for; a label holding none would mean a name the list is missing."""
    w = _withheld()
    labels = list(w["account_labels"].values()) + list(w["security_names"].values())
    assert labels, "withheld_names holds no labels"
    assert [x for x in labels if not names_in(x, w["names"])] == []


def test_the_private_labels_are_the_personal_books():
    """The private labels are the ones the personal book holds. No start writes them
    (#743): the book keeps them because a placeholder never overwrites. Read-only:
    tracker.db is not written."""
    w = _withheld()
    if not TRACKER_DB.exists() or TRACKER_DB.stat().st_size == 0:
        pytest.skip("no personal book")
    con = sqlite3.connect(f"file:{TRACKER_DB.as_posix()}?mode=ro", uri=True)
    try:
        for pseudo, label in w["account_labels"].items():
            got = con.execute("SELECT display_name FROM accounts WHERE pseudonym = ?",
                              (pseudo,)).fetchall()
            assert got == [(label,)], pseudo
        for ticker, name in w["security_names"].items():
            got = con.execute("SELECT name FROM securities WHERE ticker = ?",
                              (ticker,)).fetchall()
            assert got == [(name,)], ticker
    finally:
        con.close()


def test_every_private_label_sits_where_the_seed_carries_a_placeholder():
    """Each withheld label replaces a seed value marked as a placeholder, and each
    placeholder stands for a withheld label. An unmarked seed value would reach the
    personal book over its real label on the next start (#743)."""
    from tests.test_placeholder_never_overwrites import placeholder_marks
    w = _withheld()
    accounts, securities = placeholder_marks()
    assert set(w["account_labels"]) == accounts
    assert set(w["security_names"]) == securities


# ── the withheld-names reader ─────────────────────────────────────────────────
# The overlay this file held until #743 (bootstrap_personal_db writing these labels into
# the personal book) is gone. Its two tests moved to tests/test_placeholder_never_overwrites.py:
# a fresh book gets the placeholders with or without withheld_names, and a start
# leaves an existing book's labels as they are.

def _single_stock() -> str:
    """The household seed's one single-stock ticker, read from the seed so this file
    types no ticker the census looks for."""
    import csv
    with open(ROOT / "data" / "seed" / "securities_household.csv", newline="",
              encoding="utf-8") as f:
        rows = [r["symbol"] for r in csv.DictReader(f) if r["sleeve_category"] == "single_stock"]
    assert len(rows) == 1, rows
    return rows[0]


STOCK = _single_stock()
_SYNTHETIC = {"names": ["Sample Firm"], "employer_stock_tickers": ["XYZ"],
              "account_labels": {"acct_wkpl_01": "Sample Firm plan",
                                 "acct_wkpl_02": "Sample Firm 401(k)"},
              "security_names": {STOCK: "Sample Firm Inc."},
              "source": "test", "problem": None}


def test_load_withheld_names_reads_and_refuses(tmp_path, monkeypatch):
    import json

    import src.personal_profile as pp
    profile = tmp_path / "personal_profile.json"
    monkeypatch.setattr(pp, "_PROFILE_PATH", profile)
    assert pp.load_withheld_names(is_demo=True)["names"] == []
    assert "does not exist" in pp.load_withheld_names(is_demo=False)["problem"]
    profile.write_text(json.dumps({"ordinary_income": 1}), encoding="utf-8")
    assert "no withheld_names" in pp.load_withheld_names(is_demo=False)["problem"]
    body = {k: v for k, v in _SYNTHETIC.items() if k not in ("source", "problem")}
    body["names"] = [{"name": "Sample Firm", "kind": "former employer"}]
    profile.write_text(json.dumps({"withheld_names": body}), encoding="utf-8")
    got = pp.load_withheld_names(is_demo=False)
    assert got["problem"] is None
    assert got["names"] == ["Sample Firm"]
    assert got["account_labels"] == _SYNTHETIC["account_labels"]
    for bad in ({**body, "names": []}, {**body, "names": ["Sample Firm"]},
                {**body, "employer_stock_tickers": "XYZ"},
                {**body, "account_labels": {"acct_wkpl_01": ""}}):
        profile.write_text(json.dumps({"withheld_names": bad}), encoding="utf-8")
        got = pp.load_withheld_names(is_demo=False)
        assert got["problem"] and got["account_labels"] == {}, bad
