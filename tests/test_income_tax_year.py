"""The profile's ordinary income carries its tax year, and the 0% headroom uses it only
for that year (#734, decided by the owner on November 19, 2026).

Asset Location sizes the 0% long-term gains budget as (ceiling - income), the ceiling for
the tax year the federal profile names (#728). The income named no year, so one year's
ceiling could meet another year's income and no label could say which. Now
``ordinary_income`` is {tax_year, amount}. A bare number is refused, and an income for
another year than the rates' is not used: the budget is not computed, and the page says
which year each is for. Synthetic profiles only.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

import src.personal_profile as pp
from src.location_actions import capital_gains_headroom

_EMPTY = pd.DataFrame(columns=["embedded_gain"])


def _profile(monkeypatch, tmp_path, income) -> None:
    p = tmp_path / "personal_profile.json"
    p.write_text(json.dumps({"ordinary_income": income}), encoding="utf-8")
    monkeypatch.setattr(pp, "_PROFILE_PATH", p)


def test_an_income_is_read_with_its_tax_year(monkeypatch, tmp_path):
    _profile(monkeypatch, tmp_path, {"tax_year": 2031, "amount": 40_000})
    assert pp.load_income_profile(is_demo=False) == {
        "ordinary_income": 40_000.0, "tax_year": 2031,
        "source": "private/personal_profile.json", "problem": None}


def test_a_bare_number_names_no_tax_year_and_is_refused(monkeypatch, tmp_path):
    _profile(monkeypatch, tmp_path, 40_000)
    prof = pp.load_income_profile(is_demo=False)
    assert prof["ordinary_income"] is None and prof["tax_year"] is None
    assert prof["problem"] == "ordinary_income names no tax year"


def test_the_demo_income_is_for_the_demo_rates_year():
    prof = pp.load_income_profile(is_demo=True)
    assert prof["tax_year"] == pp.load_federal_tax(is_demo=True)["tax_year"]
    assert prof["ordinary_income"] == pp.DEMO_ORDINARY_INCOME and prof["problem"] is None


def test_an_income_for_another_year_than_the_rates_is_not_used(monkeypatch, tmp_path):
    """2026's income does not size 2027's budget: it is unknown for 2027, with both years
    named, and the budget collapses to zero rather than mixing the two."""
    _profile(monkeypatch, tmp_path, {"tax_year": 2026, "amount": 10_000})
    hr = capital_gains_headroom(_EMPTY, is_demo=False, tax_year=2027)
    assert hr["income_known"] is False and hr["ordinary_income"] is None
    assert hr["total"] == 0.0
    assert hr["income_problem"] == ("ordinary_income is for tax year 2026, the rates for "
                                    "tax year 2027")


@pytest.mark.parametrize("year", [2026, 2027])
def test_an_income_for_the_rates_year_sizes_its_budget(monkeypatch, tmp_path, year):
    """The same income, for the rates' own year, sizes that year's budget from that
    year's ceiling."""
    from src.location_config import ltcg_zero_rate_ceiling
    _profile(monkeypatch, tmp_path, {"tax_year": year, "amount": 10_000})
    hr = capital_gains_headroom(_EMPTY, is_demo=False, tax_year=year)
    assert hr["income_known"] is True and hr["income_problem"] is None
    assert hr["total"] == pytest.approx(ltcg_zero_rate_ceiling(year)["amount"] - 10_000)


def test_each_reason_reaches_the_headroom(monkeypatch, tmp_path):
    """The page's not-computed sentence reads income_problem: a profile with no income,
    one that names no year, and an income passed in as unknown each say so."""
    monkeypatch.setattr(pp, "_PROFILE_PATH", tmp_path / "absent.json")
    assert capital_gains_headroom(_EMPTY, is_demo=False, tax_year=2027)[
        "income_problem"] == "private/personal_profile.json does not exist"
    _profile(monkeypatch, tmp_path, 40_000)
    assert capital_gains_headroom(_EMPTY, is_demo=False, tax_year=2027)[
        "income_problem"] == "ordinary_income names no tax year"
    assert capital_gains_headroom(_EMPTY, ordinary_income=None, is_demo=False,
                                  tax_year=2027)["income_problem"] == (
        "no ordinary income was given")


def test_the_page_says_why_the_headroom_is_not_computed():
    """Page 14's not-computed paragraph reads income_problem rather than typing one
    reason."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "pages" / "14_Asset_Location.py"
           ).read_text(encoding="utf-8")
    assert "_hr['income_problem']" in src
    assert "`ordinary_income` is set in" not in src
