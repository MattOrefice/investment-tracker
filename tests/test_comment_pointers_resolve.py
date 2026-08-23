"""A comment naming a module constant is a claim that the constant exists. #291.

`see FEDERALLY_EXEMPT_SLEEVES` sends a reader to look for something, and when it is gone
their most likely conclusion is that they are looking in the wrong place rather than
that the comment is wrong. Nothing in any toolchain checks this: the compiler does not
read comments and neither did any test.

WHY THE GAP EXISTED, which is the part worth keeping. #278 deleted
`FEDERALLY_EXEMPT_SLEEVES` and wrote a sweep to catch the symbol coming back. Its first
version searched file TEXT and flagged `household.py`, because the comment RECORDING the
deletion quotes the retired expression — the checker found its own documentation. The
fix was to read through `ast`, which is correct for that problem and made the sweep
structurally blind to comments. **Two dangling pointers then sat in
`location_config.py` for three PRs.** The AST fix caused this gap.

NOT THE SAME MECHANISM AS #174, established before merging them. #165's import guard is
RUNTIME — `_run_with_db_trap("import src.<mod>")` in a subprocess — so it observes a
CONNECTION OPENING. A comment cannot open one, so that guard can never fire on this
defect; and a text sweep cannot know whether a call touches a database. Different input,
different observable. Two fixes, and #174 is a separate PR.
"""
import ast
import io
import pathlib
import re
import tokenize

import pytest

import src.location_config as _lc

SRC = pathlib.Path(_lc.__file__).resolve().parent

# MUST CONTAIN AN UNDERSCORE, and that single requirement is the whole discriminator.
# Measured over src/: a bare ALL-CAPS pattern (>=6 chars) leaves 271 non-resolving
# mentions, because this codebase writes emphasis in capitals as a house style —
# SURFACED, DERIVED, LEGACY, EXCLUDED, BEFORE. Requiring an underscore drops that to 3.
# Emphasis words do not carry underscores; constant names do.
_CONST = re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b")

# Symbols a comment may name even though they no longer resolve, because the comment is
# a RECORD of the deletion rather than a POINTER to a live thing. This repo deliberately
# keeps such records — "REPLACES FEDERALLY_EXEMPT_SLEEVES, which was deleted rather than
# kept alongside" is exactly the institutional memory that makes a deletion
# comprehensible, and a check forbidding it would delete that memory.
#
# AN EXPLICIT ALLOWLIST, NOT A VERB HEURISTIC. #291 proposed separating a pointer
# ("see X", "lives in X") from a record ("was X", "REPLACES X") by matching verbs. With
# only three survivors that is more machinery than the problem has: a list with a reason
# per entry is checkable by reading, where a heuristic is another thing that can be
# subtly wrong. Revisit if this grows past a handful.
RETIRED_BUT_RECORDED = {
    "EQUITY_DEFAULT_YIELD": "deleted at #210 PR 3 with the fallback itself; the comment "
                            "records what used to sit there and why it went",
    "FEDERALLY_EXEMPT_SLEEVES": "deleted at #278, replaced by SLEEVE_TAX_CHARACTER; the "
                                "comments record the replacement and the rate/eligibility "
                                "split that motivated it",
}

# COMMENTS ONLY, deliberately. Adding docstrings raises the non-resolving count from 3 to
# 17, and the extra 14 are a different class entirely: TRACKER_MODE, STREAMLIT_BUILD_SHA
# and SHOW_BUILD_HASH are ENVIRONMENT VARIABLES; SPY_MC, MC_B, VALUE_SCALE and
# SIZE_ANCHOR_Y are dataframe columns or locals. None is a module constant, so each would
# need its own classification — scope this issue never had. Both #291 instances were
# comments.


def _module_level_names() -> set[str]:
    """Every name ASSIGNED at any level in src/, read from the AST.

    TWO BRANCHES, and both earned their place by measurement rather than by argument.
    The right question is not "how many names does this branch match" but "how many
    does it contribute that no other branch does, AND that a comment actually names":

    ==================  =========  ==========  ====================================
    branch              matches    UNIQUE      mentioned in a comment
    ==================  =========  ==========  ====================================
    AnnAssign               70         70              14   -> load-bearing
    Assign                  66         66               2   -> load-bearing
    FunctionDef/Class      475          0               0   -> REMOVED, unreachable
    alias                  172         25               0   -> REMOVED, redundant
    ==================  =========  ==========  ====================================

    Both removals came from surviving mutants, and the SECOND is the instructive one.
    FunctionDef went first: Python functions are snake_case, so of 475 names **zero**
    match an ALL_CAPS filter, and the docstring defending the branch as "deliberately
    wide" was defending dead code. The replacement docstring then claimed `alias` was
    load-bearing because "25 imported names match the filter" — and a mutant deleting it
    survived too. All 25 are ALSO assigned somewhere in src/, so the branch contributes
    **nothing unique**. Counting MATCHES instead of UNIQUE CONTRIBUTIONS is the same
    error twice, one line apart, the second made while correcting the first.
    """
    names: set[str] = set()
    for f in SRC.glob("*.py"):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken src/ fails louder elsewhere
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        names.add(t.id)
    return names


def _comment_mentions():
    """(file, line, symbol) for every underscore-bearing capital in a src/ COMMENT."""
    for f in sorted(SRC.glob("*.py")):
        text = f.read_text(encoding="utf-8")
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type != tokenize.COMMENT:
                continue
            for m in _CONST.finditer(tok.string):
                yield f.name, tok.start[0], m.group(0)


# ── the contract ──────────────────────────────────────────────────────────────

def test_every_comment_pointer_resolves():
    """THE CHECK. A comment naming a symbol that no longer exists is a claim that
    silently stopped being true — and three instances of a reference decaying this way
    are on record (#237's moved line number, #228's four copies, #291's two pointers).
    """
    names = _module_level_names()
    dangling = [
        f"{f}:{line} names {sym!r}"
        for f, line, sym in _comment_mentions()
        if sym not in names and sym not in RETIRED_BUT_RECORDED
    ]
    assert not dangling, (
        "a comment points at a symbol that does not resolve in src/. Either the symbol "
        "was renamed or deleted and the comment was not updated, or the comment is a "
        "RECORD of a deletion and belongs in RETIRED_BUT_RECORDED with its reason:\n  "
        + "\n  ".join(dangling))


def test_the_sweep_is_not_vacuous():
    """Every assertion above is worthless if the scan finds nothing to check. The
    underscore filter is aggressive by design, so this pins that it still admits real
    symbols rather than having narrowed to zero."""
    mentions = list(_comment_mentions())
    # A FLOOR WITH HEADROOM, not the measurement. src/ comments carried 34 such mentions
    # on 2026-08-23; the floor is set well below that because the failure this guards is
    # the pattern matching NOTHING (0, or near it), not the population shrinking a
    # little. Pinning 34 would make an unrelated comment edit fail this test, which is
    # the count-in-prose defect (#186) re-created inside the guard against it — the
    # first draft did exactly that with a guessed 40 against a real 34.
    assert len(mentions) >= 15, (
        f"only {len(mentions)} underscore-bearing capitals found in src/ comments — the "
        "pattern has stopped matching and the sweep is passing on a near-empty set")
    assert len({f for f, _, _ in mentions}) >= 3, (
        "all mentions come from fewer than three files — the scan is not reaching src/")
    names = _module_level_names()
    resolving = sum(1 for _, _, s in mentions if s in names)
    assert resolving >= len(mentions) * 0.5, (
        f"only {resolving} of {len(mentions)} mentions resolve — _module_level_names is "
        "under-populated, which would make the check fire on almost everything")


def test_a_planted_dangling_pointer_is_caught(tmp_path, monkeypatch):
    """THE POSITIVE CONTROL. `test_every_comment_pointer_resolves` passes today because
    there are no dangling pointers, which is indistinguishable from a sweep that cannot
    see one. This plants one and requires it to be found."""
    probe = tmp_path / "probe.py"
    probe.write_text("# see SOME_DELETED_CONSTANT for the rationale\nX = 1\n",
                     encoding="utf-8")
    monkeypatch.setattr(pathlib.Path, "glob",
                        lambda self, pat: iter([probe]) if str(self) == str(SRC)
                        else pathlib.Path.glob(self, pat), raising=False)
    found = [s for _, _, s in _comment_mentions()]
    assert "SOME_DELETED_CONSTANT" in found, (
        "a planted dangling pointer was not seen by the sweep")


def test_emphasis_words_are_not_treated_as_symbols():
    """The reason the underscore requirement exists. This codebase writes emphasis in
    capitals — SURFACED, DERIVED, LEGACY — and a bare ALL-CAPS pattern leaves 271
    non-resolving mentions against 3. A check that noisy would be turned off."""
    for word in ("SURFACED", "DERIVED", "LEGACY", "EXCLUDED", "STRATEGIC"):
        assert not _CONST.search(word), f"{word} is emphasis, not a symbol"
    for name in ("FEDERALLY_EXEMPT_SLEEVES", "SLEEVE_TAX_CHARACTER", "TAX_PROFILE"):
        assert _CONST.search(name), f"{name} is a symbol and must be scanned"


# ── the allowlist is a record, and must stay one ──────────────────────────────

def test_every_allowlisted_symbol_is_genuinely_gone():
    """An allowlist entry for a symbol that still resolves is an exemption doing nothing
    — and worse, it would suppress a real dangling pointer if that symbol were later
    deleted for a different reason."""
    names = _module_level_names()
    live = [s for s in RETIRED_BUT_RECORDED if s in names]
    assert not live, (
        f"{live} still resolve in src/ — remove them from RETIRED_BUT_RECORDED, which "
        "is for symbols that are GONE")


def test_every_allowlisted_symbol_records_why():
    for sym, why in RETIRED_BUT_RECORDED.items():
        assert why and len(why) > 30, f"{sym} is exempted without a reason"
        assert "#" in why, f"{sym}'s reason names no issue or PR"


def test_the_allowlist_is_still_needed():
    """Non-vacuity in the other direction: if nothing in src/ mentions these any more,
    the exemptions are dead weight and should go."""
    mentioned = {s for _, _, s in _comment_mentions()}
    unused = [s for s in RETIRED_BUT_RECORDED if s not in mentioned]
    assert not unused, (
        f"{unused} are allowlisted but no comment mentions them — the exemption "
        "outlived the record it was protecting")


# ── #186: the thematic comment states an invariant, not a count ───────────────

def _thematic_comment() -> str:
    """The comment block above the thematic group's `population`/`caption` keys, read
    from COMMENT tokens so the assertions cannot be satisfied by code or prose
    elsewhere in the file."""
    src = (SRC / "location_actions.py").read_text(encoding="utf-8")
    lines = src.splitlines()
    i = next(i for i, l in enumerate(lines) if '"population": "matched_symbols"' in l)
    out = []
    for j in range(i - 1, max(0, i - 14), -1):
        st = lines[j].strip()
        if not st.startswith("#"):
            break
        out.append(st.lstrip("# ").rstrip())
    return " ".join(reversed(out))


def test_the_thematic_comment_states_the_invariant_not_a_count():
    """#186. It said "the 2 mislocation rows" while the register derives 1 — a count in
    a comment beside a computation, maintainable only by hand and guarded by nothing.

    THE FIX WAS TO REMOVE THE FIGURE, NOT UPDATE IT. Updating defers the same failure to
    the next time a position moves; removing it ends the class."""
    c = _thematic_comment()
    assert "mislocation" in c.lower(), "the comment no longer describes the row set"
    assert not re.search(r"\d+\s+mislocation", c, re.I), (
        f"a count is back in the thematic comment: {c!r}")


def test_the_thematic_comment_keeps_the_reason_the_caption_is_mandatory():
    """THE OTHER HALF, and the trap #186 sets. A stale figure inside a TRUE sentence
    does not make the sentence false — the point (population deliberately exceeds the
    register rows, so a caption is required) was always right and is worth keeping.
    Deleting the comment to remove the number would lose what it was for."""
    c = _thematic_comment().lower()
    # ASSERT THE CLAIM, NOT A SUBSTRING. The first version checked `"caption" in c` and a
    # mutant deleting the live sentence SURVIVED it — because `resolve_caption` is named
    # two lines below, so the word persisted inside an IDENTIFIER. Same shape as a source
    # pin satisfied by a helper's definition: presence is not assertion.
    assert re.search(r"caption is (mandatory|required)", c), (
        f"the comment no longer says a caption is MANDATORY: {c!r}")
    # And the invariant must be stated about the live rule, not only recalled in the
    # #186 record paragraph below it, which also contains the word "exceeds".
    assert "always fewer" in c, (
        "the comment no longer states that the register rows are always fewer than the "
        "population — the invariant that motivates the caption")


def test_the_invariant_the_comment_states_actually_holds():
    """Assert the claim, not just its presence. The comment says the matched-symbol
    population exceeds the register rows; if that ever stopped being true the comment
    would be wrong in a way no prose check could see."""
    import pandas as pd
    import src.household as hh
    import src.location_actions as la
    import src.location_config as lc
    from src.db import get_connection
    from src.household import exclude_non_household_positions
    from src.household_data import find_latest_positions_csv, load_latest_positions
    if find_latest_positions_csv() is None:
        pytest.skip("personal-mode inputs absent")
    pos, _c, _a = load_latest_positions()
    with get_connection() as conn:
        acc = pd.read_sql_query(
            "SELECT account_id, name, type, custodian, is_active, created_at, "
            "tax_treatment, pseudonym, display_name, managed_by, included_in_household "
            "FROM accounts", conn)
        sec = pd.read_sql_query("SELECT * FROM securities", conn)
        comp = pd.read_sql_query("SELECT * FROM fund_compositions", conn)
    reg = hh.build_location_register(
        exclude_non_household_positions(pos, acc), acc, sec, lc.TAX_PROFILE,
        lc.SLEEVE_PRIORITY_BY_ACCOUNT_TYPE, lc.ACCOUNT_SHELTER_PRIORITY,
        compositions_df=comp)
    grp = next(g for g in la.ACTION_GROUPS if g.get("key") == "thematic_sprawl")
    rows = la.filter_register_for_group(reg, grp)
    assert len(grp["symbols"]) > len(rows), (
        f"the population ({len(grp['symbols'])}) no longer exceeds the register rows "
        f"({len(rows)}) — the comment's invariant has stopped holding")
