"""Every dollar figure in committed text says where it comes from (#721).

No figure from the personal book goes into committed code, comments, docs or tests.
A position value, a total, a gain, a drag or a balance read off data/tracker.db or a
positions export, or a rate derived from private/personal_profile.json, is reported in a
session and never committed. A dollar figure the tree does type sits in
tests/dollar_figures.py, which says for each file and figure whether it comes from the
demo book, is synthetic, or is a public figure cited with its date. A figure with no
entry fails here, whatever it is.

WHAT IS READ. Every file `git ls-files` lists that decodes as UTF-8, CHANGELOG.md and
the docs included: a figure in a history entry is as public as one in code. Two kinds of
file are read differently, each named in tests/dollar_figures.py:
  * SOURCED_FILES, whose every figure is generated from a public book by a tool and held
    to it by a test (the committed books' locked-figures golden);
  * tests/dollar_figures.py itself, every figure in which is an entry naming a figure in
    another file.

What is a figure, and the zero-or-unit rule, are defined in tests/dollar_figures.py.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.dollar_figures import (
    CATEGORIES, FIGURES, PUBLIC, SOURCED_FILES, figures_in, is_unit_or_zero,
)

ROOT = Path(__file__).resolve().parent.parent
THE_LIST = "tests/dollar_figures.py"


def _tracked_text_files() -> "dict[str, str]":
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                         check=True).stdout.splitlines()
    files = {}
    for rel in out:
        p = ROOT / rel
        if not p.is_file():
            continue
        try:
            files[rel] = p.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue
    return files


def _unlisted(rel: str, text: str, figures=FIGURES) -> "list[str]":
    """Every figure the file carries that is neither a zero or unit nor listed, as
    'line: figure'."""
    if rel == THE_LIST or rel in SOURCED_FILES:
        return []
    listed = figures.get(rel, {})
    return [f"{line}: {fig}" for line, fig in figures_in(text)
            if not is_unit_or_zero(fig) and fig not in listed]


@pytest.fixture(scope="module")
def tracked():
    return _tracked_text_files()


def test_the_premise_the_census_reads_the_tree(tracked):
    """A census that read nothing would pass. It reads the files the rule is about, and
    finds figures in them."""
    for must in ("src/location_config.py", "src/location_actions.py", "src/household.py",
                 "pages/14_Asset_Location.py", "pages/13_Household_View.py", "CHANGELOG.md",
                 "README.md", "tests/test_location_actions.py", THE_LIST, *SOURCED_FILES):
        assert must in tracked, must
    found = sum(len(figures_in(text)) for text in tracked.values())
    assert found > 500, f"the census found only {found} figures in the whole tree"


def test_every_committed_dollar_figure_has_a_source(tracked):
    unlisted = {rel: hits for rel, text in tracked.items() if (hits := _unlisted(rel, text))}
    assert unlisted == {}, (
        "a dollar figure with no entry in tests/dollar_figures.py. If it comes from the "
        "personal book, remove it (#721); otherwise list it with its source: "
        f"{unlisted}")


def test_every_entry_names_a_figure_its_file_still_holds(tracked):
    """An entry for a figure that is gone would list nothing and hide a rewording."""
    stale = {}
    for rel, entries in FIGURES.items():
        held = {fig for _line, fig in figures_in(tracked.get(rel, ""))}
        if gone := sorted(set(entries) - held):
            stale[rel] = gone
    assert stale == {}, stale


def test_every_source_says_where_the_figure_comes_from():
    """Each entry opens with its category; a public figure cites its source with a year."""
    for rel, entries in FIGURES.items():
        for fig, source in entries.items():
            category, _, where = source.partition(": ")
            assert category in CATEGORIES and where.strip(), (rel, fig, source)
            if category == PUBLIC:
                assert any(str(y) in where for y in range(1980, 2040)), (rel, fig, source)
    for rel, source in SOURCED_FILES.items():
        assert source.partition(": ")[0] in CATEGORIES, rel


def test_the_list_holds_only_figures_it_lists(tracked):
    """tests/dollar_figures.py is exempt from the census because each figure in it is an
    entry's key. A figure typed anywhere else in it would sit there unexamined."""
    keys = {fig for entries in FIGURES.values() for fig in entries}
    extra = sorted({fig for _line, fig in figures_in(tracked[THE_LIST])
                    if not is_unit_or_zero(fig)} - keys)
    assert extra == [], extra


def test_a_sourced_file_is_tracked_and_never_the_personal_golden(tracked):
    for rel in SOURCED_FILES:
        assert rel in tracked, rel
        assert not rel.startswith("data/private/"), rel


# ── The rule, on constructed text ─────────────────────────────────────────────

def test_an_unlisted_figure_fails_wherever_it_is():
    """The negative: a figure fails in a file with no entries, and in a listed file
    that does not list that figure."""
    text = "# a position worth $12.34\nx = 1\n"
    assert _unlisted("src/new_module.py", text, figures={}) == ["1: $12.34"]
    assert _unlisted("src/new_module.py", text,
                     figures={"src/new_module.py": {"$5,678": "synthetic: x"}}) == ["1: $12.34"]
    assert _unlisted("src/new_module.py", text,
                     figures={"src/new_module.py": {"$12.34": "synthetic: x"}}) == []


@pytest.mark.parametrize("text, want", [
    ("worth $12.34 today", ["$12.34"]),
    ("a fund of $9.9B and a book of $2k", ["$9.9B", "$2k"]),
    ("the escaped \\$5,678 in Markdown", ["$5,678"]),
    ("a loss of -$75, signed", ["$75"]),
    ("$ 2k with a space", ["$2k"]),
    ("$5,678, then a comma", ["$5,678"]),
    ("$75 billion is the number alone", ["$75"]),
])
def test_what_counts_as_a_figure(text, want):
    assert [fig for _line, fig in figures_in(text)] == want


@pytest.mark.parametrize("text", ["f\"${x:,.0f}\"", "\"$%.0f\" % v", "costs $ and cents",
                                  "a $x placeholder"])
def test_a_format_placeholder_is_not_a_figure(text):
    assert figures_in(text) == []


@pytest.mark.parametrize("fig, unit", [
    ("$0", True), ("$0.00", True), ("$1", True), ("$1.00", True), ("$1k", False),
    ("$0.40", False), ("$10", False), ("$1,000", False),
])
def test_only_a_zero_or_a_unit_needs_no_entry(fig, unit):
    assert is_unit_or_zero(fig) is unit
