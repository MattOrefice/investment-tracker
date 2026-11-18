"""The sell plan leaves the tracked portfolio account out by default (owner's decision,
November 3, 2026).

The tracked portfolio account (holdings.get_portfolio_account) is the book the
performance pages measure. A sale there withdraws from it and moves its sleeves off
their targets, so the Liquidity page plans without it unless a toggle lets it in.
Everything here is SYNTHETIC. What is pinned:

  * left out, the account gives the plan nothing at any step: not its cash, its losses,
    its gains, or the partial last sale;
  * the wash-sale check still reads its holdings, so a loss elsewhere in a ticker it
    holds is still skipped and flagged;
  * let in, the plan is the plan without this rule;
  * the page names the account it leaves out and says why in one line, and the
    allocation follows the plan shown.
"""
from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from tests.test_sell_plan import (  # noqa: F401  (bootstrapped_book is a fixture)
    ACCOUNTS, NAN, SECURITIES, SYNTHETIC_RESIDENCE, _frame, _personal_page, _pos,
    bootstrapped_book,
)


def _sp():
    import src.sell_plan as sp
    return sp


def _book() -> pd.DataFrame:
    """tax_a stands in for the tracked account: it holds the plan's first pick in every
    group, so a plan that reached into it would show at each step."""
    return pd.DataFrame([
        _pos("tax_a", "MMF", 500.0, quantity=500.0),            # the largest cash
        _pos("tax_b", "CASH2", 100.0, quantity=100.0),
        _pos("tax_a", "L_DEEP", 700.0, 1_000.0),                # -30%: the deepest loss
        _pos("tax_b", "L_SHALLOW", 950.0, 1_000.0),             # -5%
        _pos("tax_a", "G_LOWSHARE", 1_010.0, 1_000.0),          # +1%: the lowest gain
        _pos("tax_b", "G_HIGHSHARE", 2_000.0, 1_000.0),         # +100%
        _pos("roth", "ROTHBIG", 5_000.0, 1_000.0),
    ])


def test_left_out_the_account_gives_the_plan_nothing_at_any_step():
    sp = _sp()
    book = _book()
    with_it = sp.build_sell_plan(2_750.0, book, ACCOUNTS, SECURITIES)
    # Without the rule, tax_a is reached in all three groups and holds the partial sale.
    by = with_it.picks.set_index("symbol")
    assert {"MMF", "L_DEEP", "G_LOWSHARE"} <= set(by.index)
    assert bool(by.loc["G_LOWSHARE", "partial"])
    assert set(with_it.picks["pseudonym"]) == {"tax_a", "tax_b"}

    left_out = sp.build_sell_plan(2_750.0, book, ACCOUNTS, SECURITIES, leave_out=("tax_a",))
    assert set(left_out.picks["pseudonym"]) == {"tax_b"}
    assert list(left_out.picks["symbol"]) == ["CASH2", "L_SHALLOW", "G_HIGHSHARE"]
    assert list(left_out.picks["group"]) == ["cash", "loss", "gain"]
    assert bool(left_out.picks.iloc[-1]["partial"]), "the partial sale lands in tax_b"
    assert left_out.raised == pytest.approx(2_750.0)


def test_left_out_its_pending_line_is_left_out_too():
    sp = _sp()
    book = pd.concat([_book(), pd.DataFrame([
        _pos("tax_a", "Pending activity", -800.0, quantity=NAN)])], ignore_index=True)
    plan = sp.build_sell_plan(500.0, book, ACCOUNTS, SECURITIES, leave_out=("tax_a",))
    assert plan.pending_gaps.empty and plan.target == pytest.approx(500.0)
    assert set(plan.picks["pseudonym"]) == {"tax_b"}


def test_the_wash_check_still_reads_the_account_it_leaves_out():
    sp = _sp()
    book = pd.DataFrame([
        _pos("tax_a", "WASHME", 1_200.0, 1_000.0),     # a gain in the left-out account
        _pos("tax_b", "WASHME", 400.0, 500.0),         # a loss elsewhere, same ticker
        _pos("tax_b", "CASH2", 100.0, quantity=100.0),
    ])
    plan = sp.build_sell_plan(1_000.0, book, ACCOUNTS, SECURITIES, leave_out=("tax_a",))
    assert list(plan.picks["symbol"]) == ["CASH2"]
    skip = plan.wash_sale_skips
    assert list(zip(skip["pseudonym"], skip["symbol"])) == [("tax_b", "WASHME")]
    assert list(skip["also_held_in"]) == ["Taxable A"]


@pytest.mark.parametrize("amount", [0.0, 700.0, 2_000.0, 4_000.0, 99_999.0])
def test_let_in_the_plan_is_the_plan_without_the_rule(amount):
    sp = _sp()
    book = _book()
    a = sp.build_sell_plan(amount, book, ACCOUNTS, SECURITIES)
    b = sp.build_sell_plan(amount, book, ACCOUNTS, SECURITIES, leave_out=())
    assert a.picks.equals(b.picks)
    assert (a.raised, a.shortfall, a.target) == (b.raised, b.shortfall, b.target)


# ── The page ─────────────────────────────────────────────────────────────────────

def _tracked(monkeypatch, book_copy):
    """Resolve the tracked account to the copy's acct_01, as a book with trades would."""
    import src.holdings as holdings
    with sqlite3.connect(book_copy) as conn:
        acct_id, name = conn.execute(
            "SELECT account_id, display_name FROM accounts WHERE pseudonym = 'acct_01'"
        ).fetchone()
    monkeypatch.setattr(holdings, "get_portfolio_account", lambda: {
        "account_id": int(acct_id), "display_name": name, "name": name})
    return name


def test_the_page_leaves_the_tracked_account_out_and_says_why(
        monkeypatch, tmp_path, bootstrapped_book):
    at, copy = _personal_page(monkeypatch, bootstrapped_book, tmp_path, SYNTHETIC_RESIDENCE)
    name = _tracked(monkeypatch, copy)
    at.run()
    assert not at.exception, at.exception
    at.number_input[0].set_value(4_000).run()
    assert not at.exception, at.exception
    said = " ".join(str(c.value) for c in at.caption)
    assert (f"Left out: **{name}**. A sale there is a withdrawal from the book the "
            "performance pages measure, and it moves that book's sleeves off their "
            "targets.") in said
    picks = _frame(at, "Est. realized gain/loss ($)")
    assert name not in set(picks["Account"])
    # The TOD account's lowest gains first: JEPI whole, then GAOSX in part.
    assert list(picks["Ticker"]) == ["JEPI", "GAOSX"]
    # IEMG's loss sits in the left-out account, so there is no loss to skip.
    assert not [d for d in at.dataframe if "Also held in" in list(d.value.columns)]
    # The allocation follows the plan shown: 4,000 leave the household.
    alloc = _frame(at, "After the plan")
    assert alloc["Before"].sum() == pytest.approx(100.0)

    # Let in, the plan is the one without the rule.
    at.toggle[0].set_value(True).run()
    assert not at.exception, at.exception
    said = " ".join(str(c.value) for c in at.caption)
    assert "Left out:" not in said
    picks = _frame(at, "Est. realized gain/loss ($)")
    assert list(picks["Ticker"]) == ["SPAXX", "VGIT", "JEPI"]
    assert list(_frame(at, "Also held in")["Ticker"]) == ["IEMG"]


def test_with_no_tracked_account_the_page_says_it_leaves_none_out(
        monkeypatch, tmp_path, bootstrapped_book):
    """The bootstrapped book has no trades, so no tracked account resolves."""
    at, _copy = _personal_page(monkeypatch, bootstrapped_book, tmp_path, SYNTHETIC_RESIDENCE)
    at.run()
    assert not at.exception, at.exception
    said = " ".join(str(c.value) for c in at.caption)
    assert "No tracked portfolio account resolves, so the plan leaves none out." in said
    assert len(at.toggle) == 0
