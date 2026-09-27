"""#401: the Macro page's CAPE captions derive the record and the years at 40.

They said "since 1881", "the full 145-year Shiller record" and "Only the dot-com bubble
peak (1999–2001) has sustained CAPE above 40". The committed series begins in 1871 (155
years to 2026), has no month at 40 in 2001, and from May 2026 runs at 40 again. The page
now reads these from the series (src.prose_helpers, on item 6's
shiller.earlier_years_at_or_above), so the Macro and SAA pages cannot drift apart.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

from src.prose_helpers import cape_forty_sentence, cape_record_span
from src.shiller import get_cape_series

ROOT = Path(__file__).resolve().parent.parent


def test_the_committed_series_gives_1871_155_years_and_1999_to_2000():
    s = get_cape_series()
    assert cape_record_span(s) == (1871, int(s.dropna().index[-1].year) - 1871)
    current = float(s.dropna().iloc[-1])
    assert current >= 40, "premise: the current run is at 40"
    years = int(s.dropna().index[-1].year) - 1871
    assert cape_forty_sentence(s, current) == (
        f"Before the current run, CAPE reached 40 only in 1999–2000 in the full "
        f"{years}-year Shiller record. ")


def _series(values, start="2000-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="MS"),
                     dtype=float)


@pytest.mark.parametrize("values, current, expected", [
    ([30, 41, 42, 35, 41], 41, "Before the current run, CAPE reached 40 only in 2000 in the full 0-year Shiller record. "),
    ([30, 41, 42, 35, 36], 36, "CAPE has reached 40 only in 2000 in the full 0-year Shiller record. "),
    ([30, 31, 32, 35, 41], 41, ""),
    ([30, 31, 32, 35, 36], 36, ""),
])
def test_the_sentence_at_its_edges(values, current, expected):
    assert cape_forty_sentence(_series(values), current) == expected


def test_the_page_carries_no_typed_record():
    src = (ROOT / "pages" / "3_Macro.py").read_text(encoding="utf-8")
    consts = [n.value for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    joined = " ".join(consts)
    for typed in ("since 1881", "145-year", "1999–2001", "dot-com bubble peak"):
        assert typed not in joined, typed
