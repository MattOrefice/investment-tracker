"""Asset Location prices state tax with the tax residence, and its TOD group follows the
book (#706).

  * TAX_PROFILE holds the federal rates only. State and local rates come from
    personal_profile.load_tax_residence: the private `tax_residence` in personal mode,
    DEMO_TAX_RESIDENCE in demo. The page builds its profile the way the Liquidity page
    does (liquidity.residence_tax_profile).
  * Every rate a decision text states is read from the profile its figures use.
  * A recorded decision keeps its decision and its date; its figures and rates are
    today's, and decision_review flags one today's rates would reverse, changing none.
  * The frozen TOD group types no tickers: bind_groups_to_book reads them from the
    book, so a manager's swap reaches the card with no edit.

Every residence here is synthetic (tests/tax_profiles.py and the ones below).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

import src.location_actions as la
import src.location_config as lc
from src.household import build_location_register
from src.liquidity import residence_tax_profile
from src.personal_profile import DEMO_TAX_RESIDENCE
from tests.tax_profiles import SYNTHETIC_TAX_PROFILE

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "pages" / "14_Asset_Location.py"

# A second synthetic residence at a different rate, so a text that typed a rate would
# be caught reading the wrong one.
OTHER_RESIDENCE = {"jurisdictions": [
    {"name": "Other Example (synthetic)", "ordinary_rate": 0.09, "gains_rate": 0.08,
     "source": "synthetic test value", "source_date": "synthetic"}]}
NO_RESIDENCE = {"jurisdictions": [], "source": "absent", "problem": "no tax_residence"}


# ── TAX_PROFILE is federal ────────────────────────────────────────────────────

def test_tax_profile_holds_the_federal_keys_only():
    assert set(lc.TAX_PROFILE) == {"federal_marginal", "federal_ltcg"}
    assert not [k for k in lc.TAX_PROFILE if "state" in k or "local" in k]


def test_a_profile_without_state_keys_raises_rather_than_pricing_at_zero():
    """TAX_PROFILE alone is not a profile anything prices with: _require_rate raises."""
    with pytest.raises(Exception, match="state_ltcg"):
        lc.ltcg_rate(lc.TAX_PROFILE)


def test_page_14_reads_the_residence_and_never_tax_profile():
    """The page's profile is the residence's; the name TAX_PROFILE does not appear in
    the page's code at all."""
    tree = ast.parse(PAGE.read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                for a in n.names}
    assert "TAX_PROFILE" not in names | imported
    calls = {ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert "load_tax_residence(IS_DEMO)" in calls
    assert "residence_tax_profile(_residence)" in calls


def test_demo_takes_the_synthetic_residence():
    from src.personal_profile import load_tax_residence
    r = load_tax_residence(True)
    assert r["jurisdictions"] == DEMO_TAX_RESIDENCE["jurisdictions"]
    assert residence_tax_profile(r) == SYNTHETIC_TAX_PROFILE


def test_in_demo_mode_the_page_renders_its_gate_and_reads_no_residence(monkeypatch):
    """Page 14 stops at its demo gate before any figure is built, so in demo it reads
    no residence at all: not the synthetic one, and never the private one."""
    import src.config
    import src.personal_profile as pp
    from streamlit.testing.v1 import AppTest
    from tests.conftest import _pin_mode

    def _refuse(is_demo):
        raise AssertionError("page 14 read a tax residence in demo mode")

    _pin_mode(monkeypatch, "demo")
    monkeypatch.setattr(src.config, "IS_DEMO", True)
    monkeypatch.setattr(pp, "load_tax_residence", _refuse)
    at = AppTest.from_file(str(PAGE), default_timeout=60).run()
    assert not at.exception, at.exception
    assert [i.value for i in at.info] == [
        "Asset location analysis is available in personal mode only."]


# ── the page on the personal book, with each residence ────────────────────────
# Personal-mode inputs only (tracker.db, a positions export, the account map), so these
# skip in CI. The private residence's names and rates are read at run time from the
# private profile and never written here.

def _skip_without_personal_inputs():
    from src.household_data import find_latest_positions_csv
    if (find_latest_positions_csv() is None
            or not (ROOT / "data" / "tracker.db").exists()
            or not (ROOT / "private" / "account_map.json").exists()):
        pytest.skip("personal-mode inputs absent")


def _render_page_14(monkeypatch, residence=None) -> str:
    import re

    import src.config
    import src.db
    import src.personal_profile as pp
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(src.config, "IS_DEMO", False)
    monkeypatch.setattr(src.db, "DB_PATH", ROOT / "data" / "tracker.db")
    if residence is not None:
        monkeypatch.setattr(pp, "load_tax_residence", lambda is_demo: residence)
    st.cache_data.clear()
    at = AppTest.from_file(str(PAGE), default_timeout=600).run()
    assert not at.exception, f"page 14 raised: {at.exception}"
    out = [re.sub(r"\s+", " ", el.value) for kind in ("markdown", "caption", "warning")
           for el in getattr(at, kind, []) if isinstance(getattr(el, "value", None), str)]
    return " ".join(out)


def _assumptions_for(residence: dict) -> "list[str]":
    from src.liquidity import residence_names
    p = residence_tax_profile(residence)
    where = residence_names(residence)
    return [f"State and local ordinary ({where}): **{p['state_marginal']:.2%}**",
            f"State and local on gains ({where}): **{p['state_ltcg']:.2%}**",
            f"combined rate on realized gains: **{lc.ltcg_rate(p):.2%}**",
            "exempt from federal tax and **fully taxable by the residence**"]


def test_page_14_renders_with_the_synthetic_residence_live(monkeypatch):
    """DEMO_TAX_RESIDENCE, as demo mode supplies it, on the personal book's figures."""
    _skip_without_personal_inputs()
    body = _render_page_14(monkeypatch, DEMO_TAX_RESIDENCE)
    for line in _assumptions_for(DEMO_TAX_RESIDENCE):
        assert line in body, line


def test_page_14_renders_with_the_private_residence_live(monkeypatch):
    """The private tax_residence, read through load_tax_residence as the page reads it.
    The expected lines are computed from it here, so no private value is written."""
    _skip_without_personal_inputs()
    from src.personal_profile import load_tax_residence
    residence = load_tax_residence(False)
    if not residence["jurisdictions"]:
        pytest.skip(f"no private tax residence: {residence['problem']}")
    body = _render_page_14(monkeypatch)
    for line in _assumptions_for(residence):
        assert line in body, "a line the private residence prices is missing"
    for line in _assumptions_for(DEMO_TAX_RESIDENCE)[:2]:
        assert line not in body


# ── the rates a text states are its figures' rates ────────────────────────────

def _thematic_fixture():
    acct = pd.DataFrame([{"pseudonym": "tod", "display_name": "Individual Taxable (TOD)",
                          "tax_treatment": "taxable"}])
    sec = pd.DataFrame([{"ticker": "SECTR", "name": "Sector", "tax_efficiency": "high",
                         "sleeve_category": "us_large_core"}])
    pos = pd.DataFrame([{"pseudonym": "tod", "symbol": "SECTR", "current_value": 30000.0,
                         "total_gain_loss": 4000.0, "cost_basis_total": 26000.0}])
    return pos, acct, sec


def _register(pos, acct, sec, profile):
    return build_location_register(pos, acct, sec, profile,
                                   lc.SLEEVE_PRIORITY_BY_ACCOUNT_TYPE,
                                   lc.ACCOUNT_SHELTER_PRIORITY)


@pytest.mark.parametrize("residence", [DEMO_TAX_RESIDENCE, OTHER_RESIDENCE, NO_RESIDENCE])
def test_the_rate_placeholders_are_read_from_the_profile(residence):
    profile = residence_tax_profile(residence)
    hr = {"income_known": True, "total": 0.0, "consumed": 0.0, "remaining": 0.0}
    out = la._rate_placeholders(profile, hr)
    assert out["gain_rate"] == f"{lc.ltcg_rate(profile):.2%}"
    if profile["state_ltcg"] > 0:
        assert out["gain_rate_parts"] == (f"{profile['federal_ltcg']:.0%} federal + "
                                          f"{profile['state_ltcg']:.2%} state and local")
    else:
        assert "no state or local tax is included" in out["gain_rate_parts"]
    assert out["bracket_sentence"] == "2026 income puts the 0% bracket out of reach."


def test_the_bracket_sentence_is_chosen_by_the_computed_budget():
    p = SYNTHETIC_TAX_PROFILE
    assert la._rate_placeholders(p, {"income_known": False, "total": 0.0,
                                      "consumed": 0.0, "remaining": 0.0})[
        "bracket_sentence"].startswith("No income is configured")
    room = la._rate_placeholders(p, {"income_known": True, "total": 1234.0,
                                     "consumed": 0.0, "remaining": 1234.0})
    assert room["bracket_sentence"] == ("The 0% bracket still has $1,234 of room in 2026, "
                                        "which these figures do not deduct.")
    assert la._rate_placeholders(None, {})["gain_rate"] is None


@pytest.mark.parametrize("key", ["relocate_gain_side", "frozen_tod_income", "thematic_sprawl"])
def test_no_decision_text_types_a_rate(key):
    """The three cards that state a tax rate read it ({gain_rate}, {gain_rate_parts}):
    their authored prose holds no percent at all."""
    import re
    g = next(x for x in la.ACTION_GROUPS if x["key"] == key)
    authored = " ".join(str(g.get(f) or "") for f in ("action", "pros", "cons"))
    assert not re.search(r"\d+(?:\.\d+)?\s?%", authored), key
    assert "{gain_rate}" in authored or "{unwind_sentence}" in authored


def test_the_thematic_unwind_sentence_states_the_profiles_rate_and_tax():
    pos, acct, sec = _thematic_fixture()
    g = {"key": "t", "symbols": ["SECTR"], "case_filter": None,
         "accounts": ["Individual Taxable (TOD)"], "population": "matched_symbols"}
    for residence in (DEMO_TAX_RESIDENCE, OTHER_RESIDENCE):
        profile = residence_tax_profile(residence)
        reg = _register(pos, acct, sec, profile)
        out = la.resolve_placeholders(g, pos, acct, sec, reg, tax_profile=profile)
        tax = la._fmt_dollars(4000.0 * lc.ltcg_rate(profile))
        assert out["gain_tax"] == tax
        assert out["unwind_sentence"] == (
            f"Unwinding realizes $4,000 of gain, costing {tax} in capital-gains tax at "
            f"{lc.ltcg_rate(profile):.2%} ({out['gain_rate_parts']}) — for no location "
            "benefit.")


def test_a_loss_unwinds_for_no_tax_and_says_so():
    pos, acct, sec = _thematic_fixture()
    pos = pos.assign(total_gain_loss=-500.0, cost_basis_total=30500.0)
    g = {"key": "t", "symbols": ["SECTR"], "case_filter": None,
         "accounts": ["Individual Taxable (TOD)"], "population": "matched_symbols"}
    reg = _register(pos, acct, sec, SYNTHETIC_TAX_PROFILE)
    out = la.resolve_placeholders(g, pos, acct, sec, reg, tax_profile=SYNTHETIC_TAX_PROFILE)
    assert out["gain_tax"] == "$0"
    assert out["unwind_sentence"].startswith("Unwinding realizes no net gain (")


# ── a recorded decision keeps its decision; its figures are today's ─────────────

def test_every_recorded_decision_names_a_commit_and_a_date():
    recorded = {g["key"]: g["decision"] for g in la.ACTION_GROUPS if g.get("decision")}
    assert set(recorded) == {"thematic_sprawl", "frozen_tod_income", "saa_sleeves_taxable"}
    for key, rec in recorded.items():
        assert rec["recorded"] == "2026-07-09", key
        assert len(rec["commit"]) == 7, key


def _review(gain: float, profile: dict, remaining: float):
    pos, acct, sec = _thematic_fixture()
    pos = pos.assign(total_gain_loss=gain, cost_basis_total=30000.0 - gain)
    g = {"key": "t", "symbols": ["SECTR"], "case_filter": None,
         "accounts": ["Individual Taxable (TOD)"], "population": "matched_symbols",
         "status": "accepted",
         "decision": {"recorded": "2026-07-09", "commit": "ab62f8d",
                      "reason_by_rates": "gain_tax"}}
    reg = _register(pos, acct, sec, profile)
    return g, la.decision_review(g, pos, acct, reg, profile, {"remaining": remaining})


def test_a_decision_whose_reason_holds_is_not_flagged():
    g, rec = _review(4000.0, SYNTHETIC_TAX_PROFILE, 0.0)
    assert rec["holds"] is True and rec["review"] is None
    assert rec["recorded"] == "2026-07-09" and rec["commit"] == "ab62f8d"
    assert rec["tax"] == pytest.approx(4000.0 * lc.ltcg_rate(SYNTHETIC_TAX_PROFILE))


def test_a_decision_todays_rates_reverse_is_flagged_and_left_as_it_was():
    """With no state tax and the 0% bracket covering the whole gain, unwinding costs
    nothing, so "costs capital-gains tax" no longer holds: flagged, not changed."""
    profile = residence_tax_profile(NO_RESIDENCE)
    g, rec = _review(4000.0, profile, 10_000.0)
    assert rec["holds"] is False and rec["tax"] == 0.0
    assert "no longer holds" in rec["review"]
    assert g["status"] == "accepted" and g["decision"]["recorded"] == "2026-07-09"


def test_a_decision_with_no_rate_dependent_reason_is_never_flagged():
    g = next(x for x in la.ACTION_GROUPS if x["key"] == "saa_sleeves_taxable")
    pos, acct, sec = _thematic_fixture()
    reg = _register(pos, acct, sec, SYNTHETIC_TAX_PROFILE)
    rec = la.decision_review(g, pos, acct, reg, SYNTHETIC_TAX_PROFILE, {"remaining": 0.0})
    assert rec["holds"] is True and rec["reason"] is None


# ── the TOD group follows the book ────────────────────────────────────────────

_TOD = "Individual Taxable (TOD)"


def _tod_book(muni: str, credit: str = "JCPB"):
    """The frozen TOD book's shape: five income funds and a single stock, as the seed
    files them. ``muni`` and ``credit`` are the two the manager has swapped before."""
    acct = pd.DataFrame([{"pseudonym": "tod", "display_name": _TOD,
                          "tax_treatment": "taxable", "managed_by": "external"}])
    # (ticker, sleeve, tax_efficiency), as data/seed/securities_household.csv files them
    rows = [("BILPX", "liquid_alt", "low"), ("GAOSX", "multi_asset", "medium"),
            (muni, "high_yield_muni", "medium"), ("JHEQX", "hedged_equity", "medium"),
            (credit, "core_fi_credit", "low"), ("MCO", "single_stock", "high")]
    sec = pd.DataFrame([{"ticker": t, "name": t, "tax_efficiency": e,
                         "sleeve_category": s} for t, s, e in rows])
    pos = pd.DataFrame([{"pseudonym": "tod", "symbol": t, "current_value": 10000.0,
                         "total_gain_loss": 300.0, "cost_basis_total": 9700.0}
                        for t, _, _ in rows])
    return pos, acct, sec


def _tod_group(pos, acct, sec):
    return next(g for g in la.bind_groups_to_book(sec, pos, acct)
                if g["key"] == "frozen_tod_income")


def test_the_authored_tod_group_types_no_tickers():
    g = next(x for x in la.ACTION_GROUPS if x["key"] == "frozen_tod_income")
    assert g["symbols"] is None and g["book_sleeves"]
    assert g["accounts"] == [_TOD]


def test_a_managers_swap_reaches_the_group_with_no_edit():
    """A synthetic swap: the national muni fund and the core-plus fund are replaced.
    The group's tickers, its municipal fund and its caption follow the book."""
    before = _tod_book("GHYIX", "HLIPX")
    after = _tod_book("HIMU", "JCPB")
    gb, ga = _tod_group(*before), _tod_group(*after)
    assert gb["symbols"] == ["BILPX", "GAOSX", "GHYIX", "HLIPX", "JHEQX"]
    assert ga["symbols"] == ["BILPX", "GAOSX", "HIMU", "JCPB", "JHEQX"]
    assert (gb["muni_symbols"], ga["muni_symbols"]) == (["GHYIX"], ["HIMU"])
    pos, acct, sec = after
    reg = _register(pos, acct, sec, SYNTHETIC_TAX_PROFILE)
    cap = la.resolve_caption(ga, pos, acct, reg)
    assert "HIMU is a federally exempt municipal fund" in cap and "GHYIX" not in cap
    cons = la.render_prose(ga["cons"], la.resolve_placeholders(
        ga, pos, acct, sec, reg, tax_profile=SYNTHETIC_TAX_PROFILE))
    assert "HIMU is a municipal fund" in cons and "GHYIX" not in cons
    la.assert_full_coverage(reg, la.bind_groups_to_book(sec, pos, acct))


def test_the_single_stock_and_other_groups_symbols_stay_out():
    pos, acct, sec = _tod_book("HIMU")
    assert "MCO" not in _tod_group(pos, acct, sec)["symbols"]


def test_a_book_with_no_municipal_fund_says_so():
    pos, acct, sec = _tod_book("HIMU")
    keep = pos["symbol"] != "HIMU"
    pos, sec = pos[keep], sec[sec["ticker"] != "HIMU"]
    g = _tod_group(pos, acct, sec)
    assert g["muni_symbols"] == []
    reg = _register(pos, acct, sec, SYNTHETIC_TAX_PROFILE)
    assert la.resolve_placeholders(g, pos, acct, sec, reg)["muni_sentence"] == (
        "No municipal fund sits in this book.")


def test_the_household_views_fixed_income_list_is_read_from_the_book():
    """Household View's synthesis named six fixed-income tickers it had typed; it reads
    the externally managed accounts' fixed-income holdings now, so the swap reaches it."""
    from src.household import advisor_fixed_income_tickers
    pos, acct, sec = _tod_book("GHYIX", "HLIPX")
    assert advisor_fixed_income_tickers(pos, acct, sec) == ["GHYIX", "HLIPX"]
    pos, acct, sec = _tod_book("HIMU", "JCPB")
    assert advisor_fixed_income_tickers(pos, acct, sec) == ["HIMU", "JCPB"]
    self_directed = acct.assign(managed_by="self")
    assert advisor_fixed_income_tickers(pos, self_directed, sec) == []


def test_binding_never_changes_the_authored_groups():
    pos, acct, sec = _tod_book("HIMU")
    la.bind_groups_to_book(sec, pos, acct)
    assert next(x for x in la.ACTION_GROUPS
                if x["key"] == "frozen_tod_income")["symbols"] is None
