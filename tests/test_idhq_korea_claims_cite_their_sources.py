"""The Factor Profile's IDHQ note cites each South Korea classification claim to its
owner's own dated document, and the Korea weights it typed are gone (#614, decided by the
owner on October 16, 2026).

The note said S&P's Developed ex-US quality universe classifies South Korea as developed
and holds it, that French's factors and the IQLT control classify Korea as emerging, and
that the S&P screen's Korea weight stood near 9.5% in the first half of 2026 and roughly
2% after the June reconstitution. None of it named a source. Each classification now
names the document it was read from, with its date:
  * S&P: its Quality Indices methodology (March 2025) for the index's universe, and
    Appendix B of S&P DJI's 2026 country classification consultation (July 14, 2026)
    for South Korea among its Developed markets;
  * French: his emerging-markets description, which lists South Korea, and his
    developed-markets country table, which does not;
  * MSCI: its Emerging Markets Index factsheet, which lists Korea among its EM
    countries, and the factsheet for IQLT's index, whose DM countries include Canada
    and not Korea, both as of September 30, 2026.
No S&P document for the weights was found, so they went. So did "holds it": the
methodology takes the top quintile by quality score, with no rule per country, so it
makes Korean stocks eligible and holds none by rule. Research's pointer to the note
stays, since the classifications it points to stay.
"""
from __future__ import annotations

import re

import pytest

from src import factors

_DATE = re.compile(r"(January|February|March|April|May|June|July|August|September|October|"
                   r"November|December)( \d{1,2},)? 20\d\d")
SOURCES = ("_SP_QUALITY_METHODOLOGY", "_SP_CLASSIFICATION", "_FRENCH_EMERGING_LIST",
           "_FRENCH_COUNTRY_TABLE", "_MSCI_EM_FACTSHEET", "_MSCI_CONTROL_FACTSHEET")


def _idhq_note() -> str:
    entry = next(s for s in factors._INTL_TILT_SLEEVES if s["fund"] == "IDHQ")
    return " ".join(factors.build_intl_tilt_disclosure(
        {**entry, "fund_result": None, "control_result": None}))


@pytest.mark.parametrize("name", SOURCES)
def test_each_source_is_named_with_its_date_and_cited_in_the_note(name):
    source = getattr(factors, name)
    assert _DATE.search(source), (name, source)
    assert source in _idhq_note(), name


@pytest.mark.parametrize("claim, source", [
    ("S&P classifies South Korea as **developed**", "_SP_CLASSIFICATION"),
    ("selects from the S&P Developed Ex-U.S. LargeMidCap Index", "_SP_QUALITY_METHODOLOGY"),
    ("Ken French counts South Korea among the emerging markets", "_FRENCH_EMERGING_LIST"),
    ("leaves it out of his Developed ex-US factors", "_FRENCH_COUNTRY_TABLE"),
    ("MSCI classifies Korea as an Emerging Market", "_MSCI_EM_FACTSHEET"),
    ("include Canada and not Korea", "_MSCI_CONTROL_FACTSHEET"),
])
def test_each_classification_claim_is_followed_by_its_source(claim, source):
    """The source sits in the claim's own sentence, after it, not elsewhere in the note."""
    text = _idhq_note()
    assert claim in text, claim
    # A sentence ends at ". ", except inside "Ex-U.S. LargeMidCap".
    sentence_rest = re.split(r"(?<!U\.S)\. ", text[text.index(claim):])[0]
    assert getattr(factors, source) in sentence_rest, (claim, sentence_rest)


def test_the_typed_weights_and_the_uncited_claims_are_gone():
    text = _idhq_note()
    for gone in ("9.5%", "roughly 2%", "holds it", "largely a universe artifact",
                 "classify Korea as emerging", "the weights above are dated"):
        assert gone not in text, gone
    assert not re.search(r"\d%", text), "a weight is typed in the IDHQ note"
