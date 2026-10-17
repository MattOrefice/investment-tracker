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


# ── #627 (decided by the owner on October 16, 2026) ───────────────────────────

def test_the_operational_bullet_keeps_only_custody_risk_and_the_cited_grantor_trust(bullet):
    _, text = bullet
    assert text.startswith("Operational complexity: self-custody introduces key management "
                           "risk and exchange-held BTC introduces counterparty risk (FTX "
                           "2022). IBIT's sponsor treats the trust as a grantor trust")
    for gone in ("2024 launch of spot Bitcoin ETFs", "materially reduces",
                 "standard brokerage account", "mirror commodity ETF wrappers",
                 "expense drag", "tax classification"):
        assert gone not in text, gone


def test_the_allocator_side_operational_bullet_keeps_custodial_complexity_alone(bullet):
    page, _ = bullet
    assert "**Operational:** custodial complexity\n" in page
    for gone in ("ETF alternatives simplify ops", "fee drag and tracking risk"):
        assert gone not in page, gone


def test_the_pages_second_trigger_names_its_own_curves_range(bullet):
    import re
    page, _ = bullet
    assert re.search(r"An allocation of up to \d+% improves the portfolio's Sharpe ratio "
                     r"after explicit tax-drag adjustment \(the analytical gap in the "
                     r"constrained mean-variance analysis\)\.", page), "the trigger's range"
    assert "5–10%" not in page


def test_the_pages_sharpe_argument_reads_the_whole_curve(bullet):
    """#629: the page's argument is ae.sharpe_argument's, as the PDF's is: the curve's best
    weight against its 0% point, with its window named."""
    import re
    page, _ = bullet
    assert re.search(r"Sharpe-improving against SAA target weights: the curve's highest "
                     r"Sharpe is at \d+% allocation \(\d\.\d{3} at 0% → \d\.\d{3}, "
                     r"[+-]\d\.\d{3} over the full sample, 2018 to present\)", page), (
        "the page's Sharpe argument")
    # The page's old form: "Sharpe-improving against SAA target weights at 10% allocation
    # (0.496 → 0.582, +0.086 over the 2018-present sample)".
    assert "target weights at 10% allocation" not in page and "-present sample" not in page
