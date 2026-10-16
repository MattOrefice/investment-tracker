"""Research and Correlations claims #427 did not list, as decided by the owner on
October 15, 2026 (#608).

  * "Sleeve target weights locked at portfolio inception (May 2025)" goes. The sentence
    says what is true of the locks the books hold: each holds the policy as it stood when
    taken, or, taken before locks held it, as it stood the day it was recorded.
  * IDHQ's "S&P classifies South Korea as developed where MSCI does not" goes: the dated
    pages src/factors.py cites are FTSE's classification and French's country table,
    which say nothing of either.
  * VTV's "Multi-metric is academically superior" goes, with the uncited argument after
    its colon.
  * interpret_correlations drops "High" and the reading it carried, and states the top
    pairs' correlations.
The fund rationales and the VTV thesis reach both committed books through the seeds'
data paths, which tests/test_markets_copy.py and tests/test_operations_copy.py hold.
"""
from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from src.factors import interpret_correlations

ROOT = Path(__file__).resolve().parent.parent
BOOKS = [ROOT / "data" / "demo.db", ROOT / "tests" / "fixtures" / "frozen_book.db"]


def _ro(book):
    return sqlite3.connect(f"file:{Path(book).as_posix()}?mode=ro", uri=True)


def _page_strings(page: str) -> str:
    tree = ast.parse((ROOT / "pages" / page).read_text(encoding="utf-8"))
    return " ".join(n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str))


# ── the policy sentence ───────────────────────────────────────────────────────

POLICY_SENTENCE = (
    "Sleeve target weights are the book's current SAA targets. Each quarter's lock holds "
    "the policy as it stood when that lock was taken, or, for a lock taken before locks "
    "held it, as it stood on the day it was recorded, which that lock's cover states.")


def test_the_research_methodology_says_what_the_locks_hold():
    strings = _page_strings("8_Research.py")
    assert POLICY_SENTENCE in strings
    assert "locked at portfolio inception" not in strings and "May 2025" not in strings


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_every_committed_lock_holds_a_policy_the_sentence_describes(book):
    """The sentence's premise, read off each lock: a held policy, and, for a lock given
    it after it was taken, the flag the cover line reads."""
    from src.cache import policy_recorded_note
    con = _ro(book)
    try:
        rows = con.execute("SELECT quarter_id, snapshot_data FROM quarter_snapshots").fetchall()
    finally:
        con.close()
    assert rows, "premise: the book holds locks"
    for quarter, payload in rows:
        held = json.loads(payload)["inputs"]["policy"]
        assert held["kind"] == "policy" and held["asset_classes"], quarter
        if held["recorded_after_lock"]:
            snap = type("Snap", (), {"inputs": {"policy": held}})()
            assert "after this quarter locked" in policy_recorded_note(snap), quarter


# ── IDHQ and South Korea ──────────────────────────────────────────────────────

@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_idhqs_rationale_makes_no_uncited_classification_claim(book):
    con = _ro(book)
    try:
        text = con.execute("SELECT holding_rationale FROM securities WHERE ticker = 'IDHQ'"
                           ).fetchone()[0]
    finally:
        con.close()
    assert "S&P classifies" not in text and "MSCI does not" not in text
    assert ("The Factor Profile page sets out how IDHQ's universe treats South Korea against "
            "the factors'.") in text


def test_the_pages_factors_py_cites_say_nothing_of_s_and_p_or_msci():
    """Why the IDHQ line could not be cited: the two dated classification sources the
    module holds are FTSE's and French's."""
    from src import factors
    cited = factors._FTSE_CLASSIFICATION + " " + factors._FRENCH_COUNTRY_TABLE
    assert "FTSE" in cited and "Fama/French" in cited
    assert "S&P" not in cited and "MSCI" not in cited


# ── VTV ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_vtv_makes_no_uncited_academic_claim(book):
    con = _ro(book)
    try:
        texts = [con.execute("SELECT holding_rationale FROM securities WHERE ticker = 'VTV'"
                             ).fetchone()[0]]
        texts += list(con.execute("SELECT macro_view, view_summary, vehicle_rationale FROM "
                                  "theses WHERE thesis_id = 15").fetchone())
    finally:
        con.close()
    assert len(texts) == 4
    for text in texts:
        assert "academically superior" not in text
        assert "relying solely on P/B" not in text
        assert "section 8.2.1). VTV also costs {{er:VTV}}" in text, text


# ── correlations ──────────────────────────────────────────────────────────────

def _corr(names, values) -> pd.DataFrame:
    return pd.DataFrame(values, index=names, columns=names)


@pytest.mark.parametrize("top", [0.92, 0.20])
def test_the_top_pairs_are_stated_with_their_correlations_and_no_grade(top):
    """Whatever the top correlation is, the sentence states it and calls it nothing."""
    names = ["US Large Core", "US Small Cap", "Core Fixed Income", "International Core"]
    m = _corr(names, [[1.0, top, 0.10, top - 0.05], [top, 1.0, 0.05, top - 0.10],
                      [0.10, 0.05, 1.0, 0.02], [top - 0.05, top - 0.10, 0.02, 1.0]])
    text = interpret_correlations(m, {"US Large Core", "US Small Cap", "International Core"})
    assert text.startswith(f"The top intra-equity pairs are US Large Core × US Small Cap "
                           f"(ρ = {top:.2f})"), text
    for gone in ("High", "high", "global market beta", "largely share"):
        assert gone not in text.split("  \n")[0], gone
