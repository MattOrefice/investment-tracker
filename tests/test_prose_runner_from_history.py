"""Runner steps 2 and 4 take step 5's rule: a later known text is already superseded (#732,
decided by the owner on November 19, 2026).

Both steps read their text from TODAY's seed, so the next edit to the seed made them
abort on a book whose text had moved on: step 2 on IDHQ, step 4 on its seed-consistency
guard. The runner stopped at step 2 on the personal book, and steps 3 to 6 never ran
through it. Each step now holds its target text and the SHA-256 of every later seed text
as data. A book at a later text is left as it is.

tests/fixtures/rationale_history.json holds every text the seed held for the ten tickers
the two steps touch, read from src/seed_securities.py's git history (public, committed
prose). Pinned:
  * in CI, on a fixture book: a later text is left byte for byte and nothing is written,
    including one where step 4's anchor still occurs and a swap would have inserted the
    old falsifier beside the new one; a text the step cannot reason about still aborts;
    the held data is the history's;
  * in personal mode, on scratch copies of the personal book: from each of those texts
    the runner completes end to end, and the book itself is untouched.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

import tools.migrate_personal_intl_rationales as step2
import tools.migrate_personal_missing_falsifiers as step4
import tools.sync_personal_prose as runner

ROOT = Path(__file__).resolve().parent.parent
TRACKER_DB = ROOT / "data" / "tracker.db"
HISTORY = json.loads((ROOT / "tests" / "fixtures" / "rationale_history.json")
                     .read_text(encoding="utf-8"))
STEP_COMMIT = {step2: "4abdb40", step4: "408ca83"}
TICKERS = {step2: ("IDHQ", "AVIV", "AVDV", "AVUV", "IEMG"),
           step4: ("VOO", "VTV", "VEA", "VGIT", "PDBC")}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text(ticker: str, commit: str) -> str:
    return next(v["text"] for v in HISTORY[ticker] if v["commit"] == commit)


def _after(ticker: str, commit: str) -> "list[dict]":
    vs = HISTORY[ticker]
    return vs[[v["commit"] for v in vs].index(commit) + 1:]


def _book(tmp_path: Path, texts: "dict[str, str | None]") -> Path:
    p = tmp_path / "fixture.db"
    with closing(sqlite3.connect(p)) as c:
        c.execute("CREATE TABLE securities (ticker TEXT PRIMARY KEY, holding_rationale TEXT)")
        c.executemany("INSERT INTO securities VALUES (?, ?)", list(texts.items()))
        c.commit()
    return p


def _read(db: Path) -> "dict[str, str]":
    with closing(sqlite3.connect(db)) as c:
        return dict(c.execute("SELECT ticker, holding_rationale FROM securities"))


# ── the held data is the history's ────────────────────────────────────────────

@pytest.mark.parametrize("step", [step2, step4], ids=["step 2", "step 4"])
def test_each_step_holds_every_later_seed_text(step):
    commit = STEP_COMMIT[step]
    for t in TICKERS[step]:
        assert step.LATER[t] == {_sha(v["text"]) for v in _after(t, commit)}, t
    assert "seed_securities" not in Path(step.__file__).read_text(encoding="utf-8"), (
        "a step that reads today's seed goes stale the next time the seed is edited")


def test_step_2_holds_its_own_target_texts():
    assert step2.TARGET == {t: _text(t, "4abdb40") for t in step2.SET_FROM_TARGET}


# ── on a fixture book (CI) ────────────────────────────────────────────────────

@pytest.mark.parametrize("step", [step2, step4], ids=["step 2", "step 4"])
def test_a_book_at_the_latest_seed_texts_is_left_as_it_is(step, monkeypatch, tmp_path,
                                                          capsys):
    texts = {t: HISTORY[t][-1]["text"] for t in TICKERS[step]}
    db = _book(tmp_path, texts)
    before = db.read_bytes()
    monkeypatch.setattr(step, "TRACKER_DB", db)
    assert step.main() == 0
    out = capsys.readouterr().out
    assert "nothing to do" in out and "superseded by a later seed text" in out
    assert db.read_bytes() == before


def test_step_4_leaves_a_later_text_whose_anchor_still_occurs(monkeypatch, tmp_path):
    """VOO's later seed text still holds "liquidity I don't need.", step 4's anchor. The
    swap would have inserted the old falsifier paragraph beside the newer one."""
    later = HISTORY["VOO"][-1]["text"]
    assert "liquidity I don't need." in later and step4.SUBS["VOO"][0][1] not in later, (
        "premise: the anchor occurs, and the swap's text does not")
    texts = {t: _text(t, "408ca83") for t in TICKERS[step4]}
    texts["VOO"] = later
    db = _book(tmp_path, texts)
    monkeypatch.setattr(step4, "TRACKER_DB", db)
    assert step4.main() == 0
    assert _read(db) == texts


@pytest.mark.parametrize("step", [step2, step4], ids=["step 2", "step 4"])
def test_an_earlier_text_is_still_migrated(step, monkeypatch, tmp_path):
    texts = {t: (None if step is step2 and t in step2.SET_FROM_TARGET
                 else HISTORY[t][0]["text"]) for t in TICKERS[step]}
    db = _book(tmp_path, texts)
    monkeypatch.setattr(step, "TRACKER_DB", db)
    assert step.main() == 0
    got = _read(db)
    for t, pairs in step.SUBS.items():
        assert all(repl in got[t] for _old, repl in pairs), t
    if step is step2:
        assert all(got[t] == step2.TARGET[t] for t in step2.SET_FROM_TARGET)


@pytest.mark.parametrize("step, ticker", [(step2, "IDHQ"), (step2, "AVUV"), (step4, "VOO")],
                         ids=["step 2 set", "step 2 swap", "step 4"])
def test_a_text_neither_earlier_nor_later_still_aborts(step, ticker, monkeypatch, tmp_path):
    texts = {t: _text(t, STEP_COMMIT[step]) for t in TICKERS[step]}
    texts[ticker] = "something a human rewrote by hand"
    db = _book(tmp_path, texts)
    before = db.read_bytes()
    monkeypatch.setattr(step, "TRACKER_DB", db)
    assert step.main() == 1
    assert db.read_bytes() == before


# ── on scratch copies of the personal book (personal mode) ────────────────────

CASES = [(t, v["commit"]) for t in TICKERS[step2] + TICKERS[step4] for v in HISTORY[t]]


@pytest.mark.parametrize("ticker, commit", CASES)
def test_the_runner_completes_from_each_historical_text(ticker, commit, tmp_path, capsys):
    if not TRACKER_DB.exists() or TRACKER_DB.stat().st_size == 0:
        pytest.skip("no personal book (CI)")
    sha = hashlib.sha256(TRACKER_DB.read_bytes()).hexdigest()
    copy = tmp_path / "tracker.db"
    shutil.copyfile(TRACKER_DB, copy)
    copy.chmod(0o644)
    with closing(sqlite3.connect(copy)) as c:
        cur = c.execute("UPDATE securities SET holding_rationale = ? WHERE ticker = ?",
                        (_text(ticker, commit), ticker))
        assert cur.rowcount == 1
        c.commit()
    rc = runner.run(copy)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert hashlib.sha256(TRACKER_DB.read_bytes()).hexdigest() == sha
