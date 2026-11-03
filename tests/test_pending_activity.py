"""Fidelity's "Pending activity" line is cash in its account (#705, owner's decision,
November 3, 2026).

Every export and position here is SYNTHETIC. What is pinned, for a positive, a negative
and a zero line, and for one that takes an account's cash below zero:

  * the parser keeps it, under one label, with no quantity, cost basis or gain, so every
    account sums to the export's own total of current value;
  * every allocation counts it as Cash, with no securities row, and no reader takes it
    for a security: not the startup mapping check, the share reconciliation, the top
    holdings, the issuers or the embedded gain;
  * on the liquidity ladder it nets against its account's cash first, then the account's
    cheapest rungs: no rung or tier is negative, the running total never falls, and the
    rungs sum to the export's total;
  * in the sell plan it nets the same way; a gap below zero is covered first by sales in
    that account, the plan raises the amount plus the gap and the page says so in one
    line; the allocation after the plan settles the line.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from tests.test_sell_plan import (  # noqa: F401  (bootstrapped_book is a fixture)
    ACCOUNTS, NAN, SECURITIES, SYNTHETIC_RESIDENCE, _frame, _personal_page, _pos,
    bootstrapped_book,
)

PENDING = "Pending activity"


# ── The parser ───────────────────────────────────────────────────────────────────

_HEADER = ("Account Number,Account Name,Symbol,Description,Quantity,Last Price,"
           "Current Value,Total Gain/Loss Dollar,Cost Basis Total,Type\n")


def _export(tmp_path, rows, accounts=("X1", "X2", "X3")) -> tuple[Path, Path]:
    csv_path = tmp_path / "Portfolio_Positions_Nov-02-2026.csv"
    csv_path.write_text(_HEADER + "".join(r + "\n" for r in rows)
                        + "\n\"Footer paragraph, ignored.\"\n", encoding="utf-8")
    amap = tmp_path / "account_map.json"
    amap.write_text(json.dumps({a: f"p_{a.lower()}" for a in accounts}), encoding="utf-8")
    return csv_path, amap


EXPORT_ROWS = [
    # X1: cash, a fund, and a NEGATIVE pending line deeper than the cash.
    "X1,One,MMF**,MONEY MARKET,40,$1.00,$40.00,,,Cash",
    "X1,One,FUNDA,FUND A,10,$50.00,$500.00,+$100.00,$400.00,Cash",
    "X1,One,Pending activity,,,,-$90.00,,,",
    # X2: a POSITIVE line.
    "X2,Two,FUNDB,FUND B,5,$20.00,$100.00,-$10.00,$110.00,Cash",
    "X2,Two,Pending Activity,,,,$25.00,,,",
    # X3: a ZERO line.
    "X3,Three,FUNDC,FUND C,1,$75.00,$75.00,+$5.00,$70.00,Cash",
    "X3,Three,pending  activity,,,,$0.00,,,",
]


def test_the_parser_keeps_the_line_as_cash_with_no_quantity_basis_or_gain(tmp_path):
    from src.ingestion.fidelity import (
        PENDING_ACTIVITY_LABEL, is_pending_activity, parse_fidelity_csv)
    csv_path, amap = _export(tmp_path, EXPORT_ROWS)
    df = parse_fidelity_csv(str(csv_path), account_map_path=amap)
    pend = df[df["symbol"].map(is_pending_activity)]
    assert len(pend) == 3, "every spelling of the label is the pending line"
    assert set(pend["symbol"]) == {PENDING_ACTIVITY_LABEL}
    assert pend[["quantity", "cost_basis_total", "total_gain_loss"]].isna().all().all()
    by = dict(zip(pend["pseudonym"], pend["current_value"]))
    assert by == {"p_x1": -90.0, "p_x2": 25.0, "p_x3": 0.0}
    # Each account's total is the export's own sum of current value.
    totals = df.groupby("pseudonym")["current_value"].sum().round(2).to_dict()
    assert totals == {"p_x1": 450.0, "p_x2": 125.0, "p_x3": 75.0}


def test_a_ticker_is_never_taken_for_the_line():
    from src.ingestion.fidelity import is_pending_activity
    assert is_pending_activity("Pending activity")
    assert is_pending_activity(" PENDING  ACTIVITY ")
    for not_it in ("PENDING", "activity", "SPAXX", "", None, float("nan")):
        assert not is_pending_activity(not_it), not_it


# ── Household readers ────────────────────────────────────────────────────────────

HH_ACCOUNTS = pd.DataFrame([
    {"pseudonym": "tax_a", "display_name": "Taxable A", "managed_by": "self",
     "tax_treatment": "taxable", "included_in_household": 1},
    {"pseudonym": "roth", "display_name": "Roth IRA", "managed_by": "self",
     "tax_treatment": "roth_ira", "included_in_household": 1},
])
HH_SECURITIES = pd.DataFrame([
    {"ticker": "MMF", "name": "Money Fund", "sleeve_category": "cash",
     "tax_efficiency": "low", "is_in_saa": 1, "asset_class_id": 9},
    {"ticker": "US1", "name": "US Fund (ISHARES)", "sleeve_category": "us_large_core",
     "tax_efficiency": "high", "is_in_saa": 1, "asset_class_id": 5},
])
HH_TARGETS = pd.DataFrame([
    {"asset_class_id": 5, "name": "US Core", "target_weight": 1.0},
    {"asset_class_id": 9, "name": "Cash / MMF", "target_weight": 0.0},
])
EMPTY_COMP = pd.DataFrame(columns=["fund_symbol", "underlying_sleeve", "weight"])


def _hh(pending_value) -> pd.DataFrame:
    return pd.DataFrame([
        _pos("tax_a", "US1", 1_000.0, 800.0), _pos("tax_a", "MMF", 50.0, quantity=NAN),
        _pos("roth", "US1", 500.0, 400.0),
        _pos("tax_a", PENDING, pending_value, quantity=NAN),
    ])


def test_look_through_counts_the_line_as_cash_with_no_securities_row():
    from src.household import look_through_position
    part = look_through_position(PENDING, -12.5, EMPTY_COMP, HH_SECURITIES.iloc[0:0])
    assert part.to_dict("records") == [{"sleeve": "cash", "dollar_value": -12.5}]


@pytest.mark.parametrize("pending_value", [30.0, -80.0, 0.0])
@pytest.mark.parametrize("mode", ["look_through", "as_held"])
def test_every_allocation_counts_the_line_as_cash(pending_value, mode):
    from src.household import compute_household_allocation
    alloc = compute_household_allocation(_hh(pending_value), HH_ACCOUNTS, HH_SECURITIES,
                                         EMPTY_COMP, HH_TARGETS, mode=mode)
    by = alloc.set_index("sleeve")["dollar_value"].to_dict()
    assert by["Cash / MMF"] == pytest.approx(50.0 + pending_value)
    assert by["US Core"] == pytest.approx(1_500.0)
    assert "unknown" not in by and PENDING not in by


@pytest.mark.parametrize("pending_value", [30.0, -80.0, 0.0])
def test_each_accounts_total_is_the_exports_and_its_line_is_cash(pending_value):
    from src.household import build_account_breakdown, compute_sleeve_by_account
    pos = _hh(pending_value)
    sba = compute_sleeve_by_account(pos, HH_ACCOUNTS, HH_SECURITIES)
    cash = sba[(sba["pseudonym"] == "tax_a") & (sba["sleeve_category"] == "cash")]
    assert float(cash["current_value"].sum()) == pytest.approx(50.0 + pending_value)
    assert "unknown" not in set(sba["sleeve_category"])
    bd = build_account_breakdown(pos, HH_ACCOUNTS, HH_SECURITIES)
    assert dict(zip(bd["Account"], bd["Total AUM ($)"])) == pytest.approx(
        {"Taxable A": 1_050.0 + pending_value, "Roth IRA": 500.0})


def test_no_holdings_list_issuer_or_gain_takes_the_line_for_a_security():
    from src.household import (
        build_issuer_concentration, build_top_holdings, compute_embedded_gain)
    pos = _hh(-80.0)
    top = build_top_holdings(pos, HH_ACCOUNTS, HH_SECURITIES, n=10)
    assert PENDING not in set(top["Symbol"])
    # The household total, the share's denominator, still holds the line.
    us = top[(top["Symbol"] == "US1") & (top["Account"] == "Taxable A")]
    assert float(us["% of Household"].iloc[0]) == pytest.approx(round(1_000 / 1_470 * 100, 1))
    issuers = build_issuer_concentration(pos, n=10)
    assert float(issuers["$ Total"].sum()) == pytest.approx(1_550.0, abs=1)
    eg, _ = compute_embedded_gain(pos)
    assert PENDING not in set(eg["symbol"])


def test_the_startup_mapping_check_does_not_report_the_line(tmp_path, monkeypatch):
    from src.bootstrap import unmapped_holdings
    csv_path, amap = _export(tmp_path, EXPORT_ROWS)
    db = tmp_path / "book.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE securities (ticker TEXT, sleeve_category TEXT, "
                     "tax_efficiency TEXT)")
        conn.executemany("INSERT INTO securities VALUES (?, ?, ?)", [
            ("MMF", "cash", "low"), ("FUNDA", "us_large_core", "high"),
            ("FUNDB", "us_large_core", "high"), ("FUNDC", "us_large_core", "high")])
    check = unmapped_holdings(db, uploads_dir=tmp_path, account_map_path=amap)
    assert check.disposition == "checked"
    assert check.findings == {}, "the pending line is cash, not an unmapped ticker"


def test_the_share_reconciliation_compares_no_pending_line(monkeypatch):
    """The ledger can never hold the line; the export's cash includes it."""
    import src.reconciliation as rec
    monkeypatch.setattr(rec, "get_holdings_on_date", lambda as_of, account_id:
                        pd.DataFrame({"net_shares": [10.0]}, index=["FUNDA"]))
    pos = pd.DataFrame([
        {"pseudonym": "acct_01", "symbol": "FUNDA", "quantity": 10.0},
        {"pseudonym": "acct_01", "symbol": "SPAXX", "quantity": 40.0},
        {"pseudonym": "acct_01", "symbol": PENDING, "quantity": 5.0},   # never compared
    ])
    out = rec.reconcile_account_shares(1, pos, "acct_01", as_of="2026-11-02")
    assert out.empty, out


# ── The ladder ───────────────────────────────────────────────────────────────────

FLAT = {"federal_marginal": 0.22, "federal_ltcg": 0.15, "state_marginal": 0.0,
        "state_ltcg": 0.0}
LADDER_SEC = None


def _ladder_book(pending_value) -> pd.DataFrame:
    return pd.DataFrame([
        _pos("tax_a", "MMF", 40.0, quantity=NAN),
        _pos("tax_a", "L_DEEP", 500.0, 600.0),       # a loss: Tier 1, cost 0
        _pos("tax_a", "G_HIGHSHARE", 300.0, 100.0),  # +200%: Tier 2
        _pos("trad", "TRADBIG", 1_000.0, 900.0),
        _pos("tax_a", PENDING, pending_value, quantity=NAN),
    ])


def _ladder(pending_value):
    from src.liquidity import build_liquidity_ladder
    return build_liquidity_ladder(_ladder_book(pending_value), ACCOUNTS, FLAT)


def _ladder_holds(lad, export_total):
    assert (lad["value"] >= 0).all() and (lad["net_cash"] >= 0).all(), lad
    assert (lad.groupby("tier")["value"].sum() >= 0).all()
    assert lad["cumulative_net_cash"].is_monotonic_increasing, lad
    assert float(lad["value"].sum()) == pytest.approx(export_total)


def test_a_positive_line_is_a_cash_rung_of_its_own():
    lad = _ladder(25.0)
    row = lad[lad["symbol"] == PENDING]
    assert len(row) == 1 and int(row["tier"].iloc[0]) == 1
    assert float(row["net_cash"].iloc[0]) == pytest.approx(25.0)
    assert (lad["pending_netted"] == 0).all()
    _ladder_holds(lad, 1_865.0)


def test_a_zero_line_changes_nothing():
    assert _ladder(0.0).drop(columns="note").equals(
        _ladder_book_without_pending().drop(columns="note"))


def _ladder_book_without_pending():
    from src.liquidity import build_liquidity_ladder
    book = _ladder_book(0.0)
    return build_liquidity_ladder(book[book["symbol"] != PENDING], ACCOUNTS, FLAT)


def test_a_negative_line_inside_the_cash_comes_off_the_cash_rung():
    lad = _ladder(-30.0)
    mmf = lad[lad["symbol"] == "MMF"].iloc[0]
    assert mmf["value"] == pytest.approx(10.0) and mmf["pending_netted"] == pytest.approx(30.0)
    assert float(lad["pending_netted"].sum()) == pytest.approx(30.0)
    assert PENDING not in set(lad["symbol"])
    _ladder_holds(lad, 1_810.0)


def test_a_line_below_the_cash_comes_off_the_accounts_cheapest_rungs_next():
    lad = _ladder(-100.0)
    by = lad.set_index("symbol")
    # 40 off the cash, then 60 off the cheapest of the account's other rungs.
    assert by.loc["MMF", "pending_netted"] == pytest.approx(40.0)
    assert by.loc["MMF", "value"] == pytest.approx(0.0)
    assert by.loc["L_DEEP", "pending_netted"] == pytest.approx(60.0)
    assert by.loc["L_DEEP", "value"] == pytest.approx(440.0)
    assert by.loc["G_HIGHSHARE", "pending_netted"] == 0.0
    assert by.loc["TRADBIG", "pending_netted"] == 0.0, "another account's rungs are not touched"
    assert "covers this account's pending activity" in by.loc["L_DEEP", "note"]
    assert lad.attrs["pending_uncovered"] == {}
    _ladder_holds(lad, 1_740.0)


def test_a_line_no_rung_can_cover_is_reported_not_made_negative():
    lad = _ladder(-1_000.0)
    taxable = lad[lad["account"] == "Taxable A"]
    assert (taxable["net_cash"] == 0).all()
    # Every taxable rung gives up its net cash: 40 + 500 + (300 - its 30 tax) = 810.
    covered = float(taxable["pending_netted"].sum())
    assert covered == pytest.approx(810.0)
    assert lad.attrs["pending_uncovered"] == {"Taxable A": pytest.approx(190.0)}
    # No rung goes below zero to absorb the rest, so the rungs sum to the holdings less
    # what was covered, not to the account's negative total.
    _ladder_holds(lad, 1_840.0 - covered)


# ── The sell plan ────────────────────────────────────────────────────────────────

def _sp():
    import src.sell_plan as sp
    return sp


def _plan_book(pending_value) -> pd.DataFrame:
    return pd.DataFrame([
        _pos("tax_a", "MMF", 40.0, quantity=40.0),
        _pos("tax_b", "CASH2", 100.0, quantity=100.0),
        _pos("tax_b", "L_DEEP", 800.0, 1_000.0),        # -20%: sold before tax_a's
        _pos("tax_a", "L_SHALLOW", 950.0, 1_000.0),     # -5%
        _pos("tax_a", "G_LOWSHARE", 2_040.0, 2_000.0),  # +2%
        _pos("tax_a", PENDING, pending_value, quantity=NAN),
    ])


def _no_negative_sale(plan):
    assert (plan.picks["value"] > 0).all(), plan.picks
    assert (plan.picks["shares"].dropna() > 0).all(), plan.picks


def test_a_positive_line_is_cash_the_plan_uses():
    sp = _sp()
    plan = sp.build_sell_plan(150.0, _plan_book(25.0), ACCOUNTS, SECURITIES)
    assert list(plan.picks["symbol"]) == ["CASH2", "MMF", PENDING]
    row = plan.picks.iloc[2]
    assert row["group"] == sp.GROUP_PENDING and row["value"] == pytest.approx(10.0)
    assert pd.isna(row["cost_basis"]) and row["realized_gain"] == 0.0 and pd.isna(row["shares"])
    assert plan.target == pytest.approx(150.0) and plan.pending_gaps.empty
    _no_negative_sale(plan)


def test_a_zero_line_plans_as_if_it_were_absent():
    sp = _sp()
    book = _plan_book(0.0)
    with_zero = sp.build_sell_plan(1_000.0, book, ACCOUNTS, SECURITIES)
    without = sp.build_sell_plan(1_000.0, book[book["symbol"] != PENDING], ACCOUNTS, SECURITIES)
    assert with_zero.picks.equals(without.picks)
    assert with_zero.target == without.target == pytest.approx(1_000.0)


def test_a_negative_line_inside_the_cash_lowers_the_cash_the_plan_can_use():
    sp = _sp()
    plan = sp.build_sell_plan(130.0, _plan_book(-30.0), ACCOUNTS, SECURITIES)
    by = plan.picks.set_index("symbol")
    assert by.loc["CASH2", "value"] == pytest.approx(100.0)
    # MMF's 40, less the 30 already spoken for: 10 to use, and part of the position.
    assert by.loc["MMF", "value"] == pytest.approx(10.0) and bool(by.loc["MMF", "partial"])
    assert by.loc["MMF", "shares"] == pytest.approx(10.0)
    assert by.loc["L_DEEP", "value"] == pytest.approx(20.0)
    assert plan.target == pytest.approx(130.0) and plan.pending_gaps.empty
    assert plan.raised == pytest.approx(130.0)
    _no_negative_sale(plan)


def test_a_line_below_the_cash_is_covered_first_by_that_accounts_sales():
    """tax_a's line takes its cash 60 below zero. tax_b's loss comes first in the plan's
    order, so without the rule the amount would be met in tax_b alone. With it, tax_a's
    own first sale covers the gap, and the plan raises the amount plus the gap."""
    sp = _sp()
    plan = sp.build_sell_plan(500.0, _plan_book(-100.0), ACCOUNTS, SECURITIES)
    gaps = plan.pending_gaps
    assert gaps.to_dict("records") == [{
        "account": "Taxable A", "pseudonym": "tax_a", "pending": -100.0, "cash": 40.0,
        "gap": 60.0, "covered": 60.0}]
    assert plan.target == pytest.approx(560.0) and plan.raised == pytest.approx(560.0)
    assert plan.shortfall == 0.0
    by = plan.picks.set_index("symbol")
    assert "MMF" not in by.index, "the line took all of tax_a's cash"
    assert by.loc["L_SHALLOW", "pending_covered"] == pytest.approx(60.0)
    assert by.loc["L_SHALLOW", "value"] == pytest.approx(60.0)
    assert by.loc["CASH2", "value"] == pytest.approx(100.0)
    assert by.loc["L_DEEP", "value"] == pytest.approx(400.0)
    assert float(plan.picks["pending_covered"].sum()) == pytest.approx(60.0)
    _no_negative_sale(plan)


def test_the_allocation_after_the_plan_settles_the_line():
    sp = _sp()
    book = _plan_book(-100.0)
    plan = sp.build_sell_plan(500.0, book, ACCOUNTS, SECURITIES)
    sales = sp.plan_sales(plan)
    settle = sales[sales["symbol"] == PENDING]
    assert settle["value"].tolist() == [pytest.approx(-60.0)]
    t = sp.allocation_table(book, SECURITIES, EMPTY_COMP, {}, sales).set_index("broad_class")
    before_total, after_total = t["before_value"].sum(), t["after_value"].sum()
    assert before_total == pytest.approx(3_830.0)
    # 560 raised; 60 of it settles the line and stays in the household's books as the
    # line's cash going to zero, so 500 leaves.
    assert after_total == pytest.approx(3_330.0)
    # Cash after: MMF 40 + line -100 + 60 settled, CASH2 sold: 0.
    assert t.loc[sp.CASH, "after_value"] == pytest.approx(0.0)


# ── The page ─────────────────────────────────────────────────────────────────────

def test_the_page_says_in_one_line_that_the_plan_raises_the_gap_too(
        monkeypatch, tmp_path, bootstrapped_book):
    from datetime import date

    import src.household_data as household_data
    import tests.test_sell_plan as tsp

    positions = pd.concat([tsp.PAGE_POSITIONS, pd.DataFrame([
        _pos("acct_taxable_02", PENDING, -250.0, quantity=NAN)])], ignore_index=True)
    at, _book = _personal_page(monkeypatch, bootstrapped_book, tmp_path, SYNTHETIC_RESIDENCE)
    path = tmp_path / "Portfolio_Positions_Nov-02-2026.csv"
    monkeypatch.setattr(household_data, "load_latest_positions", lambda *a, **k:
                        household_data.LoadedPositions(positions.copy(), path,
                                                       date.fromisoformat(tsp.PAGE_AS_OF)))
    at.run()
    assert not at.exception, at.exception
    at.number_input[0].set_value(1_000).run()
    assert not at.exception, at.exception
    said = " ".join(str(c.value) for c in at.caption)
    assert ("Pending activity of -\\$250.00 in Individual Taxable (TOD) takes its cash "
            "\\$250.00 below zero, so the plan raises \\$1,000.00 plus that \\$250.00: the "
            "first sales in Individual Taxable (TOD) cover it.") in said
    mets = {m.label: m.value for m in at.metric}
    assert mets["Total raised"] == "$1,250.00"
    ladder = _frame(at, "Cumulative net cash ($)")
    assert (ladder["Net cash ($)"] >= 0).all()
    assert ladder["Cumulative net cash ($)"].is_monotonic_increasing
    assert "Pending activity" not in set(ladder["Symbol"])
