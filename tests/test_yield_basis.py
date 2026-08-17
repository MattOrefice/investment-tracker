"""#210 PR 1 — a blend is looked through, a refusal is a refusal, and no total lies.

Three states replace the old boolean `yield_from_default`:

  table                 the sleeve has an entry
  look_through          the SYMBOL has a fund_compositions row set; the yield is the
                        weight-weighted yield of its underlying sleeves
  look_through_partial  same, but some underlying weight fell to the equity default
                        (self-retiring: PR 2 gives every underlying sleeve an entry)
  not_modelled          the model refuses to size this row's income
  default               unlisted, non-blend (PR 3 turns this into a raise)

WHY LOOK-THROUGH AND NOT A NOT-MODELLED STATE FOR BLENDS. GAOSX, RFUTX and
31564E540 all carry FULL compositions in fund_compositions (7/7/6 sleeves, weights
summing to 1.00), and look_through_position already decomposes them for the Household
View and for the thematic card's denominator. Declaring a blend unmodellable would
invent a limitation, which is a different failure from refusing an invented number.
GAOSX resolves to 2.48% against the 1.80% equity default it used to take.

THE CRUX — pandas reads NaN as zero. Five sites sum annual_benefit
(location_actions.py:760, :1148, :1156; pages/14:147, :157). A not-modelled row
carried as NaN would vanish into a total that renders as complete, via a default
nobody typed — holdings.py:325 in a different column. So the contract is:

  * GROUP totals REFUSE: any group holding a not-modelled row resolves
    {annual_benefit} to None, and render_prose raises rather than render an
    understated figure inside authored prose.
  * The KPI STATES: refusing would take the page down, so it carries a coverage
    record and discloses the excluded rows and their value.

There is one assertion per surface below, not a reading of the diff.

A NOTE ON None VS NaN. A refused row is asserted with pd.isna, not `is None`:
pandas converts None to NaN when it builds a float column, so `is None` would be
testing pandas rather than this design. That conversion is exactly why the basis
column has to exist — NaN alone cannot say WHY a cell is empty, and the five .sum()
sites read the number, not the reason.
"""
import pandas as pd
import pytest

from src.household import YIELD_BASES, build_location_register
from src.location_actions import (
    ACTION_GROUPS,
    drag_coverage,
    format_assumed_yield,
    format_drag_exclusion,
    resolve_placeholders,
)
from src.location_config import (
    ACCOUNT_SHELTER_PRIORITY,
    EQUITY_DEFAULT_YIELD,
    SLEEVE_ASSUMED_YIELD,
    SLEEVE_PRIORITY_BY_ACCOUNT_TYPE,
    TAX_PROFILE,
)

VALUE = 10_000.0
ORDINARY = TAX_PROFILE["federal_marginal"] + TAX_PROFILE["state_marginal"]

# A composition whose underlying sleeves are ALL in the yield table, so the
# look-through is complete and its expected value is computable by hand here.
COMPLETE_MIX = [("core_fi_treasury", 0.50), ("cash", 0.30), ("high_yield_fi", 0.20)]
COMPLETE_YIELD = sum(SLEEVE_ASSUMED_YIELD[s] * w for s, w in COMPLETE_MIX)

# A composition with one underlying sleeve that has NO entry (us_large_core), so the
# look-through is partial and part of the weight resolves through the default.
# A sleeve name that is in NO config set, deliberately. After #210 PR 2 every
# real sleeve resolves through the table, a look-through or an explicit refusal,
# so the ONLY way to reach the default is a name the config has never seen — which
# is exactly the condition PR 3 turns into a raise. Using a real sleeve here would
# break again the moment it gained an entry.
UNLISTED = "zz_unlisted_test_sleeve"

PARTIAL_MIX = [("core_fi_treasury", 0.60), (UNLISTED, 0.40)]
PARTIAL_YIELD = (SLEEVE_ASSUMED_YIELD["core_fi_treasury"] * 0.60
                 + EQUITY_DEFAULT_YIELD * 0.40)


def _compositions(rows):
    """rows: (fund_symbol, [(sleeve, weight), ...])"""
    out = []
    for fund, mix in rows:
        for sleeve, w in mix:
            out.append({"fund_symbol": fund, "underlying_sleeve": sleeve,
                        "weight": w, "as_of_date": "2026-08-01", "source": "test"})
    return pd.DataFrame(out)


def _fixture():
    """One symbol per basis, all inside ONE authored action group.

    The symbols and the account name are the real ``frozen_tod_income`` group's
    (``BILPX/GAOSX/GHYIX/JHEQX/JCPB`` in "Individual Taxable (TOD)", case_filter
    A/B) because filter_register_for_group matches on the group's AUTHORED symbol
    list. An invented ticker is claimed by no group, and the surface tests below
    then skip — which reads as green and proves nothing. That is how the first cut
    of this fixture silently left three of the five surfaces unexercised.

    Sleeves are ASSIGNED here to reach each basis, not to describe the real book:

    JHEQX  hedged_equity   — a table entry
    GAOSX  multi_asset     — blend WITH a complete composition   (real, as it happens)
    JCPB   multi_asset     — blend WITH a partial composition
    BILPX  thematic        — the confirmed refusal set
    GHYIX  us_mid_cap      — the confirmed refusal set
    """
    acct = pd.DataFrame([
        {"pseudonym": "acct_tod", "display_name": "Individual Taxable (TOD)",
         "tax_treatment": "taxable"},
    ])
    specs = [("JHEQX", "hedged_equity"), ("GAOSX", "multi_asset"),
             ("JCPB", "multi_asset"), ("BILPX", "thematic"), ("GHYIX", "us_mid_cap")]
    sec = pd.DataFrame([
        {"ticker": t, "name": f"{t} fund", "tax_efficiency": "medium", "sleeve_category": s}
        for t, s in specs
    ])
    pos = pd.DataFrame([
        {"pseudonym": "acct_tod", "symbol": t, "current_value": VALUE,
         "total_gain_loss": 500.0, "cost_basis_total": 9_500.0}
        for t, _ in specs
    ])
    comps = _compositions([("GAOSX", COMPLETE_MIX), ("JCPB", PARTIAL_MIX)])
    return pos, acct, sec, comps


def _register(compositions="default"):
    pos, acct, sec, comps = _fixture()
    if compositions == "default":
        compositions = comps
    return build_location_register(
        pos, acct, sec, TAX_PROFILE, SLEEVE_PRIORITY_BY_ACCOUNT_TYPE,
        ACCOUNT_SHELTER_PRIORITY, compositions_df=compositions)


def _row(reg, symbol):
    m = reg[reg["symbol"] == symbol]
    assert len(m) == 1, f"expected one {symbol} row, got {len(m)}"
    return m.iloc[0]


# ── basis resolution ─────────────────────────────────────────────────────────

def test_basis_set_is_closed():
    assert YIELD_BASES == frozenset({
        "table", "look_through", "look_through_partial", "default", "not_modelled"})


def test_table_sleeve_resolves_from_the_table():
    r = _row(_register(), "JHEQX")
    assert r["yield_basis"] == "table"
    assert r["assumed_yield"] == pytest.approx(SLEEVE_ASSUMED_YIELD["hedged_equity"])


def test_blend_with_a_complete_composition_is_looked_through():
    """Right-hand side computed from the composition by hand, never by calling the
    resolver — otherwise the assertion is the implementation restated."""
    r = _row(_register(), "GAOSX")
    assert r["yield_basis"] == "look_through"
    assert r["assumed_yield"] == pytest.approx(COMPLETE_YIELD)
    # and it is NOT the equity default it used to take
    assert r["assumed_yield"] != pytest.approx(EQUITY_DEFAULT_YIELD)


def test_blend_with_a_partial_composition_says_partial():
    """Some underlying weight has no entry, so part of the yield came from the
    default. The marker exists so PR 2 can retire it by giving every underlying
    sleeve an entry — a self-retiring disclosure, not a permanent caveat."""
    r = _row(_register(), "JCPB")
    assert r["yield_basis"] == "look_through_partial"
    assert r["assumed_yield"] == pytest.approx(PARTIAL_YIELD)


def test_blend_with_no_composition_is_not_modelled():
    """The fail-loud state, correctly triggered: a blend the model has no
    decomposition for. Currently unreachable on the real book (all three blend funds
    carry compositions), which is what a guard should look like."""
    pos, acct, sec, comps = _fixture()
    only_jcpb = comps[comps["fund_symbol"] == "JCPB"]      # GAOSX loses its composition
    reg = build_location_register(
        pos, acct, sec, TAX_PROFILE, SLEEVE_PRIORITY_BY_ACCOUNT_TYPE,
        ACCOUNT_SHELTER_PRIORITY, compositions_df=only_jcpb)
    r = _row(reg, "GAOSX")
    assert r["yield_basis"] == "not_modelled"
    assert pd.isna(r["assumed_yield"])


def test_blend_whose_composition_does_not_sum_to_one_is_not_modelled():
    """A composition accounting for 70% of the fund cannot be normalised into a yield
    nobody can check — it refuses instead."""
    pos, acct, sec, _ = _fixture()
    short = _compositions([("GAOSX", [("core_fi_treasury", 0.40), ("cash", 0.30)])])
    reg = build_location_register(
        pos, acct, sec, TAX_PROFILE, SLEEVE_PRIORITY_BY_ACCOUNT_TYPE,
        ACCOUNT_SHELTER_PRIORITY, compositions_df=short)
    assert _row(reg, "GAOSX")["yield_basis"] == "not_modelled"


def test_omitting_compositions_makes_every_blend_not_modelled():
    """The absent-argument direction, stated as a contract rather than left to
    discovery: a caller that did not supply compositions cannot look anything
    through, and the honest result is a visible refusal on every blend — never a
    silent fallback to an equity yield for a bond-carrying fund."""
    reg = _register(compositions=None)
    for sym in ("GAOSX", "JCPB"):
        r = _row(reg, sym)
        assert r["yield_basis"] == "not_modelled", f"{sym} should refuse without compositions"
        assert pd.isna(r["assumed_yield"])


@pytest.mark.parametrize("symbol,sleeve", [("BILPX", "thematic"), ("GHYIX", "us_mid_cap")])
def test_refusal_set_is_not_modelled(symbol, sleeve):
    """The two sleeves with no cache proxy and no composition — the only places the
    refusal is real. thematic's twelve symbols are homogeneous in kind (all equity,
    all growth-tilted) so the gap is data, not concept; but with no proxy there is
    nothing to declare a basis against, so the model says so instead of guessing."""
    r = _row(_register(), symbol)
    assert r["sleeve"] == sleeve
    assert r["yield_basis"] == "not_modelled"
    assert pd.isna(r["assumed_yield"])


def test_unlisted_non_blend_sleeve_still_takes_the_default_in_pr1():
    """PR 3 turns this into a raise. Pinned now so that change is visible as a
    behaviour change rather than arriving inside an unrelated diff."""
    pos, acct, sec, comps = _fixture()
    sec = sec.copy()
    sec.loc[sec["ticker"] == "JHEQX", "sleeve_category"] = UNLISTED
    reg = build_location_register(
        pos, acct, sec, TAX_PROFILE, SLEEVE_PRIORITY_BY_ACCOUNT_TYPE,
        ACCOUNT_SHELTER_PRIORITY, compositions_df=comps)
    r = _row(reg, "JHEQX")
    assert r["yield_basis"] == "default"
    assert r["assumed_yield"] == pytest.approx(EQUITY_DEFAULT_YIELD)


# ── annual_benefit and payback on a refused row ──────────────────────────────

def test_not_modelled_benefit_is_none_not_zero():
    """None, not 0.0 — a zero is a claim that there is no drag, which is the
    fabrication this whole workstream removes."""
    r = _row(_register(), "BILPX")
    assert pd.isna(r["annual_benefit"]), (
        "a refused row must not carry a number; NaN is the in-frame representation and "
        "yield_basis is what makes it unambiguous"
    )
    assert r["yield_basis"] == "not_modelled"


def test_not_modelled_payback_is_none_and_distinguishable_from_free():
    """payback_months is already None for a FREE move. A refused row must not be
    readable as 'costs nothing' — the basis column is what separates them."""
    reg = _register()
    refused = _row(reg, "BILPX")
    assert pd.isna(refused["payback_months"])
    assert refused["yield_basis"] == "not_modelled"
    modelled = _row(reg, "JHEQX")
    assert modelled["yield_basis"] == "table"


def test_look_through_benefit_uses_the_looked_through_yield():
    r = _row(_register(), "GAOSX")
    assert float(r["annual_benefit"]) == pytest.approx(
        round(VALUE * COMPLETE_YIELD * ORDINARY, 2))


# ── surface 1: _group_annual_benefit (location_actions.py:760) ───────────────

def _group_holding(reg, symbol):
    """The first ACTION_GROUP whose filter claims this symbol's row."""
    from src.location_actions import filter_register_for_group
    for g in ACTION_GROUPS:
        rr = filter_register_for_group(reg, g)
        if symbol in set(rr["symbol"]):
            return g
    return None


def test_surface_group_total_refuses_when_a_row_is_not_modelled():
    """SURFACE 1/5. A group total that omits a refused row must resolve to None so
    render_prose raises, never to a smaller number rendered as complete."""
    from src.location_actions import _group_annual_benefit
    reg = _register()
    g = _group_holding(reg, "BILPX")
    assert g is not None, (
        "the fixture uses frozen_tod_income's authored symbols precisely so a group "
        "claims this row; a skip here would leave the surface unexercised"
    )
    assert _group_annual_benefit(reg, g["key"]) is None


def test_surface_group_total_is_a_figure_when_nothing_is_refused():
    """The complement — refusing must not become the answer for every group. Built
    by DROPPING the refused holdings rather than by searching for a clean group, so
    this can never degrade into a skip that reads as a pass."""
    from src.location_actions import _group_annual_benefit, filter_register_for_group
    pos, acct, sec, comps = _fixture()
    keep = pos[~pos["symbol"].isin(["BILPX", "GHYIX"])]
    reg = build_location_register(
        keep, acct, sec, TAX_PROFILE, SLEEVE_PRIORITY_BY_ACCOUNT_TYPE,
        ACCOUNT_SHELTER_PRIORITY, compositions_df=comps)
    assert not reg["yield_basis"].eq("not_modelled").any(), "precondition: nothing refused"
    g = _group_holding(reg, "GAOSX")
    assert g is not None, "precondition: a group claims the look-through row"
    assert not filter_register_for_group(reg, g).empty
    assert _group_annual_benefit(reg, g["key"]) is not None


# ── surface 2 & 3: resolve_placeholders (location_actions.py:1148, :1156) ────

def test_surface_placeholder_annual_benefit_refuses_when_not_modelled():
    """SURFACE 2/5."""
    pos, acct, sec, comps = _fixture()
    reg = _register()
    g = _group_holding(reg, "BILPX")
    assert g is not None, (
        "the fixture uses frozen_tod_income's authored symbols precisely so a group "
        "claims this row; a skip here would leave the surface unexercised"
    )
    resolved = resolve_placeholders(g, pos, acct, sec, reg, roth_idle_cash=1000.0,
                                   compositions_df=comps)
    assert resolved["annual_benefit"] is None


def test_surface_placeholder_payback_refuses_when_not_modelled():
    """SURFACE 3/5 — :1156's _ab feeds a rendered '<n>-year payback' string, so a
    silently-reduced denominator would render a payback that is too short."""
    pos, acct, sec, comps = _fixture()
    reg = _register()
    g = _group_holding(reg, "BILPX")
    assert g is not None, (
        "the fixture uses frozen_tod_income's authored symbols precisely so a group "
        "claims this row; a skip here would leave the surface unexercised"
    )
    resolved = resolve_placeholders(g, pos, acct, sec, reg, roth_idle_cash=1000.0,
                                   compositions_df=comps)
    assert resolved["payback"] is None


# ── surface 4 & 5: the KPI and the deferred split (pages/14:147, :157) ───────

def test_surface_kpi_states_the_exclusion_rather_than_refusing():
    """SURFACES 4+5. Refusing here would take the page down, so the KPI carries a
    coverage record: the total covers the modelled rows and names what it left out."""
    reg = _register()
    cov = drag_coverage(reg)

    abd = reg[reg["case"].isin(["A", "B", "D"])]
    modelled = abd[abd["yield_basis"] != "not_modelled"]
    refused = abd[abd["yield_basis"] == "not_modelled"]

    assert cov.total == pytest.approx(float(modelled["annual_benefit"].sum()))
    assert cov.n_not_modelled == len(refused)
    assert cov.not_modelled_value == pytest.approx(float(refused["current_value"].sum()))
    assert set(cov.symbols) == set(refused["symbol"])


def test_kpi_coverage_carries_what_a_bare_sum_would_lose():
    """The crux, asserted directly. pandas skips NaN, so a bare .sum() over a frame
    containing refused rows returns the modelled total with NOTHING marking the
    omission — numerically identical to the honest total, and that is the danger.
    The coverage record is what makes the two distinguishable."""
    reg = _register()
    abd = reg[reg["case"].isin(["A", "B", "D"])]
    bare = float(pd.to_numeric(abd["annual_benefit"], errors="coerce").sum())
    cov = drag_coverage(reg)

    assert cov.total == pytest.approx(bare), (
        "the total itself is right; the bare sum's defect is silence, not arithmetic"
    )
    assert cov.n_not_modelled > 0, "fixture precondition: something is refused"
    assert format_drag_exclusion(cov) is not None, (
        "a total omitting refused rows rendered with no exclusion notice"
    )


def test_drag_exclusion_notice_names_the_rows_and_their_value():
    cov = drag_coverage(_register())
    note = format_drag_exclusion(cov)
    for sym in cov.symbols:
        assert sym in note
    assert f"{cov.n_not_modelled}" in note
    assert "not modelled" in note.lower() or "cannot" in note.lower()


def test_drag_exclusion_notice_is_none_when_nothing_is_excluded():
    """Silent when there is nothing to say — a notice that always renders trains the
    reader to skip it."""
    pos, acct, sec, comps = _fixture()
    keep = pos[~pos["symbol"].isin(["BILPX", "GHYIX"])]
    reg = build_location_register(
        keep, acct, sec, TAX_PROFILE, SLEEVE_PRIORITY_BY_ACCOUNT_TYPE,
        ACCOUNT_SHELTER_PRIORITY, compositions_df=comps)
    cov = drag_coverage(reg)
    assert cov.n_not_modelled == 0
    assert format_drag_exclusion(cov) is None


def test_drag_coverage_on_an_empty_register():
    reg = build_location_register(
        pd.DataFrame(columns=["pseudonym", "symbol", "current_value",
                              "total_gain_loss", "cost_basis_total"]),
        pd.DataFrame(columns=["pseudonym", "display_name", "tax_treatment"]),
        pd.DataFrame(columns=["ticker", "name", "tax_efficiency", "sleeve_category"]),
        TAX_PROFILE, SLEEVE_PRIORITY_BY_ACCOUNT_TYPE, ACCOUNT_SHELTER_PRIORITY,
        compositions_df=None)
    cov = drag_coverage(reg)
    assert cov.total == 0.0 and cov.n_not_modelled == 0
    assert format_drag_exclusion(cov) is None


# ── the per-row marker ───────────────────────────────────────────────────────

def test_format_assumed_yield_per_basis():
    assert format_assumed_yield(0.040, "table") == "4.00%"
    assert format_assumed_yield(0.0248, "look_through") == "2.48% (look-through)"
    assert format_assumed_yield(0.0266, "look_through_partial") == \
        "2.66% (look-through, partial)"
    assert format_assumed_yield(0.018, "default") == "1.80% (default)"
    assert format_assumed_yield(None, "not_modelled") == "not modelled"


def test_format_assumed_yield_marks_a_zero_default():
    """A 0.00% row is the one most likely to read as 'no assumption was made'."""
    assert format_assumed_yield(0.0, "table") == "0.00%"
    assert format_assumed_yield(0.0, "default") == "0.00% (default)"
