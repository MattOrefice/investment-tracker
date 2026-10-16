"""The Factor Profile's residual-reading note and its controls note cite each
classification and universe claim to its owner's own dated document, and the typed Canada
weight is gone (#623, decided by the owner on October 16, 2026, by #608's and #614's rule).

The reading-order note said South Korea is developed to S&P and to FTSE but emerging to Ken
French, and that Canada is in the factor universe but out of MSCI EAFE, citing nothing. The
controls box said the tilt funds hold Canada "at ~11-13%" and that IQLT, IVLU and ISVL each
match the factor universe on both counts, citing nothing. Each claim now names its source:
  * S&P DJI's July 14, 2026 consultation, FTSE's classification as at April 7, 2026 and
    French's emerging-markets page, as the IDHQ note and the Developed sleeve's note cite them;
  * French's developed-markets country table for Canada in the factors and Korea out;
  * MSCI's factsheets as of September 30, 2026: EAFE "excluding the US and Canada", and the
    22 DM countries of IQLT's and IVLU's indexes, Canada among them and Korea not;
  * FTSE's Focused Value ground rules (v2.4, August 2026) for ISVL's universe, the FTSE
    Developed ex US ex Korea Small Cap Index, with FTSE's classification for Canada.
No fund factsheet giving the tilt funds' Canada weight was found, so the weight went, and the
claim that the tilt funds hold Canada went with it.
"""
from __future__ import annotations

import re

import pytest

from src import factors

_DATE = re.compile(r"(January|February|March|April|May|June|July|August|September|October|"
                   r"November|December)( \d{1,2},)? 20\d\d")

READING_ORDER = [
    ("South Korea is developed to S&P", "_SP_CLASSIFICATION"),
    ("and to FTSE, which provides VEA's index", "_FTSE_CLASSIFICATION"),
    ("but emerging to Ken French", "_FRENCH_EMERGING_LIST"),
    ("Canada is in the factor universe", "_FRENCH_COUNTRY_TABLE"),
    ("but out of MSCI EAFE", "_MSCI_EAFE_FACTSHEET"),
]
CONTROLS = [
    ("include Canada and leave Korea out", "_FRENCH_COUNTRY_TABLE"),
    ("MSCI EAFE (EFV, SCZ) excludes Canada", "_MSCI_EAFE_FACTSHEET"),
    ("Canada among them and Korea not", "_MSCI_CONTROLS_FACTSHEETS"),
    ("FTSE Developed ex US ex Korea Small Cap Index", "_FTSE_FOCUSED_VALUE_RULES"),
    ("FTSE classes Canada as Developed", "_FTSE_CLASSIFICATION"),
]


@pytest.mark.parametrize("name", sorted({s for _, s in READING_ORDER + CONTROLS}))
def test_each_source_names_its_date(name):
    assert _DATE.search(getattr(factors, name)), name


@pytest.mark.parametrize("claim, source", READING_ORDER)
def test_each_reading_order_claim_is_followed_by_its_source(claim, source):
    text = factors.intl_residual_reading_order()
    assert f"{claim} ({getattr(factors, source)})" in text, claim


@pytest.mark.parametrize("claim, source", CONTROLS)
def test_each_controls_claim_is_followed_by_its_source(claim, source):
    text = factors.intl_controls_note()
    assert f"{claim} ({getattr(factors, source)})" in text, claim


def test_the_typed_canada_weight_and_the_holding_claim_are_gone():
    text = factors.intl_controls_note()
    for gone in ("11–13", "11-13", "tilt funds hold", "in the tilt funds",
                 "the tilt funds do not share", "on both counts (Canada in, Korea out)"):
        assert gone not in text, gone
    assert "%" not in text, "a weight is typed in the controls note"
