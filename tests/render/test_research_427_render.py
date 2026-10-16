"""The Research page after #427 (decided by the owner on October 15, 2026), rendered on a
copy of the demo book, offline: the rationales as a visitor reads them, every token
filled. The data and the helpers are checked in tests/test_research_427.py.
"""
from __future__ import annotations

import pytest

from src.factors import _NPORT_ASOF
from tests.render.test_typed_in_figures import _render


@pytest.fixture(scope="module")
def research(tmp_path_factory):
    _, text = _render("8_Research.py", tmp_path_factory)
    return text


def test_vtv_cites_both_index_documents(research):
    assert ("The index it tracks, CRSP US Large Cap Value (renamed Morningstar US Large Cap "
            "Value in July 2026), scores value on five ratios") in research
    assert ("(Russell US Equity Indexes Construction and Methodology, v7.2, August 2026, "
            "section 8.2.1)") in research
    assert "P/CF" not in research and "P/B-heavy" not in research


def test_avdv_names_the_filing_by_its_date(research):
    assert (f"As of the Avantis N-PORT holdings disclosure for the period ending "
            f"{_NPORT_ASOF}, the filing the Factor Profile page reads, AVDV held no South "
            "Korea position") in research
    assert "verified at the position level" not in research


def test_both_er_savings_notes_say_what_the_page_computes(research):
    assert research.count("where each holding's weight is its sleeve's SAA target split "
                          "equally across the sleeve's holdings, not the portfolio's actual") == 2
    assert "sleeve-weighted to reflect actual portfolio" not in research
    assert "{{" not in research
