"""The methodology says the valuation rule in force, read from it (#702, decided by the owner
on November 18, 2026).

Two statements named the portfolio value series' basis as adjusted close: Benchmark
Attribution's methodology caption, and the first bullet of factors.build_benchmark_methodology,
which that page and the quarterly PDF render. Since #700 live reads value the book on the
raw-close rule (#369); only a lock taken before the marker is on adjusted levels. Both now
read cache.valuation_basis, the words for the rule cache.valuation_in_force names. Pinned:
  * a live read says the raw-close rule;
  * inside a lock on adjusted levels, a committed one included, the bullet is the sentence
    every committed lock's report printed, word for word;
  * inside a raw-close lock it says the raw-close rule;
  * each rule has its words, and no other committed text types a basis.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src import cache
from src.factors import build_benchmark_methodology
from tests.conftest import on_adjusted_levels, point_at_frozen_book

ROOT = Path(__file__).resolve().parent.parent
# The bullet as every committed lock's report printed it before #702. It must not move:
# each of those locks is on adjusted levels, and "$1.00" in it is read as a figure.
ADJUSTED_BULLET = ("R_p: daily portfolio total return (adjusted-close basis, SPAXX proxied via "
                   "BIL normalized to $1.00 at inception). R_b: ")


def _bullet() -> str:
    return build_benchmark_methodology(None)[0]


def test_each_rule_has_its_words():
    assert set(cache.VALUATION_BASIS) == {cache.RAW_CLOSE_RULE, cache.ADJUSTED_LEVELS_RULE}
    assert cache.lock_valuation_rule(None) in cache.VALUATION_BASIS


def test_a_live_read_says_the_raw_close_rule():
    basis, spaxx = cache.VALUATION_BASIS[cache.RAW_CLOSE_RULE]
    assert cache.valuation_basis() == (basis, spaxx)
    assert _bullet().startswith(f"R_p: daily portfolio total return ({basis}, {spaxx}). R_b: ")
    assert "adjusted" not in _bullet()


def test_inside_a_lock_on_adjusted_levels_the_bullet_is_the_committed_locks_sentence():
    with on_adjusted_levels():
        assert _bullet().startswith(ADJUSTED_BULLET)
    assert not _bullet().startswith(ADJUSTED_BULLET)


def test_inside_a_raw_close_lock_it_says_the_raw_close_rule():
    basis, _ = cache.VALUATION_BASIS[cache.RAW_CLOSE_RULE]
    token = cache._VALUATION_LOCK.set((cache.RAW_CLOSE_RULE, None))
    try:
        assert _bullet().startswith(f"R_p: daily portfolio total return ({basis}, ")
    finally:
        cache._VALUATION_LOCK.reset(token)


def test_inside_a_committed_lock_it_says_adjusted_close(tmp_path, monkeypatch):
    """The frozen book's Q2 2026 lock, as the PDF reads it: through snapshot_price_context,
    the mechanism that sets the rule, not a stand-in for it."""
    point_at_frozen_book(monkeypatch, tmp_path)
    snap = cache.get_quarter_snapshot("2026Q2")[0]
    assert snap is not None and cache.lock_valuation_rule(snap) == cache.ADJUSTED_LEVELS_RULE
    with cache.snapshot_price_context(snap):
        assert _bullet().startswith(ADJUSTED_BULLET)
    assert not _bullet().startswith(ADJUSTED_BULLET)


PHRASES = ("adjusted-close basis", "raw-close basis")


def _typed_bases(root: Path, names) -> "list[str]":
    """Each file under ``root`` (src/cache.py excepted) that names a basis, with the phrase."""
    typed = []
    for name in names:
        if name == "src/cache.py" or not name.endswith((".py", ".html")):
            continue
        text = (root / name).read_text(encoding="utf-8")
        typed += [f"{name}: {phrase}" for phrase in PHRASES if phrase in text]
    return typed


def test_no_other_committed_text_types_a_basis():
    """A statement of the basis reads valuation_basis. Outside src/cache.py's table, no
    source, page or template names one."""
    files = subprocess.run(["git", "ls-files", "src", "pages", "app.py", "templates"],
                           cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    assert "pages/6_Benchmark_Attribution.py" in files and "src/factors.py" in files
    assert _typed_bases(ROOT, files) == []


@pytest.mark.parametrize("phrase", PHRASES)
def test_the_census_finds_a_typed_basis(phrase, tmp_path):
    for name, text in (("pages/page.py", f'st.caption("the value series ({phrase}).")\n'),
                       ("src/cache.py", f'"{phrase}"\n')):
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    assert _typed_bases(tmp_path, ["pages/page.py", "src/cache.py"]) == [
        f"pages/page.py: {phrase}"]
