"""#210 PR 3 — an unlisted sleeve raises, and there is no fallback left anywhere.

SCOPE OF THAT CLAIM. It is true of the RESOLVER, which is what this file tests
directly. It is narrower at the model level: build_location_register selects a
mislocation case first, and a row matching none of A/B/C/D hits `continue`
(src/household.py:837-855) before yield resolution runs. So the raise fires on an
unlisted sleeve ON A MISLOCATED HOLDING, not on any unlisted sleeve in the book —
see the ZZZ row in tests/test_asset_location.py's case-D fixture, which carries a
sleeve in no config set and correctly does not raise.

PR 1 looked blends through their compositions and refused where no basis exists. PR 2
gave the thirteen proxy-backed equity sleeves declared-basis entries, after which zero
rows fell to the default. This removes the fallback itself.

Three bases remain, down from five:

    table          the sleeve has an entry
    look_through   a blend, decomposed through fund_compositions
    not_modelled   the model declines to size it, explicitly

``default`` and ``look_through_partial`` are gone from the code, not merely unused in
the data. A basis that cannot occur is a claim the artifact does not support — and a
look-through that quietly defaults part of its weight is the same defect one level
down, so an unlisted UNDERLYING sleeve raises too.

WHY THE RAISE IS A TRACEBACK AND NOT AN st.error. An unlisted sleeve is a
configuration gap, not a data condition: a new sleeve name reached the location model
before the model was told how to size it. Only a config edit fixes it, so a friendly
in-page message would invite a reader to act on something they cannot act on.

ACCEPTANCE IS PAIRED, deliberately. This PR is inert on the live book — every one of
the 33 sleeve_category values resolves — so "nothing on the page moved" is satisfied
by a PR that did nothing at all. The positive half (the raise fires against a
synthetic sleeve) and the negative half (the live book is untouched) are each
worthless alone.
"""
import pandas as pd
import pytest

from src.household import YIELD_BASES, build_location_register
from src.location_config import (
    ACCOUNT_SHELTER_PRIORITY,
    BLEND_SLEEVES,
    NOT_MODELLED_SLEEVES,
    SLEEVE_ASSUMED_YIELD,
    SLEEVE_PRIORITY_BY_ACCOUNT_TYPE,
    TAX_PROFILE,
)

# A sleeve name in NO config set. After PR 2 every real sleeve resolves, so only a
# name the config has never seen can reach the raise — which is precisely the
# condition this PR exists to catch.
UNLISTED = "zz_unlisted_test_sleeve"
VALUE = 10_000.0


def _fixture(sleeve, symbol="JHEQX", compositions=None):
    """frozen_tod_income's authored symbol and account, so the row is claimed by a
    real group — an invented ticker yields skips that read as passes."""
    acct = pd.DataFrame([
        {"pseudonym": "acct_tod", "display_name": "Individual Taxable (TOD)",
         "tax_treatment": "taxable"},
    ])
    sec = pd.DataFrame([
        {"ticker": symbol, "name": "Fund", "tax_efficiency": "medium",
         "sleeve_category": sleeve},
    ])
    pos = pd.DataFrame([
        {"pseudonym": "acct_tod", "symbol": symbol, "current_value": VALUE,
         "total_gain_loss": 500.0, "cost_basis_total": 9_500.0},
    ])
    return pos, acct, sec, compositions


def _build(sleeve, symbol="JHEQX", compositions=None):
    pos, acct, sec, comps = _fixture(sleeve, symbol, compositions)
    return build_location_register(
        pos, acct, sec, TAX_PROFILE, SLEEVE_PRIORITY_BY_ACCOUNT_TYPE,
        ACCOUNT_SHELTER_PRIORITY, compositions_df=comps)


# ── the positive half: the raise fires ───────────────────────────────────────

def test_unlisted_sleeve_raises():
    with pytest.raises(ValueError, match="no yield basis"):
        _build(UNLISTED)


def test_raise_names_the_sleeve_and_the_symbol():
    """Both, so the offending row is findable. The sleeve says WHAT is unconfigured;
    the symbol says WHERE to look for it in the book."""
    with pytest.raises(ValueError) as exc:
        _build(UNLISTED, symbol="JHEQX")
    msg = str(exc.value)
    assert UNLISTED in msg, "the raise does not name the sleeve"
    assert "JHEQX" in msg, "the raise does not name the symbol"


def test_raise_points_at_all_three_config_sets():
    """A reader needs to know the three places a fix can go, not just that one is
    missing."""
    with pytest.raises(ValueError) as exc:
        _build(UNLISTED)
    msg = str(exc.value)
    for name in ("SLEEVE_ASSUMED_YIELD", "BLEND_SLEEVES", "NOT_MODELLED_SLEEVES"):
        assert name in msg, f"the raise does not mention {name}"
    assert "location_config" in msg


def test_raise_says_it_is_a_configuration_gap_not_a_data_condition():
    """The message must not read as something a page reader can act on — only a config
    edit fixes it."""
    with pytest.raises(ValueError) as exc:
        _build(UNLISTED)
    assert "configuration gap" in str(exc.value).lower()


def test_unlisted_underlying_sleeve_in_a_composition_raises():
    """One level down. A blend whose composition points at an unlisted sleeve used to
    take the default for that slice and report look_through_partial; quietly defaulting
    part of a weight is the same defect, so it raises."""
    comps = pd.DataFrame([
        {"fund_symbol": "GAOSX", "underlying_sleeve": "core_fi_treasury",
         "weight": 0.60, "as_of_date": "2026-08-01", "source": "test"},
        {"fund_symbol": "GAOSX", "underlying_sleeve": UNLISTED,
         "weight": 0.40, "as_of_date": "2026-08-01", "source": "test"},
    ])
    with pytest.raises(ValueError, match="no yield basis"):
        _build("multi_asset", symbol="GAOSX", compositions=comps)


def test_raise_on_an_underlying_sleeve_names_the_fund_and_the_underlying():
    """The diagnosis has to distinguish 'this holding's sleeve is unlisted' from 'this
    holding is a blend and one of its UNDERLYING sleeves is unlisted' — otherwise the
    reader looks up the wrong sleeve."""
    comps = pd.DataFrame([
        {"fund_symbol": "GAOSX", "underlying_sleeve": "core_fi_treasury",
         "weight": 0.60, "as_of_date": "2026-08-01", "source": "test"},
        {"fund_symbol": "GAOSX", "underlying_sleeve": UNLISTED,
         "weight": 0.40, "as_of_date": "2026-08-01", "source": "test"},
    ])
    with pytest.raises(ValueError) as exc:
        _build("multi_asset", symbol="GAOSX", compositions=comps)
    msg = str(exc.value)
    assert UNLISTED in msg
    assert "GAOSX" in msg
    assert "underlying" in msg.lower(), (
        "the message does not say the unlisted sleeve is an UNDERLYING one, so a "
        "reader would look for multi_asset in the table instead"
    )


# ── the fallback is gone from the code, not just from the data ──────────────

def test_basis_set_is_six():
    """THREE until #289 split `table` — which named storage, not provenance — into
    proxy / constructed / authored / structural."""
    assert YIELD_BASES == frozenset({"proxy", "constructed", "authored", "structural",
                                     "look_through", "not_modelled"})


def test_equity_default_yield_no_longer_exists():
    """Deleted, not left at zero: an unreachable constant is a fallback waiting to be
    re-wired."""
    import src.location_config as lc
    assert not hasattr(lc, "EQUITY_DEFAULT_YIELD")


def test_the_dead_second_entry_point_is_gone():
    """_assumed_yield took a sleeve with no symbol and no compositions, so it could
    resolve a yield without ever consulting a composition. It had no callers. A second
    path to the yield that nobody uses is a path that could bypass this raise later."""
    import src.household as h
    assert not hasattr(h, "_assumed_yield"), (
        "_assumed_yield still exists; it is a second entry point to the yield"
    )
    assert hasattr(h, "_assumed_yield_with_source"), "the real resolver must remain"


def test_no_basis_string_survives_for_a_removed_state():
    """format_assumed_yield must not carry a label for a basis that cannot occur."""
    from src.location_actions import _BASIS_SUFFIX
    assert "default" not in _BASIS_SUFFIX
    assert "look_through_partial" not in _BASIS_SUFFIX


def test_the_note_makes_no_default_claim():
    """A disclosure clause describing an impossible state is a claim the artifact does
    not support — the same lesson as PR 2's 'no declared basis' fix, applied the other
    way."""
    from src.location_actions import yield_assumption_note
    reg = _build("multi_sector_fi")
    note = yield_assumption_note(reg).lower()
    assert "equity default" not in note
    assert "fall back" not in note


# ── the config sets still partition the real taxonomy ───────────────────────

def test_the_three_sets_are_disjoint():
    """A sleeve in two sets would resolve by whichever branch is checked first —
    order-dependent, and invisible."""
    table = set(SLEEVE_ASSUMED_YIELD)
    assert not (table & BLEND_SLEEVES), table & BLEND_SLEEVES
    assert not (table & NOT_MODELLED_SLEEVES), table & NOT_MODELLED_SLEEVES
    assert not (BLEND_SLEEVES & NOT_MODELLED_SLEEVES)


# ── the negative half: inert on the real taxonomy, and on the carrier trades ─

def test_every_real_sleeve_still_resolves():
    """The paired half. Worst-case arguments — no symbol, no compositions — which
    forces a blend down the refusal branch rather than a look-through, so a sleeve that
    resolves here resolves everywhere.

    Asserts a non-empty comparison: an empty securities table would otherwise satisfy
    this by having nothing to check.
    """
    import sqlite3
    from pathlib import Path

    from src.household import _assumed_yield_with_source

    db = Path(__file__).resolve().parent.parent / "data" / "tracker.db"
    if not db.exists() or db.stat().st_size == 0:
        pytest.skip("personal cache absent")
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        sleeves = sorted({r[0] for r in conn.execute(
            "SELECT DISTINCT sleeve_category FROM securities "
            "WHERE sleeve_category IS NOT NULL AND sleeve_category != ''")})
    finally:
        conn.close()

    assert len(sleeves) >= 30, (
        f"only {len(sleeves)} sleeve names found; a near-empty taxonomy would pass "
        "this test without exercising anything"
    )
    for sleeve in sleeves:
        _assumed_yield_with_source(sleeve, "", None)   # must not raise


def test_the_intl_carrier_trades_do_not_raise():
    """The 9->12 international restructure: AVIV / IDHQ / AVDV landing in the taxable
    account must not take the page down.

    TWO independent reasons, either sufficient:

    1. All three sleeves gained declared-basis entries in PR 2 (intl_large_value
       0.0448, intl_quality 0.0231, intl_small_value 0.0309), so the yield resolves
       through the table.
    2. The carriers generate NO register row at all, so yield resolution is never
       reached for them. They are tax_efficiency='high' holdings in a taxable account,
       so cases A (low) and B (medium) cannot fire; they are not in a Roth, so no case
       C; and case D needs roth_rank < taxable_rank, but the priority maps give these
       sleeves taxable=1 and roth=None, so the comparison is 1e9 < 1.

    "No register row" is THE MODEL WORKING, not a defect: these are high-efficiency
    assets sitting in the account the priority map ranks first for them, so there is no
    mislocation to report. Whoever finds this test later should not read the empty
    result as a gap.
    """
    carriers = [("AVIV", "intl_large_value"), ("IDHQ", "intl_quality"),
                ("AVDV", "intl_small_value")]
    for _, sleeve in carriers:
        assert sleeve in SLEEVE_ASSUMED_YIELD, f"reason 1 no longer holds for {sleeve}"

    acct = pd.DataFrame([
        {"pseudonym": "acct_01", "display_name": "Individual Taxable (Self-Directed)",
         "tax_treatment": "taxable"},
    ])
    sec = pd.DataFrame([
        {"ticker": t, "name": t, "tax_efficiency": "high", "sleeve_category": s}
        for t, s in carriers
    ])
    pos = pd.DataFrame([
        {"pseudonym": "acct_01", "symbol": t, "current_value": 227.0,
         "total_gain_loss": 5.0, "cost_basis_total": 222.0} for t, _ in carriers
    ])

    reg = build_location_register(
        pos, acct, sec, TAX_PROFILE, SLEEVE_PRIORITY_BY_ACCOUNT_TYPE,
        ACCOUNT_SHELTER_PRIORITY, compositions_df=None)

    assert reg.empty, (
        "the carriers now generate register rows; reason 2 above no longer holds and "
        f"the docstring needs revisiting. rows: {reg[['symbol','case']].to_dict('records')}"
    )
    # reason 2's mechanism, pinned so a priority-map edit surfaces here
    roth = SLEEVE_PRIORITY_BY_ACCOUNT_TYPE.get("roth_ira", {})
    taxable = SLEEVE_PRIORITY_BY_ACCOUNT_TYPE.get("taxable", {})
    for _, sleeve in carriers:
        assert roth.get(sleeve) is None, f"{sleeve} gained a Roth rank; case D may fire"
        assert taxable.get(sleeve) == 1, f"{sleeve}'s taxable rank changed"
