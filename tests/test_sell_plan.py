"""The Liquidity page's sell plan (src/sell_plan.py) and its tax residence (owner's
decision, November 3, 2026).

Every position here is SYNTHETIC: invented tickers and values for the unit tests, and
invented values on real seed tickers for the page renders, which run on a personal book
bootstrapped from nothing in a temporary directory. No personal figure is read or
written, and no test reads data/uploads or the real private profile.

What is pinned:
  * the order — cash and money market, then losses by depth, then the lowest gain as a
    share of cost (not in dollars);
  * whole positions first, then the last one in part, pro rata;
  * a loss position whose ticker another account holds is skipped and flagged, IRAs
    and accounts outside the household included; a gain position is not;
  * no tax-advantaged account is ever picked, however short the taxable side falls;
  * the allocation looks blends through their fund_compositions rows, counts Fidelity's
    pending-activity line as cash, and takes proceeds out of the household;
  * the ladder's tax note names the residence's jurisdictions, read from the private
    profile, and says so when there is none; the page names no home state;
  * demo mode still hides the page.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
NAN = float("nan")


def _sp():
    """The module under test, imported per test so each test fails on its own."""
    import src.sell_plan as sp
    return sp


# ── Synthetic book ──────────────────────────────────────────────────────────────

ACCOUNTS = pd.DataFrame([
    {"pseudonym": "tax_a", "tax_treatment": "taxable", "display_name": "Taxable A"},
    {"pseudonym": "tax_b", "tax_treatment": "taxable", "display_name": "Taxable B"},
    {"pseudonym": "roth", "tax_treatment": "roth_ira", "display_name": "Roth IRA"},
    {"pseudonym": "trad", "tax_treatment": "traditional_ira", "display_name": "Trad IRA"},
    {"pseudonym": "k401", "tax_treatment": "workplace_plan", "display_name": "401(k)"},
    {"pseudonym": "hsa", "tax_treatment": "hsa", "display_name": "HSA"},
])

SECURITIES = pd.DataFrame([
    {"ticker": t, "sleeve_category": s, "is_in_saa": saa, "asset_class_id": cid}
    for t, s, saa, cid in [
        ("MMF", "cash", 0, None), ("CASH2", "cash", 0, None),
        ("L_DEEP", "us_large_core", 0, None), ("L_SHALLOW", "intl_developed", 0, None),
        ("G_LOWSHARE", "core_fi_credit", 0, None), ("G_HIGHSHARE", "thematic", 0, None),
        ("WASHME", "emerging_markets", 0, None), ("WASH2", "us_mid_cap", 0, None),
        ("WASH3", "us_small_core", 0, None), ("GAINDUP", "us_large_value", 0, None),
        ("ROTHBIG", "us_large_core", 0, None), ("TRADBIG", "intl_developed", 0, None),
        ("NOBASIS", "us_large_growth", 0, None),
    ]
])


def _pos(pseudonym, symbol, value, cost=NAN, quantity=None):
    """One synthetic position. Cash passes no cost; its gain is missing, as Fidelity's is."""
    gain = value - cost if cost == cost else NAN
    return {"pseudonym": pseudonym, "account_name": pseudonym, "symbol": symbol,
            "description": symbol, "quantity": (value / 10.0 if quantity is None else quantity),
            "current_value": float(value), "cost_basis_total": cost,
            "total_gain_loss": gain, "type": "Cash"}


def _order_book() -> pd.DataFrame:
    return pd.DataFrame([
        _pos("tax_a", "MMF", 500.0, quantity=NAN),
        _pos("tax_b", "CASH2", 100.0, quantity=NAN),
        # Losses: the DEEPER share first, though its dollar loss is the smaller.
        _pos("tax_a", "L_SHALLOW", 9_500.0, 10_000.0),     # -5%,  -$500
        _pos("tax_b", "L_DEEP", 800.0, 1_000.0),           # -20%, -$200
        # Gains: the LOWER share first, though its dollar gain is the larger.
        _pos("tax_a", "G_HIGHSHARE", 300.0, 200.0),        # +50%, +$100
        _pos("tax_b", "G_LOWSHARE", 20_400.0, 20_000.0),   # +2%,  +$400
        # Tax-advantaged money the plan must never touch.
        _pos("roth", "ROTHBIG", 50_000.0, 10_000.0),
        _pos("trad", "TRADBIG", 50_000.0, 49_000.0),
        _pos("roth", "MMF", 5_000.0, quantity=NAN),
    ])


# ── Order ───────────────────────────────────────────────────────────────────────

def test_the_plan_sells_cash_then_losses_then_the_lowest_gain_share():
    sp = _sp()
    book = _order_book()
    plan = sp.build_sell_plan(31_600.0, book, ACCOUNTS, SECURITIES)
    assert list(plan.picks["symbol"]) == [
        "MMF", "CASH2", "L_DEEP", "L_SHALLOW", "G_LOWSHARE", "G_HIGHSHARE"]
    assert list(plan.picks["group"]) == ["cash", "cash", "loss", "loss", "gain", "gain"]
    assert not plan.picks["partial"].any(), "the amount is every taxable dollar: all whole"
    assert plan.raised == pytest.approx(31_600.0) and plan.shortfall == 0.0
    # Cash realizes nothing and carries no basis; the rest realize their own gain.
    cash = plan.picks[plan.picks["group"] == "cash"]
    assert (cash["realized_gain"] == 0.0).all() and cash["cost_basis"].isna().all()
    assert plan.realized_gain == pytest.approx(-500 - 200 + 400 + 100)


def test_a_loss_or_gain_is_ranked_by_its_share_of_cost_not_its_dollars():
    """The contrast that separates share-of-cost from dollars: in each pair the
    position ranked first has the SMALLER dollar figure's opposite."""
    sp = _sp()
    picks = sp.build_sell_plan(31_600.0, _order_book(), ACCOUNTS, SECURITIES).picks
    order = list(picks["symbol"])
    assert order.index("L_DEEP") < order.index("L_SHALLOW")          # -$200 before -$500
    assert order.index("G_LOWSHARE") < order.index("G_HIGHSHARE")    # +$400 before +$100


# ── Whole positions first, then the last one in part ──────────────────────────────

def test_whole_positions_first_then_the_last_one_in_part():
    sp = _sp()
    amount = 500 + 100 + 800 + 9_500 + 1_020.0          # cash + losses + part of G_LOWSHARE
    plan = sp.build_sell_plan(amount, _order_book(), ACCOUNTS, SECURITIES)
    assert list(plan.picks["symbol"]) == ["MMF", "CASH2", "L_DEEP", "L_SHALLOW", "G_LOWSHARE"]
    assert list(plan.picks["partial"]) == [False, False, False, False, True]
    last = plan.picks.iloc[-1]
    f = 1_020.0 / 20_400.0
    assert last["value"] == pytest.approx(1_020.0)
    assert last["shares"] == pytest.approx(2_040.0 * f)        # quantity = value / 10
    assert last["cost_basis"] == pytest.approx(20_000.0 * f, abs=0.01)
    assert last["realized_gain"] == pytest.approx(400.0 * f, abs=0.01)
    assert plan.raised == pytest.approx(amount) and plan.shortfall == 0.0
    assert "G_HIGHSHARE" not in set(plan.picks["symbol"]), "nothing after the partial pick"


def test_an_amount_inside_the_first_position_sells_part_of_it_alone():
    sp = _sp()
    plan = sp.build_sell_plan(200.0, _order_book(), ACCOUNTS, SECURITIES)
    assert list(plan.picks["symbol"]) == ["MMF"]
    assert plan.picks.iloc[0]["partial"] and plan.picks.iloc[0]["value"] == 200.0


@pytest.mark.parametrize("amount", [0, 0.0, -50.0, None])
def test_no_amount_plans_nothing(amount):
    sp = _sp()
    plan = sp.build_sell_plan(amount, _order_book(), ACCOUNTS, SECURITIES)
    assert plan.picks.empty and plan.raised == 0.0 and plan.shortfall == 0.0


# ── Wash sales ────────────────────────────────────────────────────────────────────

def _wash_book() -> pd.DataFrame:
    return pd.DataFrame([
        _pos("tax_a", "WASHME", 900.0, 1_000.0),     # loss; the Roth holds it too
        _pos("roth", "WASHME", 400.0, 300.0),
        _pos("tax_b", "WASH2", 700.0, 1_000.0),      # loss; ANOTHER TAXABLE account holds it
        _pos("tax_a", "WASH2", 50.0, 40.0),
        _pos("tax_a", "WASH3", 600.0, 800.0),        # loss; only an account outside the household
        _pos("tax_a", "GAINDUP", 1_100.0, 1_000.0),  # GAIN; the IRA holds it too: not a wash
        _pos("trad", "GAINDUP", 500.0, 400.0),
        _pos("tax_a", "L_DEEP", 800.0, 1_000.0),     # loss held nowhere else: sold
    ])


def test_a_loss_held_in_another_account_is_skipped_and_flagged():
    sp = _sp()
    book = _wash_book()
    everywhere = pd.concat([book, pd.DataFrame([_pos("k401", "WASH3", 100.0, 90.0)])],
                           ignore_index=True)
    plan = sp.build_sell_plan(100_000.0, book, ACCOUNTS, SECURITIES, everywhere_df=everywhere)
    skipped = plan.wash_sale_skips.set_index("symbol")
    assert set(skipped.index) == {"WASHME", "WASH2", "WASH3"}
    assert skipped.loc["WASHME", "also_held_in"] == "Roth IRA"
    assert skipped.loc["WASH2", "also_held_in"] == "Taxable A"
    assert skipped.loc["WASH3", "also_held_in"] == "401(k)"
    assert skipped.loc["WASHME", "loss"] == pytest.approx(-100.0)
    picked = set(zip(plan.picks["pseudonym"], plan.picks["symbol"]))
    flagged = {("tax_a", "WASHME"), ("tax_b", "WASH2"), ("tax_a", "WASH3")}
    assert not picked & flagged, "a flagged loss is never sold"
    # A gain, or a loss held nowhere else, is sold, including tax_a's WASH2: it is the
    # twin of a flagged loss, but at a gain, so selling it washes nothing.
    assert {("tax_a", "GAINDUP"), ("tax_a", "L_DEEP"), ("tax_a", "WASH2")} <= picked


def test_the_wash_check_reads_only_the_positions_it_is_given_by_default():
    """Without everywhere_df the check reads positions_df: WASH3's only twin is outside
    it, so WASH3 is sold. The page passes every account in the file."""
    sp = _sp()
    plan = sp.build_sell_plan(100_000.0, _wash_book(), ACCOUNTS, SECURITIES)
    assert "WASH3" in set(plan.picks["symbol"])


# ── Tax-advantaged accounts ───────────────────────────────────────────────────────

def test_no_tax_advantaged_account_is_ever_picked():
    sp = _sp()
    book = pd.concat([_order_book(), pd.DataFrame([
        _pos("k401", "TRADBIG", 80_000.0, 60_000.0),
        _pos("hsa", "MMF", 3_000.0, quantity=NAN),
    ])], ignore_index=True)
    taxable_total = 500 + 100 + 9_500 + 800 + 300 + 20_400.0
    plan = sp.build_sell_plan(1_000_000.0, book, ACCOUNTS, SECURITIES)
    assert set(plan.picks["pseudonym"]) <= {"tax_a", "tax_b"}
    assert plan.raised == pytest.approx(taxable_total)
    assert plan.shortfall == pytest.approx(1_000_000.0 - taxable_total)


def test_a_position_with_no_cost_basis_is_skipped_not_guessed():
    sp = _sp()
    book = pd.DataFrame([_pos("tax_a", "NOBASIS", 700.0), _pos("tax_a", "L_DEEP", 800.0, 1_000.0)])
    plan = sp.build_sell_plan(10_000.0, book, ACCOUNTS, SECURITIES)
    assert list(plan.no_basis_skips["symbol"]) == ["NOBASIS"]
    assert list(plan.picks["symbol"]) == ["L_DEEP"]


def test_pending_activity_is_never_picked():
    sp = _sp()
    book = pd.DataFrame([_pos("tax_a", "Pending activity", 25.0, quantity=NAN),
                         _pos("tax_a", "MMF", 100.0, quantity=NAN)])
    plan = sp.build_sell_plan(1_000.0, book, ACCOUNTS, SECURITIES)
    assert list(plan.picks["symbol"]) == ["MMF"]


# ── Allocation before and after, looked through ───────────────────────────────────

ALLOC_SECURITIES = pd.DataFrame([
    {"ticker": t, "sleeve_category": s, "is_in_saa": saa, "asset_class_id": cid}
    for t, s, saa, cid in [
        ("US1", "us_large_core", 1, 5), ("INTL1", "intl_developed", 1, 6),
        ("BND", "core_fi_credit", 1, 7), ("REIT", "real_assets_reit", 1, 8),
        ("BLEND", "multi_asset", 0, 15), ("THEME", "thematic", 0, 15),
        ("HEDGE", "hedged_equity", 0, 15), ("COIN", "crypto", 0, 15),
        ("MMF", "cash", 1, 15),
    ]
])
COMPOSITIONS = pd.DataFrame([
    {"fund_symbol": "BLEND", "underlying_sleeve": "us_large_core", "weight": 0.6},
    {"fund_symbol": "BLEND", "underlying_sleeve": "core_fi_treasury", "weight": 0.3},
    {"fund_symbol": "BLEND", "underlying_sleeve": "cash", "weight": 0.1},
])
ASSET_CLASSES = pd.DataFrame([
    {"asset_class_id": i, "name": n, "parent_id": p, "target_weight": w}
    for i, n, p, w in [
        (1, "Equity", None, 0.8), (2, "Income", None, 0.1), (3, "Real Assets", None, 0.1),
        (4, "Cash", None, 0.0), (5, "US Core", 1, 0.5), (6, "Intl", 1, 0.3),
        (7, "Core FI", 2, 0.1), (8, "Real", 3, 0.1), (9, "Cash / MMF", 4, 0.0),
        (15, "Other / Non-SAA", None, 0.0),
    ]
])


def _alloc_book() -> pd.DataFrame:
    return pd.DataFrame([
        _pos("tax_a", "US1", 4_000.0, 3_000.0), _pos("roth", "INTL1", 2_000.0, 1_500.0),
        _pos("tax_a", "BLEND", 1_000.0, 900.0), _pos("trad", "BND", 1_000.0, 1_000.0),
        _pos("tax_a", "THEME", 500.0, 400.0), _pos("tax_b", "HEDGE", 500.0, 450.0),
        _pos("tax_b", "REIT", 300.0, 250.0), _pos("tax_b", "COIN", 200.0, 300.0),
        _pos("tax_a", "MMF", 550.0, quantity=NAN),
        _pos("tax_a", "Pending activity", -50.0, quantity=NAN),
    ])


def test_policy_targets_map_each_class_through_its_securities_or_its_root():
    sp = _sp()
    t = sp.policy_targets(ASSET_CLASSES, ALLOC_SECURITIES)
    assert t == pytest.approx({sp.US_STOCKS: 50.0, sp.INTL_STOCKS: 30.0, sp.BONDS: 10.0,
                               sp.REAL_ASSETS: 10.0, sp.CASH: 0.0})
    assert sp.THEMES not in t and sp.HEDGED not in t and sp.ALTERNATIVES not in t


def test_the_allocation_looks_blends_through_before_and_after_the_sales():
    sp = _sp()
    targets = sp.policy_targets(ASSET_CLASSES, ALLOC_SECURITIES)
    sales = pd.DataFrame([{"pseudonym": "tax_a", "symbol": "BLEND", "value": 500.0},
                          {"pseudonym": "tax_a", "symbol": "THEME", "value": 500.0}])
    t = sp.allocation_table(_alloc_book(), ALLOC_SECURITIES, COMPOSITIONS, targets, sales)
    assert list(t["broad_class"]) == list(sp.BROAD_CLASSES)
    by = t.set_index("broad_class")
    # Before: 10,000 in all. BLEND's 1,000 is 600 US, 300 bonds, 100 cash; the
    # pending line's -50 is cash.
    before = {sp.US_STOCKS: 4_600, sp.INTL_STOCKS: 2_000, sp.THEMES: 500, sp.HEDGED: 500,
              sp.BONDS: 1_300, sp.REAL_ASSETS: 300, sp.ALTERNATIVES: 200, sp.CASH: 600}
    for c, v in before.items():
        assert by.loc[c, "before_value"] == pytest.approx(v), c
        assert by.loc[c, "before_pct"] == pytest.approx(v / 10_000 * 100), c
    # After: half of BLEND, pro rata, and all of THEME, leave; 9,000 remain.
    after = {sp.US_STOCKS: 4_300, sp.INTL_STOCKS: 2_000, sp.THEMES: 0, sp.HEDGED: 500,
             sp.BONDS: 1_150, sp.REAL_ASSETS: 300, sp.ALTERNATIVES: 200, sp.CASH: 550}
    for c, v in after.items():
        assert by.loc[c, "after_value"] == pytest.approx(v), c
        assert by.loc[c, "after_pct"] == pytest.approx(v / 9_000 * 100), c
    assert by["before_pct"].sum() == pytest.approx(100.0)
    assert by["after_pct"].sum() == pytest.approx(100.0)
    assert by.loc[sp.US_STOCKS, "target_pct"] == pytest.approx(50.0)
    assert pd.isna(by.loc[sp.THEMES, "target_pct"]), "the policy sets no target for themes"


def test_a_plans_picks_feed_the_allocation_as_its_sales():
    sp = _sp()
    book = _alloc_book()
    plan = sp.build_sell_plan(700.0, book, ACCOUNTS, ALLOC_SECURITIES)
    assert list(plan.picks["symbol"]) == ["MMF", "COIN"] and plan.picks.iloc[-1]["partial"]
    t = sp.allocation_table(book, ALLOC_SECURITIES, COMPOSITIONS,
                            sp.policy_targets(ASSET_CLASSES, ALLOC_SECURITIES),
                            plan.picks[["pseudonym", "symbol", "value"]]).set_index("broad_class")
    assert t.loc[sp.CASH, "after_value"] == pytest.approx(600 - 550)
    assert t.loc[sp.ALTERNATIVES, "after_value"] == pytest.approx(200 - 150)
    assert t["after_value"].sum() == pytest.approx(10_000 - 700)


def test_whole_position_sales_names_what_it_sells_and_refuses_what_is_not_held():
    sp = _sp()
    s = sp.whole_position_sales(_alloc_book(), "tax_a", ["BLEND", "THEME"])
    assert dict(zip(s["symbol"], s["value"])) == {"BLEND": 1_000.0, "THEME": 500.0}
    with pytest.raises(ValueError, match="holds no"):
        sp.whole_position_sales(_alloc_book(), "tax_a", ["HEDGE"])


def test_a_blend_without_compositions_and_an_unknown_sleeve_raise():
    sp = _sp()
    targets = sp.policy_targets(ASSET_CLASSES, ALLOC_SECURITIES)
    with pytest.raises(ValueError, match="blend"):
        sp.allocation_table(_alloc_book(), ALLOC_SECURITIES, COMPOSITIONS.iloc[0:0], targets)
    odd = pd.concat([ALLOC_SECURITIES, pd.DataFrame([{"ticker": "ODD", "sleeve_category":
                     "unheard_of", "is_in_saa": 0, "asset_class_id": 15}])], ignore_index=True)
    book = pd.concat([_alloc_book(), pd.DataFrame([_pos("tax_a", "ODD", 10.0, 10.0)])])
    with pytest.raises(ValueError, match="no broad class"):
        sp.allocation_table(book, odd, COMPOSITIONS, targets)


def test_a_policy_class_spanning_two_broad_classes_raises():
    sp = _sp()
    sec = ALLOC_SECURITIES.copy()
    sec.loc[sec["ticker"] == "INTL1", "asset_class_id"] = 5     # US Core now holds intl too
    with pytest.raises(ValueError, match="spans"):
        sp.policy_targets(ASSET_CLASSES, sec)


def test_every_sleeve_has_a_broad_class_or_is_a_blend():
    """Complete against the sleeve universe and the seed, so no held sleeve can fall
    through to a default (there is none: the allocation raises)."""
    sp = _sp()
    from src.location_config import SLEEVE_TAX_CHARACTER
    with (ROOT / "data" / "seed" / "securities_household.csv").open(encoding="utf-8") as fh:
        seeded = {r["sleeve_category"] for r in csv.DictReader(fh)}
    universe = set(SLEEVE_TAX_CHARACTER) | seeded
    covered = set(sp.BROAD_CLASS_OF_SLEEVE) | sp.BLEND_SLEEVES
    assert universe - covered == set()
    assert not set(sp.BROAD_CLASS_OF_SLEEVE) & sp.BLEND_SLEEVES
    assert set(sp.BROAD_CLASS_OF_SLEEVE.values()) == set(sp.BROAD_CLASSES)


# ── Tax residence: the ladder's tax-cost wording ──────────────────────────────────

SYNTHETIC_RESIDENCE = {
    "jurisdictions": [
        {"name": "Synthetica State", "ordinary_rate": 0.06, "gains_rate": 0.06,
         "source": "Synthetica Revenue Dept., rate table T-1", "source_date": "2026 edition"},
        {"name": "Synthetica City", "ordinary_rate": 0.04, "gains_rate": 0.03,
         "source": "Synthetica City Finance, rate table C-1: over $50,000",
         "source_date": "2026 edition"},
    ],
    "source": "test-synthetic", "problem": None,
}


def _ladder_fixture():
    accounts = pd.DataFrame([
        {"pseudonym": "tax", "tax_treatment": "taxable", "display_name": "Taxable"}])
    positions = pd.DataFrame([
        {"pseudonym": "tax", "symbol": "BIG", "current_value": 1500.0,
         "total_gain_loss": 500.0, "cost_basis_total": 1000.0}])
    return positions, accounts


def test_the_ladders_tax_note_names_the_residences_jurisdictions():
    from src.liquidity import (build_liquidity_ladder, gain_tax_note, residence_names,
                               residence_tax_profile)
    tax = residence_tax_profile(SYNTHETIC_RESIDENCE)
    assert tax["state_marginal"] == pytest.approx(0.10)
    assert tax["state_ltcg"] == pytest.approx(0.09)
    where = residence_names(SYNTHETIC_RESIDENCE)
    assert where == "Synthetica State and Synthetica City"
    pos, acc = _ladder_fixture()
    lad = build_liquidity_ladder(pos, acc, tax, tax_location=where).set_index("symbol")
    note = lad.loc["BIG", "note"]
    assert note == ("Taxable with a meaningful gain — 24.00% on the gain: 15% federal + "
                    "Synthetica State and Synthetica City")
    assert lad.loc["BIG", "cost_to_cash"] == pytest.approx(500.0 * 0.24, abs=0.01)
    assert not re.search(r"\bPA\b|Pennsylvania|3\.07", note)
    assert gain_tax_note(tax, where) in note


def test_with_no_residence_the_note_says_no_state_or_local_tax_is_included():
    from src.liquidity import build_liquidity_ladder, residence_names, residence_tax_profile
    empty = {"jurisdictions": [], "source": "absent", "problem": "no tax_residence"}
    tax = residence_tax_profile(empty)
    assert tax["state_marginal"] == 0.0 and tax["state_ltcg"] == 0.0
    pos, acc = _ladder_fixture()
    lad = build_liquidity_ladder(pos, acc, tax, tax_location=residence_names(empty))
    assert lad.iloc[0]["note"] == ("Taxable with a meaningful gain — 15.00% on the gain: "
                                   "15% federal only; no state or local tax is included")


def test_the_residence_profile_never_reads_tax_profiles_state_keys(monkeypatch):
    """The committed config's state rate must not reach this page's prices."""
    import src.location_config as lc
    from src.liquidity import residence_tax_profile
    monkeypatch.setitem(lc.TAX_PROFILE, "state_marginal", 0.5)
    monkeypatch.setitem(lc.TAX_PROFILE, "state_ltcg", 0.5)
    tax = residence_tax_profile(SYNTHETIC_RESIDENCE)
    assert tax["state_marginal"] == pytest.approx(0.10)
    assert tax["federal_ltcg"] == lc.TAX_PROFILE["federal_ltcg"]


# Every state's name, so the check below singles out none of them.
US_STATES = (
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut",
    "Delaware", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa",
    "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan",
    "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina",
    "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island",
    "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia",
    "Washington", "West Virginia", "Wisconsin", "Wyoming",
)


def test_the_liquidity_code_names_no_state_and_no_state_rate():
    """Where the owner lives is read from the private profile: the committed code that
    prices this page names no state, and the old home's code and rate are gone."""
    assert len(set(US_STATES)) == 50
    for rel in ("pages/15_Liquidity.py", "src/liquidity.py", "src/sell_plan.py",
                "src/personal_profile.py", "personal_profile.example.json"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        named = [s for s in US_STATES if re.search(rf"\b{s}\b", text)]
        assert named == [], f"{rel} names {named}"
        assert not re.search(r"\bPA\b|3\.07", text), rel


def _write_profile(tmp_path, body) -> Path:
    p = tmp_path / "personal_profile.json"
    p.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
    return p


def test_the_residence_is_read_from_the_private_profile(monkeypatch, tmp_path):
    import src.personal_profile as pp
    body = {"tax_residence": {"jurisdictions": SYNTHETIC_RESIDENCE["jurisdictions"]}}
    monkeypatch.setattr(pp, "_PROFILE_PATH", _write_profile(tmp_path, body))
    r = pp.load_tax_residence(is_demo=False)
    assert r["source"] == "private/personal_profile.json" and r["problem"] is None
    assert [j["name"] for j in r["jurisdictions"]] == ["Synthetica State", "Synthetica City"]


@pytest.mark.parametrize("entry, why", [
    ({"name": "X", "ordinary_rate": 0.05, "gains_rate": 0.05, "source_date": "2026"}, "source"),
    ({"name": "X", "ordinary_rate": 0.05, "gains_rate": 0.05, "source": "T"}, "source"),
    ({"name": "X", "ordinary_rate": 5.0, "gains_rate": 0.05, "source": "T",
      "source_date": "2026"}, "outside"),
    ({"name": "X", "ordinary_rate": None, "gains_rate": 0.05, "source": "T",
      "source_date": "2026"}, "not a number"),
    ({"ordinary_rate": 0.05, "gains_rate": 0.05, "source": "T", "source_date": "2026"}, "no name"),
])
def test_an_uncited_or_malformed_jurisdiction_is_refused(monkeypatch, tmp_path, entry, why):
    import src.personal_profile as pp
    body = {"tax_residence": {"jurisdictions": [entry]}}
    monkeypatch.setattr(pp, "_PROFILE_PATH", _write_profile(tmp_path, body))
    r = pp.load_tax_residence(is_demo=False)
    assert r["jurisdictions"] == [] and r["source"] == "invalid" and why in r["problem"]


@pytest.mark.parametrize("body, source", [
    (None, "absent"), ("{ not json", "invalid"), ({"ordinary_income": 1}, "absent")])
def test_a_missing_residence_says_why(monkeypatch, tmp_path, body, source):
    import src.personal_profile as pp
    path = tmp_path / "nope.json" if body is None else _write_profile(tmp_path, body)
    monkeypatch.setattr(pp, "_PROFILE_PATH", path)
    r = pp.load_tax_residence(is_demo=False)
    assert r["jurisdictions"] == [] and r["source"] == source and r["problem"]


def test_demo_takes_the_synthetic_residence_and_never_the_private_one(monkeypatch, tmp_path):
    import src.personal_profile as pp
    body = {"tax_residence": {"jurisdictions": SYNTHETIC_RESIDENCE["jurisdictions"]}}
    monkeypatch.setattr(pp, "_PROFILE_PATH", _write_profile(tmp_path, body))
    r = pp.load_tax_residence(is_demo=True)
    assert r["source"] == "demo-synthetic"
    assert r["jurisdictions"] == pp.DEMO_TAX_RESIDENCE["jurisdictions"]
    assert all("synthetic" in j["name"] for j in r["jurisdictions"])


# ── The seed: HIMU and the four names Fidelity's export uses ──────────────────────

def test_the_seed_carries_himu_and_fidelitys_names():
    with (ROOT / "data" / "seed" / "securities_household.csv").open(encoding="utf-8") as fh:
        rows = {r["symbol"]: r for r in csv.DictReader(fh)}
    assert (rows["HIMU"]["description"], rows["HIMU"]["sleeve_category"]) == (
        "iShares High Yield Muni Active ETF", "high_yield_muni")
    assert {s: (rows[s]["description"], rows[s]["sleeve_category"]) for s in
            ("BFRIX", "GBOSX", "FIWDX", "BILPX")} == {
        "BFRIX": ("BlackRock Floating Rate Income Portfolio Institutional", "floating_rate"),
        "GBOSX": ("JPMorgan Global Bond Opportunities Fund Class I", "multi_sector_fi"),
        "FIWDX": ("Fidelity Advisor Strategic Income Fund Class Z", "multi_sector_fi"),
        "BILPX": ("BlackRock Event Driven Equity Fund Institutional", "liquid_alt"),
    }


# ── The page: demo hides it; personal renders the plan, writing nothing ───────────

def _page_app():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(ROOT / "pages" / "15_Liquidity.py"), default_timeout=120)


def test_demo_mode_still_hides_the_page(monkeypatch):
    import src.config
    monkeypatch.setattr(src.config, "IS_DEMO", True)
    at = _page_app().run()
    assert not at.exception
    assert [i.value for i in at.info] == [
        "The liquidity hierarchy is available in personal mode only."]
    assert len(at.number_input) == 0 and len(at.dataframe) == 0 and len(at.metric) == 0


@pytest.fixture(scope="module")
def bootstrapped_book(tmp_path_factory):
    """A personal book bootstrapped from nothing (schema, SAA, seed securities incl.
    HIMU, fund compositions, household accounts), offline, with no positions file."""
    import socket
    import src.bootstrap as bootstrap
    import src.db as db
    import src.household_data as household_data
    import src.macro as macro
    import src.prices as prices
    from tests.conftest import _pin_mode

    def _offline(*_a, **_k):
        raise OSError("network blocked: the synthetic book is built offline")

    tmp = tmp_path_factory.mktemp("sell_plan_book")
    book = tmp / "tracker.db"
    book.write_bytes(b"")
    (tmp / "uploads").mkdir()
    with pytest.MonkeyPatch.context() as mp:
        _pin_mode(mp, "personal")
        mp.setattr(db, "DB_PATH", book)
        mp.setattr(db, "_migrated_paths", set())
        mp.setattr(db, "_RUNTIME_CACHE", None)
        mp.setattr(bootstrap, "_bootstrapped", set())
        mp.setattr(household_data, "_UPLOADS_DIR", tmp / "uploads")
        mp.setattr(socket, "getaddrinfo", _offline)
        mp.setattr(prices._SESSION, "get", _offline)
        mp.setattr(macro, "fetch_fred_series", _offline)
        bootstrap.bootstrap_personal_db()
    return book


# Synthetic positions on real seed tickers, in the bootstrapped accounts.
PAGE_POSITIONS = pd.DataFrame([
    _pos("acct_01", "SPAXX", 1_000.0, quantity=NAN),
    _pos("acct_01", "VGIT", 2_000.0, 2_200.0),          # loss, held nowhere else
    _pos("acct_01", "IEMG", 1_500.0, 1_800.0),          # loss, the Roth holds it: skipped
    _pos("acct_taxable_02", "GAOSX", 10_000.0, 9_500.0),  # +5.3%, a looked-through blend
    _pos("acct_taxable_02", "JEPI", 3_000.0, 2_970.0),    # +1.0%
    _pos("acct_taxable_02", "QQQ", 2_000.0, 1_000.0),     # +100%: a Tier 2 ladder row
    _pos("acct_roth_01", "IEMG", 4_000.0, 3_000.0),
    _pos("acct_trad_ira_01", "VOO", 20_000.0, 12_000.0),
    _pos("acct_wkpl_02", "RFUTX", 30_000.0, quantity=1_000.0),
])
PAGE_AS_OF = "2026-11-02"


def _personal_page(mp, book, tmp_dir, residence):
    """The page, in personal mode, on a COPY of the bootstrapped book, with the
    synthetic positions, Roth profile and residence. Returns (AppTest, the copy)."""
    from datetime import date

    import src.config
    import src.db as db
    import src.household_data as household_data
    import src.personal_profile as pp
    import streamlit as st
    from tests.conftest import _pin_mode

    copy = tmp_dir / "book.db"
    shutil.copyfile(book, copy)
    _pin_mode(mp, "personal")
    mp.setattr(src.config, "IS_DEMO", False)
    mp.setattr(db, "DB_PATH", copy)
    mp.setattr(db, "_migrated_paths", set())
    mp.setattr(db, "_RUNTIME_CACHE", None)
    path = tmp_dir / "Portfolio_Positions_Nov-02-2026.csv"
    mp.setattr(household_data, "load_latest_positions", lambda *a, **k:
               household_data.LoadedPositions(PAGE_POSITIONS.copy(), path,
                                              date.fromisoformat(PAGE_AS_OF)))
    mp.setattr(pp, "load_roth_profile", lambda is_demo, as_of: {
        "roth_contribution_basis": 1_000.0, "date_of_birth": None,
        "is_qualified_age": False, "source": "test-synthetic"})
    mp.setattr(pp, "load_tax_residence", lambda is_demo: residence)
    st.cache_data.clear()
    return _page_app(), copy


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _frame(at, column):
    found = [d.value for d in at.dataframe if column in list(d.value.columns)]
    assert found, f"no table with a {column!r} column"
    return found[0]


def test_the_page_renders_the_plan_and_writes_nothing(monkeypatch, tmp_path, bootstrapped_book):
    at, book = _personal_page(monkeypatch, bootstrapped_book, tmp_path, SYNTHETIC_RESIDENCE)
    at.run()
    assert not at.exception, at.exception
    before = _sha(book)
    at.number_input[0].set_value(4_000).run()
    assert not at.exception, at.exception
    assert _sha(book) == before, "the plan wrote to the book"

    said = " ".join(str(e.value) for kind in (at.caption, at.markdown, at.info, at.warning)
                    for e in kind)
    # Beside the amount: why tax-advantaged accounts are never picked, cited.
    assert "Only **taxable** accounts are sold" in said
    assert "generally owes a 10% additional tax (IRS Publications 590-B and 575" in said
    assert "Roth withdrawal of contributions is tax-free (Publication 590-B, 2025)" in said
    assert "gone for good once 60 days pass" in said
    assert "nothing is traded or written" in said

    picks = _frame(at, "Est. realized gain/loss ($)")
    assert list(picks["Ticker"]) == ["SPAXX", "VGIT", "JEPI"]
    assert list(picks["Account"]) == ["Individual Taxable (Self-Directed)"] * 2 + [
        "Individual Taxable (TOD)"]
    assert list(picks["Why"]) == ["Cash and money market", "At a loss",
                                  "Lowest gain as a share of cost (part)"]
    mets = {m.label: m.value for m in at.metric}
    assert mets["Total raised"] == "$4,000.00"
    assert mets["Est. realized gain/loss"] == "-$190.00"     # -200 + 1000/3000 * 30
    assert f"Positions as of {PAGE_AS_OF} (Portfolio_Positions_Nov-02-2026.csv)" in said

    skipped = _frame(at, "Also held in")
    assert list(skipped["Ticker"]) == ["IEMG"] and list(skipped["Also held in"]) == ["Roth IRA"]
    assert "(IRS Publication 550 (2025), Wash Sales)" in said
    assert "Dividends Used To Buy More Stock" in said

    alloc = _frame(at, "After the plan")
    assert list(alloc["Class"]) == ["US stocks", "International stocks",
                                    "Themes and single stocks", "Hedged equity", "Bonds",
                                    "Real assets", "Alternatives", "Cash"]
    assert "Bank accounts outside the positions file are left out" in said
    assert "looked through with their fund_compositions rows: GAOSX, RFUTX" in said

    # The rates, each with its table and date, and the ladder's note naming the places.
    # A $ in the profile's text is escaped, or markdown reads "$50,000 ... $" as LaTeX.
    assert ("Synthetica State 6% (Synthetica Revenue Dept., rate table T-1, 2026 edition); "
            "Synthetica City 4% ordinary and 3% on gains (Synthetica City Finance, rate "
            "table C-1: over \\$50,000, 2026 edition)") in said
    ladder = _frame(at, "Cumulative net cash ($)")
    notes = set(ladder["Why"])
    assert any("Synthetica State and Synthetica City" in n for n in notes), notes
    assert not any(re.search(r"\bPA\b", n) for n in notes)


def test_with_no_residence_the_page_says_no_state_tax_is_included(
        monkeypatch, tmp_path, bootstrapped_book):
    empty = {"jurisdictions": [], "source": "absent",
             "problem": "private/personal_profile.json has no tax_residence"}
    at, _book = _personal_page(monkeypatch, bootstrapped_book, tmp_path, empty)
    at.run()
    assert not at.exception, at.exception
    said = " ".join(str(c.value) for c in at.caption)
    assert ("No state or local tax is included: private/personal_profile.json has no "
            "tax_residence.") in said
    assert "Enter an amount to see which holdings the plan sells." in said


def test_an_amount_beyond_the_taxable_holdings_says_how_short_it_falls(
        monkeypatch, tmp_path, bootstrapped_book):
    at, _book = _personal_page(monkeypatch, bootstrapped_book, tmp_path, SYNTHETIC_RESIDENCE)
    at.run()
    at.number_input[0].set_value(100_000).run()
    assert not at.exception, at.exception
    picks = _frame(at, "Est. realized gain/loss ($)")
    assert set(picks["Account"]) <= {"Individual Taxable (Self-Directed)",
                                     "Individual Taxable (TOD)"}
    warned = " ".join(str(w.value) for w in at.warning)
    # Taxable sellable: SPAXX 1,000 + VGIT 2,000 + JEPI 3,000 + GAOSX 10,000 + QQQ 2,000.
    assert "raise \\$18,000.00, \\$82,000.00 short of \\$100,000.00" in warned
    assert "never sells from tax-advantaged accounts" in warned
