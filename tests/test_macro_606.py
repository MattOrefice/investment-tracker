"""The Macro page's grades and readings #424 did not list, as decided by the owner on
October 15, 2026 (#606).

Grades on fixed cuts that name no window go, and their figures stand alone: the HY
spread's five grades, the HY/IG ratio's three, and the regime methodology's "historically
tight labor". A cut the classifier uses is stated as its rule. "How regimes transition"
names the classifier's three inputs. CCC's "alongside tight BB" goes. The dollar's
12-month change does not render without an observation a year back. The page as it
renders is checked in tests/render/test_macro_606_render.py.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

from src import macro

ROOT = Path(__file__).resolve().parent.parent


def _page_strings() -> str:
    tree = ast.parse((ROOT / "pages" / "3_Macro.py").read_text(encoding="utf-8"))
    return " ".join(n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str))


def test_the_hy_cuts_are_gone_with_their_grades():
    for name in ("HY_SPREAD_TIGHT", "HY_SPREAD_NORMAL", "HY_SPREAD_WIDE",
                 "HY_SPREAD_RECESSIONARY"):
        assert not hasattr(macro, name), name
    assert macro.interpret_hy_spread(340.4) == "HY spreads at 340 bps."


def test_ccc_tight_makes_no_claim_about_bb():
    text = macro.credit_reading("CCC", 610.0, 10, "Sep 2023")
    assert text == ("CCC OAS at 610 bps is tight against its history since Sep 2023: broad "
                    "risk-on appetite is reaching into distressed names.")
    assert "BB" not in text


def test_the_page_states_no_grade_on_a_fixed_cut():
    strings = _page_strings()
    for gone in ("historically tight", "labor still healing", "curve not inverted",
                 "mid-cycle norms", "tail-risk concerns", "compressed, indicating tight",
                 "credit spreads compress", "credit spreads peak"):
        assert gone not in strings, gone


def test_the_transition_caption_names_the_three_inputs():
    strings = _page_strings()
    assert ("Cycle regimes shift gradually as the classifier's three inputs evolve: the NBER "
            "recession indicator (USREC), the 10Y−2Y Treasury spread (T10Y2Y) and the "
            "unemployment rate (UNRATE).") in strings


def _series(values, dates):
    return pd.Series(values, index=pd.to_datetime(dates))


def test_the_dollars_change_reads_the_last_observation_a_year_back():
    s = _series([100.0, 101.0, 102.3], ["2025-10-01", "2025-10-15", "2026-10-14"])
    assert macro.year_change_phrase(s, "2025-10-15") == "+1.3% over 12 months"
    assert macro.year_change_phrase(s, "2025-10-10") == "+2.3% over 12 months"
    falling = _series([110.0, 99.0], ["2025-10-01", "2026-10-14"])
    assert macro.year_change_phrase(falling, "2025-10-15") == "-10.0% over 12 months"


def test_without_an_observation_a_year_back_it_does_not_render():
    s = _series([100.0, 102.3], ["2026-01-02", "2026-10-14"])
    assert macro.year_change_phrase(s, "2025-10-15") is None
    assert macro.year_change_phrase(pd.Series(dtype=float), "2025-10-15") is None
    assert macro.year_change_phrase(_series([0.0, 1.0], ["2025-10-01", "2026-10-14"]),
                                    "2025-10-15") is None


@pytest.mark.parametrize("now", [100.0, 100.04, 99.96])
def test_a_change_that_prints_as_zero_carries_no_sign(now):
    s = _series([100.0, now], ["2025-10-01", "2026-10-14"])
    assert macro.year_change_phrase(s, "2025-10-15") == "0.0% over 12 months"


def test_the_page_renders_the_change_only_when_there_is_one():
    source = (ROOT / "pages" / "3_Macro.py").read_text(encoding="utf-8")
    assert "dtwex_1y_chg   = macro.year_change_phrase(dtwex_clean, ONE_YR_AGO)" in source
    assert '(f"{dtwex_1y_chg} · " if dtwex_1y_chg else "")' in source
