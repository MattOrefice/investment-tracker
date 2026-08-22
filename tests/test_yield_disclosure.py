"""#191 — the assumed yield must be visible where it is applied and disclosed.

`annual_benefit = position value × assumed sleeve yield × tax rate`. The value is
live from the positions CSV and the rate is tax policy, but the yield is an authored
constant in src/location_config.py with no declared basis. Page 14's Assumptions
expander enumerated every tax rate, said nothing about a yield, and closed by
asserting that "dollar figures in every card are templated from the live positions
CSV" — true of one multiplicand and false of the product.

THIS PR CHANGES NO YIELD AND NO ARITHMETIC. It records which yield each row used and
whether that yield came from the table or from the silent equity-default fallback,
and it discloses the mechanism. (#210 PR 3 deleted that fallback outright — an
unlisted sleeve now raises — so the tests here that pinned the default state are
INVERTED below rather than deleted, and the raise itself is pinned in
tests/test_yield_raise.py.) Correcting fifteen invented numbers to
fifteen differently-invented ones would not be progress; deriving was measured and
rejected (+0.7% on the KPI, zero verdict flips, and the eleven sleeves carrying most
of the drag are absent from the price cache — plus IAU/IBIT's authored 0.000 encodes
a fact the dividends table cannot express, so deriving would replace two correct
answers with "unknown").

WHY THESE ARE FUNCTION-LEVEL TESTS. Page 14 stops on IS_DEMO, so the default (demo)
suite cannot render it and there is no tests/render/ file for it. The disclosure
therefore lives in src/location_actions.py as a string-returning helper, which is
what makes the prose assertable in any mode. Page-level proof exists too and is NOT
substituted for by these: tests/test_location_actions.py runs the real page through
AppTest with IS_DEMO forced False against the real tracker.db, skipping when
personal-mode inputs are absent — see
test_page14_discloses_the_yield_assumption_and_marks_defaults_live. Both are needed:
these pin the string's content in CI, that one proves it reaches the rendered page.
"""
import pandas as pd
import pytest

from src.household import build_location_register
from src.location_actions import format_assumed_yield, yield_assumption_note
from src.location_config import (
    ACCOUNT_SHELTER_PRIORITY,
    BLEND_SLEEVES,
    SLEEVE_ASSUMED_YIELD,
    SLEEVE_PRIORITY_BY_ACCOUNT_TYPE,
    TAX_PROFILE,
)

# A sleeve name that is in NO config set, deliberately. After #210 PR 2 every
# real sleeve resolves through the table, a look-through or an explicit refusal,
# so the ONLY way to reach the default is a name the config has never seen — which
# is exactly the condition PR 3 turns into a raise. Using a real sleeve here would
# break again the moment it gained an entry.
UNLISTED = "zz_unlisted_test_sleeve"

VALUE = 50_000.0
ORDINARY = TAX_PROFILE["federal_marginal"] + TAX_PROFILE["state_marginal"]


def _fixture():
    """One sleeve IN the yield table, one ABSENT from it.

    Both symbols are table entries now. UNLISTED used to sit here to produce a
    defaulted row — the state #191 disclosed — but #210 PR 3 made an unlisted sleeve
    RAISE, so a fixture containing one cannot build a register at all.

    #210 NOTE: this fixture used multi_asset for the default case, because GAOSX (a
    $10,833 global allocation fund, the largest drag row) took the equity default.
    multi_asset now resolves by LOOK-THROUGH, so using it here would no longer test
    the default at all. The basis mechanics live in tests/test_yield_basis.py; what
    stays here is the DISCLOSURE.
    """
    acct = pd.DataFrame([
        {"pseudonym": "acct_tax", "display_name": "Taxable Acct", "tax_treatment": "taxable"},
    ])
    sec = pd.DataFrame([
        {"ticker": "INTBL", "name": "In Table",  "tax_efficiency": "medium",
         "sleeve_category": "multi_sector_fi"},
        {"ticker": "NOTBL", "name": "Second Entry", "tax_efficiency": "medium",
         "sleeve_category": "core_fi_credit"},
    ])
    pos = pd.DataFrame([
        {"pseudonym": "acct_tax", "symbol": "INTBL", "current_value": VALUE,
         "total_gain_loss": 2000.0, "cost_basis_total": 48000.0},
        {"pseudonym": "acct_tax", "symbol": "NOTBL", "current_value": VALUE,
         "total_gain_loss": 2000.0, "cost_basis_total": 48000.0},
    ])
    return pos, acct, sec


def _register(pos=None, acct=None, sec=None):
    if pos is None:
        pos, acct, sec = _fixture()
    return build_location_register(pos, acct, sec, TAX_PROFILE,
                                  SLEEVE_PRIORITY_BY_ACCOUNT_TYPE, ACCOUNT_SHELTER_PRIORITY)


def _row(reg, symbol):
    match = reg[reg["symbol"] == symbol]
    assert len(match) == 1, f"expected exactly one {symbol} row, got {len(match)}"
    return match.iloc[0]


# ── the register records the yield it applied ────────────────────────────────

def test_register_records_the_yield_it_applied():
    """Right-hand side taken from the config dict directly, never by calling the
    helper under test — otherwise the assertion is the implementation restated."""
    reg = _register()

    assert _row(reg, "INTBL")["assumed_yield"] == pytest.approx(0.040)
    assert _row(reg, "NOTBL")["assumed_yield"] == pytest.approx(0.035)
    # and those literals are the config's, checked separately so a config edit
    # fails here loudly instead of silently agreeing with a stale expectation
    assert SLEEVE_ASSUMED_YIELD["multi_sector_fi"] == pytest.approx(0.040)
    assert SLEEVE_ASSUMED_YIELD["core_fi_credit"] == pytest.approx(0.035)
    # INVERTED (#210). This line used to read
    #     assert "multi_asset" not in SLEEVE_ASSUMED_YIELD
    # written in #191 as a fixture precondition. It became a PIN holding the defect
    # open: multi_asset having no entry was the reason GAOSX took a US-equity yield.
    # Same class as the test_cache.py:79 aliasing pin. The sleeve still has no entry
    # — correctly, because it is a blend — so what must hold is that it does NOT
    # resolve through the default.
    assert "multi_asset" not in SLEEVE_ASSUMED_YIELD
    assert "multi_asset" in BLEND_SLEEVES, (
        "multi_asset must be declared a blend, or it silently takes the equity default"
    )


def test_register_flags_rows_that_fell_back_to_the_default():
    reg = _register()

    # INVERTED (#210 PR 3): NOTBL used to be an unlisted sleeve asserting basis
    # "default". There is no default any more — an unlisted sleeve raises — so both
    # rows resolve through the table and the raise is pinned in test_yield_raise.py.
    # MIGRATED at #289: `table` split four ways. Both fixture sleeves
    # (multi_sector_fi, core_fi_credit) are AUTHORED — a judgement, and the basis now
    # says so instead of naming where the number is stored.
    assert _row(reg, "INTBL")["yield_basis"] == "authored"
    assert _row(reg, "NOTBL")["yield_basis"] == "authored"


def test_yield_is_read_from_the_config_not_a_copy_of_it(monkeypatch):
    """Editing a sleeve's entry must move that row's yield AND its benefit with no
    other change — proving both halves read the live config rather than a snapshot.

    INVERTED (#210 PR 3): this used to flip a row from basis "default" to "table" by
    adding its sleeve to the table. There is no default to flip from, so it now edits
    an existing entry instead. The basis-transition version lives in
    tests/test_yield_basis.py, where an unlisted sleeve raises."""
    import src.location_config as lc

    before = _register()
    assert _row(before, "NOTBL")["yield_basis"] == "authored"
    benefit_before = float(_row(before, "NOTBL")["annual_benefit"])
    original = SLEEVE_ASSUMED_YIELD["core_fi_credit"]

    monkeypatch.setitem(lc.SLEEVE_ASSUMED_YIELD, "core_fi_credit", 0.036)
    after = _register()

    assert _row(after, "NOTBL")["yield_basis"] == "authored"
    assert _row(after, "NOTBL")["assumed_yield"] == pytest.approx(0.036)
    # computed from scratch, not by scaling benefit_before: that value is already
    # rounded to 2dp in the register, so scaling it reintroduces the rounding error
    assert float(_row(after, "NOTBL")["annual_benefit"]) == pytest.approx(
        round(VALUE * 0.036 * ORDINARY, 2))
    assert benefit_before == pytest.approx(round(VALUE * original * ORDINARY, 2))


def test_recording_the_yield_does_not_change_the_benefit():
    """The added columns are provenance, not arithmetic: annual_benefit still equals
    value × yield × rate on an independently-computed right-hand side."""
    reg = _register()
    for sym, y in (("INTBL", 0.040), ("NOTBL", 0.035)):
        r = _row(reg, sym)
        assert float(r["annual_benefit"]) == pytest.approx(round(VALUE * y * ORDINARY, 2))


# ── the per-row marker ───────────────────────────────────────────────────────

def test_format_assumed_yield_marks_anything_that_is_not_a_table_entry():
    """Only the table case is unmarked — the one basis a reader can verify unaided.

    INVERTED (#210 PR 3): "default" was an explicit label here. It is no longer a
    basis the resolver can return, so passing it now exercises the UNKNOWN-basis
    fallback. That the fallback still renders the name visibly is the property worth
    keeping: an unrecognised basis must never render as a bare percentage.
    """
    # Unmarked means DECLARED (proxy or constructed); the undeclared kinds are marked.
    assert format_assumed_yield(0.040, "proxy") == "4.00%"
    assert format_assumed_yield(0.040, "authored") == "4.00% (authored)"
    assert format_assumed_yield(0.0248, "look_through") == "2.48% (look-through)"
    assert "default" in format_assumed_yield(0.018, "default")


def test_format_assumed_yield_marks_a_zero_non_table_basis_too():
    """A 0.00% row is the case most likely to read as 'no assumption was made'."""
    assert format_assumed_yield(0.0, "proxy") == "0.00%"
    assert "unknown_basis" in format_assumed_yield(0.0, "unknown_basis")


# ── the disclosure ───────────────────────────────────────────────────────────

def test_note_names_all_three_multiplicands():
    note = yield_assumption_note(_register())
    low = note.lower()
    assert "value" in low and "yield" in low and "rate" in low
    assert "authored assumption" in low, (
        "the note must say the yield is authored, not merely that it is a yield"
    )
    assert "location_config" in note, "the note must name where the assumption lives"


def test_note_says_the_value_is_a_judgement_not_a_measurement():
    """MIGRATED at #289, and the old phrase became FALSE rather than reworded. This
    required "no declared basis" — true while authored MEANT undeclared.
    SLEEVE_YIELD_AUTHORED makes an authored entry a declared basis that happens not to
    be a measured one, so the note states what KIND of claim it is. The property this
    test protects is unchanged: the reader must be told the number was not measured."""
    note = yield_assumption_note(_register()).lower()
    assert "judgement, not a measurement" in note
    assert "no declared basis** at all" not in note
    # and names at least two of the bases that would disagree, so "undeclared" is
    # concrete rather than a hedge
    assert "trailing" in note and ("sec 30-day" in note or "forward" in note)


def test_note_does_not_claim_every_dollar_comes_from_the_csv():
    """The inverted assertion. The old closing sentence was 'Dollar figures in every
    card are templated from the live positions CSV', which is what made the omission
    a false claim rather than a gap. Any note that reinstates that absolute form is
    the defect again."""
    note = yield_assumption_note(_register()).lower()
    assert "dollar figures in every card are templated" not in note
    assert "every card" not in note


def test_note_counts_its_populations_from_the_register():
    """INVERTED (#210 PR 3). This trio used to pin the DEFAULT count, its scaling, and
    the default rate — the population #191 disclosed. PR 3 deleted the fallback, so
    there is no default row to count. What the note reports now is the split between
    entries WITH a declared benchmark basis and entries authored without one, and the
    counts must still be derived from the frame rather than authored.
    """
    reg = _register()
    table_rows = int(reg["yield_basis"].isin(
        ("proxy", "constructed", "authored", "structural")).sum())
    assert table_rows == 2, "fixture precondition: two table rows"

    note = yield_assumption_note(reg)
    # both populations are named with computed counts, never a literal
    assert f"of {len(reg)} rows" in note
    # Both fixture sleeves are authored, so the proxied clause does not fire; the
    # authored clause is what must carry a computed count here.
    assert f"**{table_rows} of {len(reg)} rows** use an **authored** yield" in note


def test_note_makes_no_claim_about_a_default(monkeypatch):
    """The complement of the above: a clause describing a state that cannot occur is a
    claim the artifact does not support."""
    note = yield_assumption_note(_register()).lower()
    assert "equity default" not in note
    assert "fall back" not in note
    assert "1.8%" not in note


def test_note_does_not_overclaim_when_no_row_uses_the_default(monkeypatch):
    """With every sleeve in the table, the note must not report a fallback share —
    the disclosure has to be true of the book in front of the reader, not a fixed
    paragraph that always warns."""
    import src.location_config as lc
    monkeypatch.setitem(lc.SLEEVE_ASSUMED_YIELD, "core_fi_credit", 0.036)

    reg = _register()
    assert int(reg["yield_basis"].eq("default").sum()) == 0

    note = yield_assumption_note(reg)
    assert "0 of" not in note, "a zero count must not render at all"
    # the mechanism disclosure survives even with no fallback row
    assert "authored assumption" in note.lower()
    assert "judgement, not a measurement" in note.lower()


def test_note_is_empty_safe_on_an_empty_register():
    """An empty register is a legitimate state (nothing mislocated) and must not
    divide by zero building a share."""
    empty = _register(
        pd.DataFrame(columns=["pseudonym", "symbol", "current_value",
                              "total_gain_loss", "cost_basis_total"]),
        pd.DataFrame(columns=["pseudonym", "display_name", "tax_treatment"]),
        pd.DataFrame(columns=["ticker", "name", "tax_efficiency", "sleeve_category"]),
    )
    note = yield_assumption_note(empty)
    assert "authored assumption" in note.lower()
