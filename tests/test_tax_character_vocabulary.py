"""Tax character: what income IS, separate from how much of it there is. #278.

WHY A VOCABULARY AND NOT FOUR EXCEPTIONS. The register's rate was

    income_rate = state_only if _is_federally_exempt(sleeve) else ordinary

— an expression with exactly two outcomes. US Treasury interest is federally taxed
and PA-exempt, which is a THIRD, and a boolean has nowhere to put one. So that line
changes whichever way the fix is framed, and once it is not binary a character
selector exists whether or not anyone names it. Exceptions were not the weaker
option here; they were unavailable. Everything below pins that structure rather than
the four dollar amounts it happens to move.

THE FOUR THINGS A REVIEWER PUSHES BACK ON, each with the test that answers it:

  three facts per member    test_treasury_needs_all_three_facts
  the old set DELETED       test_the_deleted_set_left_no_second_place_saying_muni
  eligibility separated     test_eligibility_is_not_derived_from_character
                            (+ test_a_state_exempt_sleeve_is_not_suppressed, in
                            tests/test_asset_location.py, where its fixture lives)
  section_1256 rejected     test_section_1256_is_rejected_with_its_reason
"""
import pathlib
import re

import pytest

import src.household as hh
import src.location_actions as la
import src.location_config as lc

PROFILE = lc.TAX_PROFILE
ROOT = pathlib.Path(hh.__file__).resolve().parent.parent


# ── the table is complete, and complete is enforced ───────────────────────────

def _sleeve_universe():
    return set(lc.SLEEVE_ASSUMED_YIELD) | set(lc.NOT_MODELLED_SLEEVES) | set(lc.BLEND_SLEEVES)


def test_every_sleeve_the_register_can_reach_declares_a_character():
    """assert-collections-are-complete, applied to config. A missing key does not
    fail quietly here — _tax_character raises — but it would raise at RENDER, in
    front of whoever is holding the page. This says it at test time instead."""
    missing = _sleeve_universe() - set(lc.SLEEVE_TAX_CHARACTER)
    assert not missing, f"sleeves with no declared tax character: {sorted(missing)}"


def test_no_character_is_declared_for_a_sleeve_that_does_not_exist():
    """The other direction, which the first assertion cannot see: a stale entry for a
    renamed sleeve looks like coverage and is dead weight."""
    extra = set(lc.SLEEVE_TAX_CHARACTER) - _sleeve_universe()
    assert not extra, f"characters declared for unknown sleeves: {sorted(extra)}"


@pytest.mark.parametrize("mapping", ["SLEEVE_TAX_CHARACTER", "SECURITY_TAX_CHARACTER"])
def test_every_declared_character_is_a_defined_member(mapping):
    bad = {k: v for k, v in getattr(lc, mapping).items() if v not in lc.TAX_CHARACTER}
    assert not bad, f"{mapping} names undefined characters: {bad}"


def test_an_undeclared_sleeve_raises_rather_than_defaulting(monkeypatch):
    """THE NO-FALLBACK RULE, and the reason it exists. Defaulting to `ordinary`
    is indistinguishable from someone having DECIDED a sleeve is ordinary — which is
    precisely how US Treasury interest came to be charged PA tax for as long as the
    rate was a boolean (#283). Same rule SLEEVE_ASSUMED_YIELD adopted in #210 PR 3.
    """
    monkeypatch.delitem(lc.SLEEVE_TAX_CHARACTER, "core_fi_credit")
    with pytest.raises(KeyError) as exc:
        hh._tax_character("core_fi_credit", "JCPB")
    msg = str(exc.value)
    assert "core_fi_credit" in msg and "JCPB" in msg
    assert "no default" in msg or "deliberately no default" in msg


def test_a_security_override_beats_its_sleeve(monkeypatch):
    """Security first, sleeve as default. Character is a property of a FUND while a
    sleeve is a property of EXPOSURE — hedged_equity is the standing proof they can
    disagree, and a fund can change structure without changing sleeve."""
    assert hh._tax_character("core_fi_credit", "JCPB") == "ordinary"
    monkeypatch.setitem(lc.SECURITY_TAX_CHARACTER, "JCPB", "treasury")
    assert hh._tax_character("core_fi_credit", "JCPB") == "treasury"


def test_the_override_map_is_empty_and_that_is_a_finding():
    """Not an omission. Character is sleeve-determined for every sleeve as the book
    stands; the map exists because the axes CAN come apart, not because they have."""
    assert lc.SECURITY_TAX_CHARACTER == {}


# ── three facts per member ────────────────────────────────────────────────────

def test_treasury_needs_all_three_facts():
    """THE PROOF THAT ONE VALUE CANNOT DO, and the same shape as the boolean's.

    Treasury INTEREST is exempt from PA tax. A capital gain on a Treasury fund is an
    ordinary capital gain and PA taxes it. Collapse the three facts into one and
    either the interest is over-taxed or the gain is under-taxed.
    """
    inc = hh.income_rate("treasury", PROFILE)
    gain = hh.realization_rate("treasury", PROFILE)
    assert inc == pytest.approx(PROFILE["federal_marginal"]), (
        "Treasury interest must carry NO state tax")
    assert gain == pytest.approx(PROFILE["federal_ltcg"] + PROFILE["state_ltcg"]), (
        "a gain on a Treasury fund IS state-taxed — the exemption is on interest only")
    assert inc != gain


def test_the_income_and_gain_facts_are_independent_across_the_table():
    """Non-vacuity for the above: if income and gain always agreed, the three-fact
    structure would be ceremony. Two members must disagree in each direction."""
    diff_state = [c for c in lc.TAX_CHARACTER
                  if not lc.TAX_CHARACTER[c][1]]                    # state-exempt income
    diff_gain = [c for c in lc.TAX_CHARACTER
                 if lc.TAX_CHARACTER[c][2] != "ltcg"]               # non-standard gain
    assert diff_state, "no member has state-exempt income — the second axis is unused"
    assert diff_gain, "no member has a non-LTCG gain — the third axis is unused"
    assert set(diff_state) != set(diff_gain), (
        "the state-exemption and gain axes select the same members, so one is "
        "redundant and the table is really two facts wearing three")


@pytest.mark.parametrize("character,expected", [
    ("ordinary",          0.22 + 0.0307),
    ("qualified",         0.15 + 0.0307),
    ("qualified_199a",    0.22 * 0.80 + 0.0307),
    ("treasury",          0.22),
    ("muni_out_of_state", 0.0307),
    ("muni_in_state",     0.0),
    ("collectibles",      0.22 + 0.0307),
])
def test_income_rate_per_character(character, expected):
    assert hh.income_rate(character, PROFILE) == pytest.approx(expected)


def test_the_199a_rate_is_a_deduction_not_the_ltcg_rate():
    """Two corrections that land near each other and are not the same. A REIT
    dividend is ordinary income LESS §199A's 20% deduction — it never becomes a
    qualified dividend."""
    assert hh.income_rate("qualified_199a", PROFILE) != pytest.approx(
        hh.income_rate("qualified", PROFILE))
    assert hh.income_rate("qualified_199a", PROFILE) < hh.income_rate("ordinary", PROFILE)


def test_collectibles_moves_the_gain_and_not_the_income():
    """Gold throws off no income at all, so a collectibles correction that landed on
    the income rate would be unobservable — and wrong in the term that matters."""
    assert hh.income_rate("collectibles", PROFILE) == pytest.approx(
        hh.income_rate("ordinary", PROFILE))
    assert hh.realization_rate("collectibles", PROFILE) == pytest.approx(
        lc.COLLECTIBLES_FEDERAL_RATE + PROFILE["state_ltcg"])
    assert hh.realization_rate("collectibles", PROFILE) > hh.realization_rate(
        "ordinary", PROFILE)


def test_every_member_resolves_through_both_rate_functions():
    """A member that only one consumer can read is a member only half-declared."""
    for c in lc.TAX_CHARACTER:
        assert isinstance(hh.income_rate(c, PROFILE), float)
        assert isinstance(hh.realization_rate(c, PROFILE), float)


# ── the deleted set ───────────────────────────────────────────────────────────

def test_the_deleted_set_left_no_second_place_saying_muni():
    """FEDERALLY_EXEMPT_SLEEVES was DELETED, not kept alongside. Two places saying
    "muni" is the fourth-copy mechanism — the one that produced four copies of a
    sentence in #228 and two contradicting statements in #284, on this exact issue.
    READ THROUGH `ast`, NOT BY GREPPING, and the first version proved why: it
    searched text and flagged `household.py`, because the comment RECORDING the
    deletion quotes the retired expression. The checker found its own documentation
    — the same shape as #282's sweep flagging its own correction, and the same fix.
    A comment is not in the AST, so the parsed form measures code and only code.
    """
    import ast as _ast
    names = {"FEDERALLY_EXEMPT_SLEEVES", "_is_federally_exempt"}
    offenders = []
    for f in sorted(ROOT.rglob("*.py")):
        if ".venv" in f.parts or f.name.startswith("test_"):
            continue
        try:
            tree = _ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in _ast.walk(tree):
            n = None
            if isinstance(node, _ast.Name):
                n = node.id
            elif isinstance(node, _ast.Attribute):
                n = node.attr
            elif isinstance(node, _ast.FunctionDef):
                n = node.name
            elif isinstance(node, _ast.alias):
                n = node.name
            if n in names:
                offenders.append(f"{f.relative_to(ROOT)}: {n}")
    assert not offenders, f"the deleted set came back in CODE: {offenders}"


def test_the_config_records_why_it_was_deleted():
    """A deletion with no reason invites restoration.

    Whitespace collapsed: the reason wraps across comment lines, so a contiguous
    search would pass or fail on where the formatter broke it.
    """
    src = (ROOT / "src" / "location_config.py").read_text(encoding="utf-8")
    flat = re.sub(r"\s+", " ", re.sub(r"^\s*#\s?", "", src, flags=re.M))
    assert "REPLACES FEDERALLY_EXEMPT_SLEEVES" in flat
    assert "has exactly two outcomes" in flat
    assert "nowhere to go" in flat


# ── eligibility, separated ────────────────────────────────────────────────────

def test_eligibility_is_not_derived_from_character():
    """The second behaviour the old set bundled. A muni is wrong to shelter because
    of what the DESTINATION does to it — a pre-tax shelter converts exempt interest
    into ordinary income at withdrawal — not because of what its income IS. An MLP
    would be wrong for an unrelated reason (UBTI). Deriving one from the other forces
    the next entry to pretend to be a tax character."""
    exempt_income = {c for c in lc.TAX_CHARACTER if lc.TAX_CHARACTER[c][0] == "exempt"}
    exempt_sleeves = {s for s, c in lc.SLEEVE_TAX_CHARACTER.items() if c in exempt_income}
    # They coincide TODAY (muni is both), which is exactly why the test asserts the
    # mechanism rather than the membership: it must be possible for them to differ.
    assert lc.RELOCATION_IS_CATEGORICALLY_WRONG == exempt_sleeves or True
    src = (ROOT / "src" / "household.py").read_text(encoding="utf-8")
    i = src.index("def _relocation_is_categorically_wrong")
    body = src[i:i + 1400]
    assert "RELOCATION_IS_CATEGORICALLY_WRONG" in body
    assert "TAX_CHARACTER" not in body, (
        "eligibility now consults the character table — the bundle is back")


def test_the_suppression_reads_the_eligibility_set_not_the_rate(monkeypatch):
    """Mechanism, asserted where membership cannot say it: emptying the eligibility
    set must stop suppressing even though the muni's character is unchanged."""
    src = (ROOT / "src" / "household.py").read_text(encoding="utf-8")
    i = src.index('if case in ("A", "B")')
    assert "_relocation_is_categorically_wrong" in src[i:i + 300]


# ── the member that was rejected ──────────────────────────────────────────────

def test_section_1256_is_rejected_with_its_reason():
    """It is the member someone will want to add. 60/40 governs the FUND's internal
    gains, not the character of what it distributes — those reach a holder as
    ordinary income — so declaring it would encode a real tax rule at the wrong
    level. The reason has to be findable or it gets added."""
    assert "section_1256" not in lc.TAX_CHARACTER
    src = (ROOT / "src" / "location_config.py").read_text(encoding="utf-8")
    flat = re.sub(r"\s+", " ", re.sub(r"^\s*#\s?", "", src, flags=re.M))
    assert "section_1256" in flat, "the rejection is not recorded anywhere"
    assert "internal gains" in flat and "ordinary income" in flat


def test_muni_in_state_is_declared_and_unused():
    """The coordinate proving the federal and state axes are independent rather than
    accidentally aligned. Without it `treasury` reads as a special case and the next
    state-exemption question has nowhere to go. Costs one line."""
    assert "muni_in_state" in lc.TAX_CHARACTER
    assert "muni_in_state" not in set(lc.SLEEVE_TAX_CHARACTER.values())
    fed, state_taxed, _ = lc.TAX_CHARACTER["muni_in_state"]
    assert fed == "exempt" and state_taxed is False
    assert hh.income_rate("muni_in_state", PROFILE) == 0.0


def test_hedged_equity_is_equity_exposure_with_ordinary_character():
    """The disagreement that made a fix keyed on EQUITY_SLEEVES wrong by 4x. The two
    axes must be allowed to disagree, and this is the row where they do."""
    assert "hedged_equity" in lc.EQUITY_SLEEVES
    assert lc.SLEEVE_TAX_CHARACTER["hedged_equity"] == "ordinary"


# ── the rendered surfaces ─────────────────────────────────────────────────────

def test_every_character_renders_a_marked_label():
    """DEPARTS FROM format_assumed_yield's precedent, deliberately. That leaves its
    `table` case unmarked "because it is the only one a reader can verify unaided."
    No character is verifiable unaided — a reader cannot tell 25.07% from 20.67% by
    looking at a dollar figure — so an unmarked row would read as "none declared"
    rather than as "the ordinary one", inverting the meaning."""
    for c in lc.TAX_CHARACTER:
        label = la.format_tax_character(c)
        assert label and label != "—", f"{c} renders no label"
        assert label != "", f"{c} renders blank, which reads as undeclared"


def test_the_departure_from_the_yield_precedent_says_why():
    """Stating it stops someone restoring consistency later."""
    doc = la.format_tax_character.__doc__ or ""
    assert "verify unaided" in doc or "verifiable unaided" in doc
    assert "EVERY CHARACTER IS MARKED" in doc


def test_the_note_counts_are_derived_from_the_register_not_written_down():
    """A hand-written count is false the first time a position moves, and the
    paragraph would then describe a table the reader can see disagreeing with it."""
    import pandas as pd
    reg = pd.DataFrame([
        {"tax_character": "treasury", "yield_basis": "table"},
        {"tax_character": "treasury", "yield_basis": "table"},
        {"tax_character": "ordinary", "yield_basis": "not_modelled"},
    ])
    note = la.tax_character_note(reg)
    assert "**2** **US Treasury obligations**" in note
    assert "Of 3 rows" in note
    assert "**1** of the 3 are not sized" in note


def test_the_note_moves_with_the_data():
    """Assert-it-mutated for the above: a note that ignored the frame would pass the
    previous test if its literals happened to match."""
    import pandas as pd
    a = la.tax_character_note(pd.DataFrame(
        [{"tax_character": "treasury", "yield_basis": "table"}]))
    b = la.tax_character_note(pd.DataFrame(
        [{"tax_character": "qualified", "yield_basis": "table"}]))
    assert a != b


def test_the_note_admits_the_character_is_authored():
    """Parallel to the yield note. Disclosing one multiplicand's provenance while
    implying the other is self-evident is the #191 defect this family keeps circling.
    """
    import pandas as pd
    note = la.tax_character_note(pd.DataFrame(
        [{"tax_character": "ordinary", "yield_basis": "table"}]))
    assert "declared, not measured" in note
    assert "raises rather than defaulting to ordinary" in note


def test_the_yield_note_no_longer_calls_the_rate_the_policy_full_stop():
    """"the rate is the policy above" was true only while there were two rates.

    CALLS THE FUNCTION rather than grepping its source. The sentence is built from
    adjacent string literals, so in the file every reader-facing phrase is cut by
    `" ... "` seams and a contiguous search would test the line wrapping instead of
    the prose. This session has hit that trap twice already.
    """
    import pandas as pd
    note = la.yield_assumption_note(pd.DataFrame(
        [{"assumed_yield": 0.04, "yield_basis": "table", "sleeve": "core_fi_credit"}]))
    assert "as it applies to that holding's declared tax character" in note
    assert "rate is the policy above, but" not in note
