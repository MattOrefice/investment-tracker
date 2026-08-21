"""A commodity sleeve has no steady-state yield, so the model declines to size it.

#210's remainder. Three pieces, and they are three because the config change alone
takes page 14 down:

  1. CONFIG      real_assets_commodities moves out of SLEEVE_ASSUMED_YIELD and into
                 NOT_MODELLED_SLEEVES. PDBC distributes REALIZED GAINS, not income,
                 so a trailing-twelve-month yield measures what it happened to sell
                 that year: 0.97 / 1.70 / 0.00 / 40.38 / 13.15 / 4.18 / 4.41 / 2.84 %
                 at successive year-ends, measured from the committed cache. The
                 shipped 1.50% and the 2.84% #210 proposed are both single-year
                 snapshots of that range.

  2. RESOLVER    a blend whose look-through hits a NOT-MODELLED component refuses
                 WHOLE rather than normalising over the remaining weight. Same rule
                 as the partial-composition check beside it, and deliberately with no
                 small-component exception — 5% is exactly where normalising feels
                 harmless. Distinct from an UNLISTED component, which stays a raise:
                 that is a config gap and must be loud.

  3. CARDS       two authored cards cited {annual_benefit}, and a refusing group
                 resolves it to None so render_prose raises. Their sentences now say
                 why the figure is withheld.

THE REFUSAL IS NARROW, which is what the card prose has to convey: each group holds
four rows and ONE refuses, so the total is withheld because one holding cannot be
sized — not because none can. Every other figure on both cards stays live.
"""
import re

import pandas as pd
import pytest

import src.household as hh
import src.location_config as lc

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

SLEEVE = "real_assets_commodities"


# ── 1. config ─────────────────────────────────────────────────────────────────

def test_the_commodity_sleeve_is_declined_not_valued():
    assert SLEEVE not in lc.SLEEVE_ASSUMED_YIELD, (
        "a commodity sleeve carries a yield again — a TTM on realized gains has no "
        "steady-state value")
    assert SLEEVE in lc.NOT_MODELLED_SLEEVES


def test_the_refusal_records_why_not_merely_that():
    """NOT_MODELLED_SLEEVES documents each member's reason; a bare set entry would
    read as an oversight to the next person who tries to fill it in."""
    src = (lc.__file__ and open(lc.__file__, encoding="utf-8").read()) or ""
    block = src[src.index("# Sleeves the model DECLINES to size"):
                src.index("NOT_MODELLED_SLEEVES:")]
    # Comment prefixes stripped and whitespace collapsed before matching: the phrase
    # wraps across lines as "REALIZED\n#   GAINS", so a contiguous substring search
    # finds nothing and the assertion passes or fails on line-wrapping rather than on
    # content. Same trap as the #250 mutation harness.
    flat = re.sub(r"\s+", " ", re.sub(r"^\s*#\s?", "", block, flags=re.M)).lower()
    assert "realized gains" in flat
    assert "40.38" in flat, "the evidence for the claim is not recorded beside it"


# ── 2. resolver ───────────────────────────────────────────────────────────────

def _mix(*pairs):
    return pd.DataFrame(
        [{"fund_symbol": "TESTFUND", "underlying_sleeve": s, "weight": w}
         for s, w in pairs])


def test_a_blend_with_a_not_modelled_component_refuses_whole():
    """THE RULE. Not 95% of an answer — no answer, and a basis that says so."""
    val, basis = hh._assumed_yield_with_source(
        "multi_asset", "TESTFUND",
        _mix(("us_large_core", 0.95), (SLEEVE, 0.05)))
    assert basis == "not_modelled"
    assert val is None, "a normalised partial yield was returned"


def test_the_rule_has_no_small_component_exception():
    """A 1% component refuses exactly as a 50% one does. 5% is precisely where
    normalising feels harmless, which is why the size does not enter into it."""
    for w in (0.01, 0.05, 0.20, 0.50):
        val, basis = hh._assumed_yield_with_source(
            "multi_asset", "TESTFUND",
            _mix(("us_large_core", 1.0 - w), (SLEEVE, w)))
        assert (val, basis) == (None, "not_modelled"), f"weight {w} did not refuse"


def test_an_unlisted_component_still_raises():
    """The distinction the rule turns on: a NOT-MODELLED component is a decision
    already recorded and refuses quietly; an UNLISTED one is a config gap and must
    be loud. Collapsing the two would silence real gaps."""
    with pytest.raises(Exception) as exc:
        hh._assumed_yield_with_source(
            "multi_asset", "TESTFUND",
            _mix(("us_large_core", 0.95), ("no_such_sleeve_xyz", 0.05)))
    assert "no_such_sleeve_xyz" in str(exc.value)


def test_a_fully_modelled_blend_still_looks_through():
    """Non-vacuity: a resolver that refused every blend would satisfy the two tests
    above."""
    val, basis = hh._assumed_yield_with_source(
        "multi_asset", "TESTFUND",
        _mix(("us_large_core", 0.60), ("core_fi_treasury", 0.40)))
    assert basis == "look_through"
    assert val is not None and val > 0


def test_the_direct_sleeve_refuses_too():
    val, basis = hh._assumed_yield_with_source(SLEEVE, "PDBC", None)
    assert (val, basis) == (None, "not_modelled")


# ── 3. the rendered cards ─────────────────────────────────────────────────────

def _skip_without_personal_inputs():
    from pathlib import Path
    from src.household_data import find_latest_positions_csv
    root = Path(hh.__file__).resolve().parent.parent
    if (find_latest_positions_csv() is None
            or not (root / "data" / "tracker.db").exists()
            or not (root / "private" / "account_map.json").exists()):
        pytest.skip("personal-mode inputs absent")


@pytest.fixture(scope="module")
def page_text():
    """One render of page 14, shared — it is the expensive part of this file."""
    _skip_without_personal_inputs()
    from pathlib import Path

    from streamlit.testing.v1 import AppTest
    root = Path(hh.__file__).resolve().parent.parent
    at = AppTest.from_file(str(root / "pages" / "14_Asset_Location.py"),
                           default_timeout=600).run()
    assert not at.exception, f"page 14 raised: {at.exception}"
    out = []
    for kind in ("markdown", "caption", "info", "warning", "error",
                 "subheader", "header"):
        for el in getattr(at, kind, []):
            v = getattr(el, "value", None)
            if isinstance(v, str):
                out.append(re.sub(r"\s+", " ", v))
    return out


def _joined(texts):
    return " ".join(texts)


def test_the_saa_card_states_the_drag_is_real_and_unsized(page_text):
    """THE SENTENCE AN ACCEPTED POSITION NEEDS: it separates the fact (there is drag)
    from the measurement (its size is unknown). The decision stands; only the figure
    is withheld."""
    body = _joined(page_text)
    assert "The drag is real and deliberately unsized" in body
    assert "realized gains rather than income" in body


def test_the_saa_card_carries_the_evidence_inline(page_text):
    """0.0%-40.4% lets a reader check the claim rather than take it. Without the
    range, 'no steady-state yield' is an assertion."""
    assert "0.0% to 40.4%" in _joined(page_text)


def test_the_saa_card_scopes_the_refusal_to_the_drag(page_text):
    """#249's tier distinction applied to prose: the card's other figures remain
    valid, so the sentence must not read as though the whole card is unavailable."""
    body = _joined(page_text)
    assert "The other three holdings are sized normally" in body
    assert "Investable pre-tax capacity is" in body, "a surviving figure vanished"
    assert "the honest price of running a coherent" in body, "the conclusion vanished"


def test_the_tod_card_explains_the_blend_refusal(page_text):
    body = _joined(page_text)
    assert "holds 5% commodities" in body
    assert "would understate the other three" in body


def test_no_card_still_asserts_a_drag_figure_it_cannot_size(page_text):
    """The retired sentences. Their survival is the defect."""
    body = _joined(page_text)
    assert "at a cost of $" not in body
    assert not re.search(r"The drag is \$[\d,]+ a year", body)


def test_the_kpi_exclusion_note_names_the_newly_unsized_holdings(page_text):
    """The total must say what it is blind to — and now names PDBC and GAOSX, where
    before it named only IDGT."""
    note = [t for t in page_text if t.startswith("Excludes")]
    assert note, "the drag KPI renders no exclusion note"
    assert "PDBC" in note[0] and "GAOSX" in note[0]
    assert "not modelled, not zero" in note[0]
