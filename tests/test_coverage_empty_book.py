"""The three empty-book states are distinguishable, and none of them is "seed it".

Unit half of #260. These run in CI (demo mode, no tracker.db) because they build
PriceCoverage records directly; the render half in
tests/render/test_empty_book_render.py needs personal-mode fixtures and skips
there. Keeping both is deliberate — the unit tests pass on a helper the page might
never call, and the render tests prove the page calls it. Neither substitutes.
"""
import pytest

from src.coverage import (
    EMPTY_CASH_ONLY,
    EMPTY_NO_HOLDINGS,
    EMPTY_UNPRICED,
    PriceCoverage,
    Unresolved,
    empty_book_note,
    empty_book_state,
)

AS_OF = "2026-08-19"
HELD = ("VOO", "VEA", "VTV")

# The sentence this change deletes. Asserted absent from every state, because the
# finding that justifies rewriting both messages is that it had NO true case.
OLD = "Seed the database first"


def _unpriced() -> PriceCoverage:
    return PriceCoverage(
        requested=HELD,
        resolved=(),
        unresolved=tuple(Unresolved(t, "no_cached_rows") for t in HELD),
        as_of_requested=AS_OF,
        frontier_served=None,
    )


def _no_holdings() -> PriceCoverage:
    return PriceCoverage(requested=(), resolved=(), unresolved=(),
                         as_of_requested=AS_OF, frontier_served=None)


def _cash_only() -> PriceCoverage:
    return PriceCoverage(requested=("BIL",), resolved=("BIL",), unresolved=(),
                         as_of_requested=AS_OF, frontier_served=AS_OF)


ALL_STATES = [
    (_unpriced, EMPTY_UNPRICED),
    (_no_holdings, EMPTY_NO_HOLDINGS),
    (_cash_only, EMPTY_CASH_ONLY),
]


@pytest.mark.parametrize("build,expected", ALL_STATES)
def test_each_condition_is_classified_as_itself(build, expected):
    assert empty_book_state(build()) == expected


def test_the_three_states_are_mutually_distinguishable():
    """A classifier that answered the same thing everywhere would satisfy every
    per-state assertion above and still be useless. Assert the CONTRAST."""
    states = [empty_book_state(build()) for build, _ in ALL_STATES]
    assert len(set(states)) == 3, f"states collapsed: {states}"


@pytest.mark.parametrize("build,_expected", ALL_STATES)
def test_no_state_tells_the_reader_to_seed_the_database(build, _expected):
    """The deleted sentence had no true case — not one where it was merely
    imprecise. It must not survive in any branch."""
    assert OLD not in empty_book_note(build())


@pytest.mark.parametrize("build,_expected", ALL_STATES)
def test_every_state_produces_a_non_empty_note(build, _expected):
    note = empty_book_note(build())
    assert note and note.strip(), "an empty note renders as no disclosure at all"


def test_unpriced_note_carries_what_it_knows():
    """holdings present, none priced, the reason, and the absent frontier — the
    four facts the reader needs to not conclude the portfolio is empty."""
    note = empty_book_note(_unpriced())
    assert str(len(HELD)) in note, "must say how many positions are held"
    for ticker in HELD:
        assert ticker in note, f"{ticker} is held and unpriced; name it"
    assert "no_cached_rows" in note, "must carry the reason, not just the fact"
    assert "No committed price date is available" in note, "frontier is absent"


def test_unpriced_note_blocks_the_empty_portfolio_inference():
    """#251's third clause. Derivable from neither the code nor the data — it
    encodes what the reader will otherwise assume, so it needs its own assertion
    or a future edit will drop it as redundant prose."""
    note = empty_book_note(_unpriced())
    assert "exist and are not displayed" in note
    assert "not an empty or unseeded portfolio" in note


def test_unpriced_note_says_figures_were_withheld_not_zero():
    """"Counted as zero" is the failure being disclosed, not the remedy. The note
    must say the figures are withheld, or a reader reasonably reads zero weights
    as measured zeros."""
    assert "withheld" in empty_book_note(_unpriced())


def test_no_holdings_note_states_a_fact_and_gives_no_instruction():
    """The only condition that legitimately reaches the empty branch. It describes
    the book; it does not tell the reader to go set something up."""
    note = empty_book_note(_no_holdings())
    assert AS_OF in note, "a claim about what is held must carry its date"
    assert "ledger" in note
    for imperative in ("Seed", "Add ", "Import", "Run ", "first."):
        assert imperative not in note, f"instruction leaked into a statement: {imperative!r}"


def test_cash_only_note_explains_why_weights_are_undefined_not_zero():
    note = empty_book_note(_cash_only())
    assert "cash" in note.lower()
    assert "undefined" in note, "zero and undefined are different claims"


def test_notes_differ_across_all_three_states():
    """The classifier can be right while every branch renders the same sentence.
    Three distinct states must produce three distinct notes."""
    notes = [empty_book_note(build()) for build, _ in ALL_STATES]
    assert len(set(notes)) == 3, "two states render the same text"
