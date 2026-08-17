"""#191 — the assumed yield must be visible where it is applied and disclosed.

`annual_benefit = position value × assumed sleeve yield × tax rate`. The value is
live from the positions CSV and the rate is tax policy, but the yield is an authored
constant in src/location_config.py with no declared basis. Page 14's Assumptions
expander enumerated every tax rate, said nothing about a yield, and closed by
asserting that "dollar figures in every card are templated from the live positions
CSV" — true of one multiplicand and false of the product.

THIS PR CHANGES NO YIELD AND NO ARITHMETIC. It records which yield each row used and
whether that yield came from the table or from the silent EQUITY_DEFAULT_YIELD
fallback, and it discloses the mechanism. Correcting fifteen invented numbers to
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
    EQUITY_DEFAULT_YIELD,
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

    multi_sector_fi is a table entry (0.040). UNLISTED is in no config set at all, so
    it takes the equity default — the state this PR discloses.

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
        {"ticker": "NOTBL", "name": "Not In Table", "tax_efficiency": "medium",
         "sleeve_category": UNLISTED},
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
    assert _row(reg, "NOTBL")["assumed_yield"] == pytest.approx(0.018)
    # and those literals are the config's, checked separately so a config edit
    # fails here loudly instead of silently agreeing with a stale expectation
    assert SLEEVE_ASSUMED_YIELD["multi_sector_fi"] == pytest.approx(0.040)
    assert EQUITY_DEFAULT_YIELD == pytest.approx(0.018)
    # INVERTED (#210). This line used to read
    #     assert "multi_asset" not in SLEEVE_ASSUMED_YIELD
    # written in #191 as a fixture precondition. It became a PIN holding the defect
    # open: multi_asset having no entry was the reason GAOSX took a US-equity yield.
    # Same class as the test_cache.py:79 aliasing pin. The sleeve still has no entry
    # — correctly, because it is a blend — so what must hold is that it does NOT
    # resolve through the default.
    assert "multi_asset" not in SLEEVE_ASSUMED_YIELD
    assert UNLISTED not in SLEEVE_ASSUMED_YIELD, "the fixture's unlisted name must stay unlisted"
    assert "multi_asset" in BLEND_SLEEVES, (
        "multi_asset must be declared a blend, or it silently takes the equity default"
    )


def test_register_flags_rows_that_fell_back_to_the_default():
    reg = _register()

    assert _row(reg, "INTBL")["yield_basis"] == "table"
    assert _row(reg, "NOTBL")["yield_basis"] == "default", (
        "a sleeve absent from SLEEVE_ASSUMED_YIELD took the default silently and "
        "the row does not say so"
    )


def test_flag_consults_the_table_not_a_hardcoded_list(monkeypatch):
    """Adding multi_asset to the table must flip the flag AND move the benefit, with
    no other edit — proving both halves read the config rather than a copy of it."""
    import src.location_config as lc

    before = _register()
    assert _row(before, "NOTBL")["yield_basis"] == "default"
    benefit_before = float(_row(before, "NOTBL")["annual_benefit"])

    monkeypatch.setitem(lc.SLEEVE_ASSUMED_YIELD, UNLISTED, 0.036)
    after = _register()

    assert _row(after, "NOTBL")["yield_basis"] == "table"
    assert _row(after, "NOTBL")["assumed_yield"] == pytest.approx(0.036)
    assert float(_row(after, "NOTBL")["annual_benefit"]) == pytest.approx(
        benefit_before * 0.036 / EQUITY_DEFAULT_YIELD
    )


def test_recording_the_yield_does_not_change_the_benefit():
    """The added columns are provenance, not arithmetic: annual_benefit still equals
    value × yield × rate on an independently-computed right-hand side."""
    reg = _register()
    for sym, y in (("INTBL", 0.040), ("NOTBL", 0.018)):
        r = _row(reg, sym)
        assert float(r["annual_benefit"]) == pytest.approx(round(VALUE * y * ORDINARY, 2))


# ── the per-row marker ───────────────────────────────────────────────────────

def test_format_assumed_yield_marks_the_default():
    assert format_assumed_yield(0.040, "table") == "4.00%"
    assert format_assumed_yield(0.018, "default") == "1.80% (default)"


def test_format_assumed_yield_marks_a_zero_default_too():
    """A 0.00% row is the case most likely to read as 'no assumption was made'."""
    assert format_assumed_yield(0.0, "table") == "0.00%"
    assert format_assumed_yield(0.0, "default") == "0.00% (default)"


# ── the disclosure ───────────────────────────────────────────────────────────

def test_note_names_all_three_multiplicands():
    note = yield_assumption_note(_register())
    low = note.lower()
    assert "value" in low and "yield" in low and "rate" in low
    assert "authored assumption" in low, (
        "the note must say the yield is authored, not merely that it is a yield"
    )
    assert "location_config" in note, "the note must name where the assumption lives"


def test_note_says_the_basis_is_undeclared():
    note = yield_assumption_note(_register()).lower()
    assert "no declared basis" in note
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


def test_note_counts_default_rows_from_the_register():
    """Derived from the frame, never a literal — a hardcoded count passes alone and
    fails as soon as the book changes."""
    reg = _register()
    n_default = int(reg["yield_basis"].eq("default").sum())
    assert n_default == 1, "fixture precondition: exactly one row on the default"

    note = yield_assumption_note(reg)
    assert f"{n_default} of {len(reg)} rows" in note


def test_note_scales_with_more_default_rows():
    """Two rows on the default must be reported as two — pins that the count is
    computed, which a single-row fixture cannot distinguish from a literal '1'."""
    pos, acct, sec = _fixture()
    sec = pd.concat([sec, pd.DataFrame([
        # A SECOND unlisted name, not a real sleeve: every real one now resolves.
        {"ticker": "NOTB2", "name": "Also Absent", "tax_efficiency": "medium",
         "sleeve_category": UNLISTED + "_2"},
    ])], ignore_index=True)
    pos = pd.concat([pos, pd.DataFrame([
        {"pseudonym": "acct_tax", "symbol": "NOTB2", "current_value": VALUE,
         "total_gain_loss": 2000.0, "cost_basis_total": 48000.0},
    ])], ignore_index=True)

    reg = _register(pos, acct, sec)
    assert int(reg["yield_basis"].eq("default").sum()) == 2
    assert f"2 of {len(reg)} rows" in yield_assumption_note(reg)


def test_note_states_the_default_rate():
    note = yield_assumption_note(_register())
    assert f"{EQUITY_DEFAULT_YIELD:.1%}" in note or "1.8%" in note


def test_note_does_not_overclaim_when_no_row_uses_the_default(monkeypatch):
    """With every sleeve in the table, the note must not report a fallback share —
    the disclosure has to be true of the book in front of the reader, not a fixed
    paragraph that always warns."""
    import src.location_config as lc
    monkeypatch.setitem(lc.SLEEVE_ASSUMED_YIELD, UNLISTED, 0.036)

    reg = _register()
    assert int(reg["yield_basis"].eq("default").sum()) == 0

    note = yield_assumption_note(reg)
    assert "0 of" not in note, "a zero count must not render at all"
    # the mechanism disclosure survives even with no fallback row
    assert "authored assumption" in note.lower()
    assert "no declared basis" in note.lower()


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
