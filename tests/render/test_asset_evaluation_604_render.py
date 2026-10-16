"""The Asset Evaluation page's operational bullet after #604 (decided by the owner on
October 15, 2026), rendered on a copy of the demo book, offline. The PDF's 2022 note and
case-against bullet are checked in tests/test_a_fall_prints_as_a_magnitude.py and
tests/test_macro_windows_and_drawdown_verb.py.
"""
from __future__ import annotations

import pytest

from tests.render.test_typed_in_figures import _render


@pytest.fixture(scope="module")
def bullet(tmp_path_factory):
    _, text = _render("5_Asset_Evaluation.py", tmp_path_factory)
    start = text.index("Operational complexity:")
    return text, text[start:text.index("No intrinsic cash flow", start)]


def test_the_grantor_trust_claim_is_cited_with_its_date(bullet):
    _, text = bullet
    assert ("IBIT's sponsor treats the trust as a grantor trust for U.S. federal income tax "
            "purposes, so its shareholders are taxed as if they directly owned a pro rata "
            "share of its bitcoin (iShares Bitcoin Trust ETF, annual report on Form 10-K for "
            "2025, filed February 27, 2026)") in text


def test_the_uncited_fee_range_and_tax_claims_are_gone(bullet):
    _, text = bullet
    for gone in ("0.12", "0.25%", "expense ratios", "qualified-dividend", "qualified dividend",
                 "wash-sale", "wash sale", "under current IRS guidance",
                 "Spot Bitcoin ETFs are structured"):
        assert gone not in text, gone


def test_the_notice_2014_21_line_stays_once(bullet):
    page, _ = bullet
    # Since #625 the Notice carries its bulletin's date.
    assert page.count("the IRS treats bitcoin as property (Notice 2014-21, ") == 1
