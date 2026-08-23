"""A yield declares HOW it was arrived at, not just where it is stored. #289.

`YIELD_BASES` held `table`, documented as "the sleeve has an entry in
SLEEVE_ASSUMED_YIELD" — a fact about STORAGE. It spanned three provenances that
rendered identically, so a reader could not tell a measured 3.97% from an authored
6.00%. The disclosure paragraph had counted proxied / constructed / authored since
#213, so the note and the per-row column disagreed about how many kinds existed.

THE DATE IS THE FINDING. A measured value's date is a fact about the WORLD; an
authored value's date can only be a fact about the AUTHOR — and only one of them
decays. A constructed entry drifts because the world moves (#231 measured a year of
TIPS drift at a median 31bp); an authored value cannot drift, because there is nothing
it could drift FROM. Every consequence follows from that one sentence: the key is
`authored` and not `as_of`, the date is 2026-06-01 and not today, and it renders in
the Assumptions note rather than beside the figure.
"""
import pathlib
import re

import pandas as pd
import pytest

import src.household as hh
import src.location_actions as la
import src.location_config as lc
from src.household import YIELD_BASES

ROOT = pathlib.Path(lc.__file__).resolve().parent.parent


# ── the four maps partition the table ─────────────────────────────────────────

def test_every_yield_declares_exactly_one_basis():
    """assert-collections-are-complete, both directions. An entry in the table and in
    no basis map is the defect this PR removes; an entry in two is ambiguous."""
    t = set(lc.SLEEVE_ASSUMED_YIELD)
    p, c = set(lc.SLEEVE_YIELD_PROXY), set(lc.SLEEVE_YIELD_CONSTRUCTION)
    a, s = set(lc.SLEEVE_YIELD_AUTHORED), set(lc.SLEEVE_YIELD_STRUCTURAL)
    assert p | c | a | s == t, (
        f"undeclared: {sorted(t - (p|c|a|s))}; declared but not in the table: "
        f"{sorted((p|c|a|s) - t)}")
    for x, y, nx, ny in ((p, c, "proxy", "constructed"), (p, a, "proxy", "authored"),
                         (p, s, "proxy", "structural"), (c, a, "constructed", "authored"),
                         (c, s, "constructed", "structural"), (a, s, "authored", "structural")):
        assert not (x & y), f"{sorted(x & y)} is in both {nx} and {ny}"


def test_a_yield_with_no_declared_basis_raises(monkeypatch):
    """No fallback, and this is a DIFFERENT case from #210's raise. That one fires for
    a sleeve absent from the table entirely (a ValueError naming three config sets).
    This one fires for a sleeve that HAS a yield and declares no basis — the state this
    PR made unrepresentable, and the one an omission would create."""
    monkeypatch.setitem(lc.SLEEVE_ASSUMED_YIELD, "zzz_undeclared", 0.04)
    with pytest.raises(KeyError) as exc:
        hh._assumed_yield_with_source("zzz_undeclared", "ZZZ")
    msg = str(exc.value)
    assert "zzz_undeclared" in msg
    assert "SLEEVE_YIELD_STRUCTURAL" in msg, "the raise must name every basis map"


def test_a_sleeve_absent_from_the_table_still_gets_210s_raise():
    """The neighbouring case, pinned so the two do not collapse into one another —
    #292 established that resolution ORDER decides which diagnostic a reader gets."""
    with pytest.raises(ValueError) as exc:
        hh._assumed_yield_with_source("no_such_sleeve_zzz", "ZZZ")
    assert "SLEEVE_ASSUMED_YIELD" in str(exc.value)


def test_the_basis_vocabulary_is_closed_at_six():
    assert YIELD_BASES == frozenset({"proxy", "constructed", "authored", "structural",
                                     "look_through", "not_modelled"})
    assert "table" not in YIELD_BASES, (
        "`table` names storage, not provenance — it is what let an authored 6.00% "
        "render exactly like a measured 3.97%")


# ── the date, which is the finding ────────────────────────────────────────────

def test_the_key_is_authored_not_as_of():
    """A DIFFERENT KEY ON PURPOSE. `as_of` would invite comparison against the proxy
    map's as-of, and those two dates answer different questions: one says when the
    world was measured, the other how long a judgement has gone unexamined."""
    for m in (lc.SLEEVE_YIELD_AUTHORED, lc.SLEEVE_YIELD_STRUCTURAL):
        for sleeve, e in m.items():
            assert "authored" in e, f"{sleeve} declares no authored date"
            assert "as_of" not in e, (
                f"{sleeve} uses `as_of` — an authored date is not a vintage, and the "
                f"key name is what stops it being read as one")


def test_the_date_is_when_the_judgement_was_made_not_when_it_was_declared():
    """2026-06-01 is `6cd6f99`, where fifteen yields arrived together, every one an
    exact multiple of 0.5%. Stamping the declaring PR's date would claim twelve
    reviews that did not happen — declaring something authored is not reviewing it."""
    import datetime
    dates = {e["authored"] for m in (lc.SLEEVE_YIELD_AUTHORED, lc.SLEEVE_YIELD_STRUCTURAL)
             for e in m.values()}
    assert dates == {"2026-06-01"}, f"unexpected authored dates: {sorted(dates)}"
    assert datetime.date.fromisoformat("2026-06-01") < datetime.date(2026, 8, 22), (
        "the authored date is not in the past relative to this PR — it has been "
        "restamped to the declaration date, which claims reviews that did not happen")


def test_the_date_is_never_rendered_beside_the_figure():
    """It must NOT read as a staleness warning. "authored 82 days ago" next to a number
    invites the inference that the value has drifted out of date — wrong in a harmful
    direction, because the failure mode is that it may never have been right and
    elapsed time neither creates nor cures that."""
    for basis in ("authored", "structural"):
        rendered = la.format_assumed_yield(0.06, basis)
        assert not re.search(r"\d{4}-\d{2}-\d{2}", rendered), (
            f"{basis} renders a date beside the figure: {rendered!r}")
        assert "ago" not in rendered and "stale" not in rendered.lower()


def test_the_authored_entries_record_why_without_inventing_evidence():
    """`why` says what KIND of claim the value is. It must not supply a corroborating
    fact invented at declaration time — #290 found the tax-character comment justified
    by "ELN income is ordinary" where `grep` returns one hit, that comment."""
    for sleeve, e in lc.SLEEVE_YIELD_AUTHORED.items():
        assert e.get("why"), f"{sleeve} records no reason"
        assert "JUDGED" in e["why"], f"{sleeve} does not say what kind of claim it is"


# ── structural is a separate kind, and the render is why ──────────────────────

def test_structural_says_why_the_number_is_not_a_measurement():
    """NOT "(structural)". A zero labelled with a category name still reads as an
    estimate of zero; the suffix has to say there was never a cash flow to measure."""
    assert la.format_assumed_yield(0.0, "structural") == "0.00% (no cash flow to yield)"
    for sleeve, e in lc.SLEEVE_YIELD_STRUCTURAL.items():
        assert "why" in e, f"{sleeve} records no reason"


def test_gold_renders_structural_and_not_authored():
    """THE RENDER DECIDED THIS KIND. The first draft folded structural into authored,
    and IAU — a live register row — came out as "0.00% (authored)", which reads as
    "somebody estimated zero" where bullion has no cash flow to yield.

    Asserted on the RESOLVER's output for the real sleeve, not on the constant: a
    mutant folding the maps together leaves the constant intact and changes only what
    a reader sees."""
    y, basis = hh._assumed_yield_with_source("real_assets_gold", "IAU")
    assert basis == "structural"
    assert la.format_assumed_yield(y, basis) == "0.00% (no cash flow to yield)"


def test_the_two_kinds_render_differently():
    """Assert-it-mutated: every assertion above is worthless if both suffixes agree."""
    assert (la.format_assumed_yield(0.0, "structural")
            != la.format_assumed_yield(0.0, "authored"))


# ── the marker rule ───────────────────────────────────────────────────────────

def test_only_undeclared_kinds_are_marked():
    """Unmarked means DECLARED — a proxy or a construction. The old justification
    ("the only one a reader can verify unaided") did not track verifiability at all:
    `constructed` is the most checkable and would be the one left unmarked under it."""
    assert la.format_assumed_yield(0.0397, "proxy") == "3.97%"
    assert la.format_assumed_yield(0.0461, "constructed") == "4.61%"
    assert la.format_assumed_yield(0.060, "authored") == "6.00% (authored)"
    assert la.format_assumed_yield(0.0, "structural") == "0.00% (no cash flow to yield)"


def test_the_docstring_records_that_the_old_justification_was_wrong():
    """Wrong, not incomplete — and it was approved in #292. Saying which stops someone
    restoring it, and stops a later consistency pass marking all four kinds."""
    doc = la.format_assumed_yield.__doc__ or ""
    assert "WRONG, NOT MERELY INCOMPLETE" in doc
    assert "does not transfer" in doc and "format_tax_character" in doc


# ── the note: templated, and it reconciles ────────────────────────────────────

def _reg(rows):
    return pd.DataFrame(rows)


def test_the_note_counts_reconcile_to_the_row_count():
    """THE RECONCILIATION. A claim about how many entries are of each kind, in prose,
    beside a table that knows — the same shape that shipped 12/4 against a real 14/6
    in #285's design note. Every row must be accounted for by exactly one clause."""
    rows = [{"sleeve": "us_large_core", "yield_basis": "proxy", "assumed_yield": 0.0092},
            {"sleeve": "tips", "yield_basis": "constructed", "assumed_yield": 0.0461},
            {"sleeve": "hedged_equity", "yield_basis": "authored", "assumed_yield": 0.06},
            {"sleeve": "real_assets_gold", "yield_basis": "structural", "assumed_yield": 0.0},
            {"sleeve": "multi_asset", "yield_basis": "look_through", "assumed_yield": 0.03},
            {"sleeve": "thematic", "yield_basis": "not_modelled", "assumed_yield": None}]
    note = la.yield_assumption_note(_reg(rows))
    claimed = [int(m.group(1)) for m in re.finditer(r"\*\*(\d+) of 6 rows?\*\*", note)]
    assert sum(claimed) == len(rows), (
        f"the note's clauses claim {sum(claimed)} of {len(rows)} rows — "
        f"{len(rows) - sum(claimed)} are described by nothing. Claims: {claimed}")


def test_the_note_counts_are_templated_not_written():
    """Assert-it-mutated for the reconciliation: a note with the right literals would
    satisfy the test above on this fixture and be wrong on every other frame."""
    one = la.yield_assumption_note(_reg(
        [{"sleeve": "hedged_equity", "yield_basis": "authored", "assumed_yield": 0.06}]))
    two = la.yield_assumption_note(_reg(
        [{"sleeve": "hedged_equity", "yield_basis": "authored", "assumed_yield": 0.06},
         {"sleeve": "liquid_alt", "yield_basis": "authored", "assumed_yield": 0.02}]))
    assert "**1 of 1 rows**" in one
    assert "**2 of 2 rows**" in two


def test_the_note_states_the_date_and_denies_it_is_a_vintage():
    note = la.yield_assumption_note(_reg(
        [{"sleeve": "hedged_equity", "yield_basis": "authored", "assumed_yield": 0.06}]))
    assert "2026-06-01" in note
    assert "not a vintage" in note
    assert "unexamined" in note, "the date must say what it DOES mean, not only what it does not"


def test_the_note_no_longer_says_the_authored_rows_carry_no_date():
    """A sentence that this PR made false. It read "the authored rows carry **no date
    at all**" — true until SLEEVE_YIELD_AUTHORED existed."""
    note = la.yield_assumption_note(_reg(
        [{"sleeve": "tips", "yield_basis": "constructed", "assumed_yield": 0.0461},
         {"sleeve": "hedged_equity", "yield_basis": "authored", "assumed_yield": 0.06}]))
    assert "no date at all" not in note


def test_the_note_says_they_were_authored_wholesale():
    """Derived, not asserted — it fires only because every stamp is the same day. A map
    with mixed dates renders a range instead, so the evidence and the sentence cannot
    drift apart."""
    note = la.yield_assumption_note(_reg(
        [{"sleeve": "hedged_equity", "yield_basis": "authored", "assumed_yield": 0.06},
         {"sleeve": "liquid_alt", "yield_basis": "authored", "assumed_yield": 0.02}]))
    assert "all on the same day" in note and "wholesale" in note


# ── zero-suppression, both directions ─────────────────────────────────────────

@pytest.mark.parametrize("kind,sleeve,phrase", [
    ("authored",    "hedged_equity",    "use an **authored** yield"),
    ("structural",  "real_assets_gold", "no cash flow to yield"),
    ("proxy",       "us_large_core",    "declared basis"),
    ("constructed", "tips",             "constructed** yield"),
])
def test_a_clause_renders_when_its_kind_is_present(kind, sleeve, phrase):
    """BOTH DIRECTIONS, and this is the half that was missing. A suppression that
    fires ALWAYS is the same defect as one that never fires — the first hides a real
    population, the second claims an empty one."""
    note = la.yield_assumption_note(_reg(
        [{"sleeve": sleeve, "yield_basis": kind, "assumed_yield": 0.04}]))
    assert phrase in note, f"{kind} is present and its clause did not render"


@pytest.mark.parametrize("kind,phrase", [
    ("authored",    "use an **authored** yield"),
    ("structural",  "no cash flow to yield"),
    ("proxy",       "declared basis"),
    ("constructed", "constructed** yield"),
])
def test_a_clause_is_suppressed_when_its_kind_is_absent(kind, phrase):
    """A register carrying only look-through and not-modelled rows must claim nothing
    about the four table kinds. A clause describing a population of zero is a claim
    the artifact does not support — the same rule format_drag_exclusion follows by
    returning None rather than reporting an exclusion of nothing."""
    note = la.yield_assumption_note(_reg([
        {"sleeve": "multi_asset", "yield_basis": "look_through", "assumed_yield": 0.03},
        {"sleeve": "thematic", "yield_basis": "not_modelled", "assumed_yield": None},
    ]))
    assert phrase not in note, f"{kind}'s clause rendered with no {kind} row present"


def test_no_clause_claims_a_count_of_zero():
    """The general form, over every clause at once: no "0 of N" may ever render."""
    for reg in (_reg([]),
                _reg([{"sleeve": "thematic", "yield_basis": "not_modelled",
                       "assumed_yield": None}])):
        note = la.yield_assumption_note(reg)
        assert not re.search(r"\*\*0 of \d+ rows?\*\*", note), (
            f"a zero count rendered: {note[:200]!r}")


def test_the_empty_register_still_states_the_mechanism():
    """Suppressing the COUNTS must not suppress the disclosure. An empty register is a
    legitimate state (nothing mislocated), and the reader is still owed how the number
    would be built if there were one."""
    note = la.yield_assumption_note(_reg([]))
    assert "position value" in note and "authored assumption" in note.lower()
    assert not re.search(r"\*\*\d+ of \d+ rows?\*\*", note), (
        "an empty register rendered a population count")
