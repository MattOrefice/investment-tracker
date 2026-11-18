"""No committed code, comment, doc or test names a home state (#706, #710).

Where the owner lives, and that place's rates, are private: they sit in the gitignored
private/personal_profile.json (`tax_residence`), and every page that prices state tax
reads them through personal_profile.load_tax_residence. A committed rate table, a
config key or a sentence naming one state would say where the owner lives as surely as
the profile does, so the committed tree names none. Tests price with synthetic
residences (tests/tax_profiles.py).

WHAT IS READ. Every file `git ls-files` lists that decodes as UTF-8 text, except
CHANGELOG.md, whose history stays as it was written, and data/cache/, which holds
provider files. Python files are read token by token, so a name in code, a comment, a
docstring or a string literal is found alike.

WHAT IS ALLOWED, each by its exact context and never by the name alone. The contexts
are defined in tests/us_states.py, beside the list of all fifty states, so this file
names no state:
  * the market's clock: the exchange's city as the date, time, close, day or session
    the repo keeps in Eastern time, and the exchange's name (MARKET_CLOCK_AFTER and
    MARKET_CLOCK_BEFORE). The same name anywhere else fails;
  * a typeface in app.py's font stack (TYPEFACE_STACK);
  * tests/us_states.py itself, the list this check is made from. Listing all fifty
    singles out none of them;
  * a migration's data. A migration tool's constants hold the text it wrote or the text
    it matches, and changing them would change what the migration does, so they stay
    as they are (#710). They are listed in MIGRATION_DATA by file and constant; the
    rest of each tool, its comments and messages included, is read like any file.

Postal codes are read the same way, in Python prose (tests.us_states.codes_named) and
in Markdown, less the codes the repo uses for something else.
"""
from __future__ import annotations

import ast
import io
import subprocess
import tokenize
from pathlib import Path

import pytest

from tests.us_states import (
    CODES_USED_OTHERWISE, MARKET_CLOCK_CITY, STATE_CODES, TYPEFACE_STACK, TYPEFACE_STATE, US_STATES,
    allowed_context, codes_in_text, codes_named, names_at,
)

ROOT = Path(__file__).resolve().parent.parent

SKIPPED = ("CHANGELOG.md",)
SKIPPED_DIRS = ("data/cache/",)
THE_LIST = "tests/us_states.py"

# file -> the module-level constants that hold a migration's data.
MIGRATION_DATA: dict[str, frozenset[str]] = {
    "tools/migrate_demo_vgit_jurisdiction.py": frozenset({"_NEW"}),
    "tools/migrate_personal_vgit_jurisdiction.py": frozenset({"_NEW"}),
    "tools/migrate_operations_copy.py": frozenset({"NEW"}),
}


def _tracked_text_files() -> "list[tuple[str, str]]":
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                         check=True).stdout.splitlines()
    files = []
    for rel in out:
        if rel in SKIPPED or rel.startswith(SKIPPED_DIRS):
            continue
        p = ROOT / rel
        if not p.is_file():
            continue
        try:
            files.append((rel, p.read_bytes().decode("utf-8")))
        except UnicodeDecodeError:
            continue
    return files


def _data_lines(rel: str, text: str) -> "set[int]":
    """The lines of a migration's data constants (MIGRATION_DATA), or none."""
    names = MIGRATION_DATA.get(rel)
    if not names:
        return set()
    lines: set[int] = set()
    for node in ast.parse(text).body:
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, ast.AnnAssign) else [])
        if any(isinstance(t, ast.Name) and t.id in names for t in targets):
            lines |= set(range(node.lineno, node.end_lineno + 1))
    return lines


def _named_in(rel: str, text: str) -> "list[str]":
    """Every state name the file carries outside an allowed context, as 'line: name'."""
    if rel == THE_LIST:
        return []
    found = []
    if rel.endswith(".py"):
        data = _data_lines(rel, text)
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.start[0] in data:
                continue
            for start, name in names_at(tok.string):
                if not allowed_context(tok.string, start, name):
                    line = tok.start[0] + tok.string.count("\n", 0, start)
                    found.append(f"{line}: {name}")
        return found
    for start, name in names_at(text):
        if not allowed_context(text, start, name):
            found.append(f"{text.count(chr(10), 0, start) + 1}: {name}")
    return found


def _codes_in(rel: str, text: str) -> "list[str]":
    if rel == THE_LIST:
        return []
    if rel.endswith(".py"):
        data = _data_lines(rel, text)
        kept = "\n".join("" if i + 1 in data else line
                         for i, line in enumerate(text.splitlines()))
        return codes_named(kept)
    if rel.endswith(".md"):
        return codes_in_text(text)
    return []


@pytest.fixture(scope="module")
def tracked():
    return _tracked_text_files()


def test_the_premise_the_check_reads_the_tree(tracked):
    """A census that read nothing would pass. It reads the files the rule is about."""
    rels = {r for r, _ in tracked}
    for must in ("src/location_config.py", "src/location_actions.py",
                 "pages/14_Asset_Location.py", "src/seed_securities.py", "README.md",
                 "tools/migrate_vgit_state_exemption.py", "tests/test_vgit_jurisdiction.py",
                 *MIGRATION_DATA):
        assert must in rels, must
    assert "CHANGELOG.md" not in rels
    assert len(set(US_STATES)) == 50 and len(set(STATE_CODES)) == 50


def test_no_committed_file_names_a_state(tracked):
    named = {rel: hits for rel, text in tracked if (hits := _named_in(rel, text))}
    assert named == {}, named


def test_no_committed_file_writes_a_state_code(tracked):
    coded = {rel: hits for rel, text in tracked if (hits := _codes_in(rel, text))}
    assert coded == {}, coded


@pytest.mark.parametrize("state", US_STATES)
def test_a_state_named_as_a_place_fails_whichever_it_is(state):
    """The negative, for every state alike: as a residence, a rate or a fund's home."""
    for sentence in (f"The owner lives in {state}.", f"taxed at {state}'s rate",
                     f"a {state} municipal fund", f"{state} taxes the interest"):
        assert _named_in("x.md", sentence) == [f"1: {state}"], sentence


@pytest.mark.parametrize("sentence", [
    "Every rendered date is {c}'s date.", "the next {c} close", "{c} time, labelled ET",
    "until midnight in {c}, the date", "a little after 16:00 {c}.",
    "Whether {c} holds a session on d", "the {c} Stock Exchange",
])
def test_the_market_clock_is_allowed(sentence):
    """The positive: the contexts the repo writes as its clock pass."""
    assert _named_in("x.md", sentence.format(c=MARKET_CLOCK_CITY)) == []


def test_the_typeface_is_allowed_and_only_as_a_typeface():
    assert _named_in("x.md", f"font-family: 'EB Garamond', {TYPEFACE_STACK}, serif") == []
    assert _named_in("x.md", f"a {TYPEFACE_STATE} fund") == [f"1: {TYPEFACE_STATE}"]


def test_a_migrations_data_is_exempt_and_its_prose_is_not():
    """The exemption covers the named constants and nothing else in the same file."""
    s = US_STATES[0]
    rel = "tools/migrate_demo_vgit_jurisdiction.py"
    src = ('"""Rewrites a sentence."""\n'
           f'_NEW = ("at {s}\'s flat rate")\n'
           f'# a comment naming {s}\n'
           f'print("wrote {s}")\n')
    assert _named_in(rel, src) == [f"3: {s}", f"4: {s}"]
    assert _named_in("tools/other.py", src) == [f"2: {s}", f"3: {s}", f"4: {s}"]


def test_a_code_in_a_pattern_is_found():
    """A regex naming a code (\\bXX\\b) is a code named."""
    code = next(c for c in STATE_CODES if c not in CODES_USED_OTHERWISE)
    assert codes_named(f'x = r"\\b{code}\\b"\n') == [code]
    assert codes_named(f'# taxed at the {code} rate\n') == [code]


def test_every_listed_migration_constant_exists():
    """An exemption for a constant that is gone would exempt nothing and hide a rename."""
    for rel, names in MIGRATION_DATA.items():
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        defined = {t.id for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign))
                   for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
                   if isinstance(t, ast.Name)}
        assert names <= defined, (rel, names - defined)
