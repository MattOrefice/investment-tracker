"""A placeholder never overwrites (#743, decided by the owner on November 21, 2026).

The seeds name no employer (#725). Where they would, they carry a neutral placeholder,
marked as one: two account labels in src/seed/household_accounts.py's _ACCOUNTS, and
the single stock's name in data/seed/securities_household.csv (`name_is_placeholder`).
A placeholder is written only when its row is first created. Neither the account seed
nor the loader writes one over an existing label or name, with or without the private
profile's withheld_names, which now feeds only the census
(tests/test_no_employer_names.py). An unmarked seed value is the seed's own and still
reaches an existing row (#707).

Until #743 a start read withheld_names and wrote its labels over the book's. A start
without the key wrote the neutral labels into the personal book, and they stayed until
the next start with the key.

Pinned:
  * in CI, on books built here, offline:
    - a fresh book gets the placeholders, with or without withheld_names;
    - a start on a book holding its own labels leaves every account label and security
      name byte for byte, with or without withheld_names;
    - an unmarked seed value still reaches an existing row;
    - nothing outside the census reads withheld_names, and a start runs without it;
  * in personal mode, on scratch copies of the personal book: a start without
    withheld_names, and one with it, leaves every account label and security name
    byte-identical, and the personal book itself is untouched. Prices and dividends may
    move on a start; the network is blocked here, so nothing is fetched.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TRACKER_DB = ROOT / "data" / "tracker.db"
SEED_CSV = ROOT / "data" / "seed" / "securities_household.csv"


def _marked(row: dict) -> bool:
    return (row.get("name_is_placeholder") or "").strip().lower() == "true"


def _seed_rows() -> "list[dict]":
    with open(SEED_CSV, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r["symbol"].strip()]


def placeholders() -> "tuple[dict[str, str], dict[str, str]]":
    """The seeds' placeholder values, read from the seeds:
    ({pseudonym: label}, {ticker: name})."""
    from src.seed.household_accounts import _ACCOUNTS
    accounts = {a[2]: a[3] for a in _ACCOUNTS if a[5]}
    securities = {r["symbol"].strip(): r["description"].strip()
                  for r in _seed_rows() if _marked(r)}
    return accounts, securities


def placeholder_marks() -> "tuple[set[str], set[str]]":
    """(pseudonyms, tickers) whose seed value is marked as a placeholder."""
    accounts, securities = placeholders()
    return set(accounts), set(securities)


def _profile(tmp_path: Path, withheld: bool) -> Path:
    """A private profile with synthetic withheld_names, or with no such key. The labels
    differ from every label a book here holds, so a start that wrote them would show."""
    accounts, securities = placeholders()
    body: dict = {}
    if withheld:
        body["withheld_names"] = {
            "names": [{"name": "Sample Firm", "kind": "former employer"}],
            "employer_stock_tickers": [],
            "account_labels": {p: f"Sample Firm plan {p}" for p in accounts},
            "security_names": {t: "Sample Firm Inc." for t in securities},
        }
    p = tmp_path / f"profile-{'with' if withheld else 'without'}.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    return p


def _fresh(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    book = d / "tracker.db"
    book.write_bytes(b"")                       # 0-byte: the fresh-clone case
    return book


def _start(monkeypatch, book: Path, profile: Path) -> None:
    """One personal start on ``book``, offline, with the private profile at ``profile``."""
    import src.personal_profile as pp
    from src import bootstrap
    from tests.conftest import block_network
    monkeypatch.setattr("src.db.DB_PATH", book)
    monkeypatch.setattr("src.db._migrated_paths", set())
    monkeypatch.setattr("src.bootstrap._bootstrapped", set())
    monkeypatch.setattr(pp, "_PROFILE_PATH", profile)
    block_network(monkeypatch)
    bootstrap.bootstrap_personal_db()


def _labels(book: Path):
    """Every account label and security name, as bytes."""
    with closing(sqlite3.connect(f"file:{book.as_posix()}?mode=ro", uri=True)) as c:
        accounts = c.execute(
            "SELECT account_id, CAST(pseudonym AS BLOB), CAST(name AS BLOB), "
            "CAST(display_name AS BLOB) FROM accounts ORDER BY account_id").fetchall()
        securities = c.execute(
            "SELECT CAST(ticker AS BLOB), CAST(name AS BLOB) FROM securities "
            "ORDER BY ticker").fetchall()
    return accounts, securities


def _set(book: Path, sql: str, args: tuple) -> None:
    with closing(sqlite3.connect(book)) as c:
        assert c.execute(sql, args).rowcount == 1, (sql, args)
        c.commit()


WITH_OR_WITHOUT = pytest.mark.parametrize(
    "withheld", [False, True], ids=["without withheld_names", "with withheld_names"])


def test_the_seeds_mark_their_placeholders():
    accounts, securities = placeholders()
    assert accounts and securities, "premise: each seed marks a placeholder"
    from src.seed.household_accounts import _ACCOUNTS
    assert all(isinstance(a[5], bool) for a in _ACCOUNTS)
    assert {r["name_is_placeholder"] for r in _seed_rows()} == {"true", "false"}


# ── on books built here (CI) ──────────────────────────────────────────────────

@WITH_OR_WITHOUT
def test_a_fresh_book_gets_the_placeholders(withheld, tmp_path, monkeypatch):
    book = _fresh(tmp_path)
    _start(monkeypatch, book, _profile(tmp_path, withheld))
    accounts, securities = placeholders()
    with closing(sqlite3.connect(book)) as c:
        for pseudo, label in accounts.items():
            assert c.execute("SELECT display_name FROM accounts WHERE pseudonym = ?",
                             (pseudo,)).fetchall() == [(label,)], pseudo
        for ticker, name in securities.items():
            assert c.execute("SELECT name FROM securities WHERE ticker = ?",
                             (ticker,)).fetchall() == [(name,)], ticker


@WITH_OR_WITHOUT
def test_a_start_leaves_every_label_and_name_as_the_book_holds_them(withheld, tmp_path,
                                                                     monkeypatch):
    book = _fresh(tmp_path)
    _start(monkeypatch, book, _profile(tmp_path, False))
    accounts, securities = placeholders()
    for pseudo in accounts:
        _set(book, "UPDATE accounts SET display_name = ? WHERE pseudonym = ?",
             (f"The book's own label for {pseudo}", pseudo))
    for ticker in securities:
        _set(book, "UPDATE securities SET name = ? WHERE ticker = ?",
             ("The book's own name", ticker))
    before = _labels(book)
    _start(monkeypatch, book, _profile(tmp_path, withheld))
    assert _labels(book) == before


def test_an_unmarked_seed_value_still_reaches_an_existing_row(tmp_path, monkeypatch):
    """#707's rule stands for every value that is not a placeholder: a label or name
    corrected in the seed reaches a book seeded before the correction."""
    from src.seed.household_accounts import _ACCOUNTS
    book = _fresh(tmp_path)
    _start(monkeypatch, book, _profile(tmp_path, False))
    account = next(a for a in _ACCOUNTS if not a[5])
    row = next(r for r in _seed_rows() if not _marked(r))
    _set(book, "UPDATE accounts SET display_name = 'a stale label' WHERE pseudonym = ?",
         (account[2],))
    _set(book, "UPDATE securities SET name = 'a stale name' WHERE ticker = ?",
         (row["symbol"].strip(),))
    _start(monkeypatch, book, _profile(tmp_path, False))
    with closing(sqlite3.connect(book)) as c:
        assert c.execute("SELECT display_name FROM accounts WHERE pseudonym = ?",
                         (account[2],)).fetchall() == [(account[3],)]
        assert c.execute("SELECT name FROM securities WHERE ticker = ?",
                         (row["symbol"].strip(),)).fetchall() == [(row["description"].strip(),)]


def test_only_the_census_reads_withheld_names(tmp_path, monkeypatch):
    import src.personal_profile as pp

    def _refuse(*_a, **_k):
        raise AssertionError("a start read withheld_names")

    monkeypatch.setattr(pp, "load_withheld_names", _refuse)
    _start(monkeypatch, _fresh(tmp_path), _profile(tmp_path, True))
    # A read is a call to the reader or the key as a string; a comment naming it is not.
    read = re.compile(r"load_withheld_names\s*\(|[\"']withheld_names[\"']")
    files = [p for top in ("src", "pages", "tools") for p in (ROOT / top).rglob("*.py")]
    readers = sorted(p.relative_to(ROOT).as_posix() for p in files + [ROOT / "app.py"]
                     if read.search(p.read_text(encoding="utf-8")))
    assert len(files) > 100, len(files)
    assert readers == ["src/personal_profile.py"], readers


# ── on scratch copies of the personal book (personal mode) ────────────────────

def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@WITH_OR_WITHOUT
def test_a_start_on_the_personal_book_changes_no_label(withheld, tmp_path, monkeypatch):
    from src.personal_profile import _PROFILE_PATH
    if not TRACKER_DB.exists() or TRACKER_DB.stat().st_size == 0:
        pytest.skip("no personal book (CI)")
    if not _PROFILE_PATH.exists():
        pytest.skip("no private profile on this machine")
    sha = _sha(TRACKER_DB)
    d = tmp_path / "data"
    d.mkdir()
    copy = d / "tracker.db"
    shutil.copyfile(TRACKER_DB, copy)
    copy.chmod(0o644)
    profile = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
    assert "withheld_names" in profile, "premise: the private profile holds withheld_names"
    if not withheld:
        del profile["withheld_names"]
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(profile), encoding="utf-8")
    before = _labels(copy)
    _start(monkeypatch, copy, p)
    assert _labels(copy) == before
    assert _sha(TRACKER_DB) == sha
