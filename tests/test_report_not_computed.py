"""#248 — a figure the PDF could not compute is DISCLOSED where it would have rendered.

Three sites used `except Exception: pass`, and in each the dependent sentence was then
silently omitted, so a computation that RAISED read exactly like one with nothing to
say. One pattern now covers all three (src.reports.not_computed_sentence): what could
not be computed, why, and what was done instead. It states, and never instructs.
"""
from unittest.mock import patch

import pandas as pd
import pytest

import src.reports as rpt

START, END, INCEPTION = "2026-01-01", "2026-03-31", "2025-05-01"


def test_the_pattern_states_and_never_instructs():
    s = rpt.not_computed_sentence("The X reading", ValueError("boom"), "this is omitted")
    assert s == "The X reading could not be computed for this report (ValueError), so this is omitted."
    for word in ("please", "run ", "check ", "should", "try "):
        assert word not in s.lower(), f"the disclosure instructs the reader: {word!r}"


def _exec_summary(cape_raises: bool) -> dict:
    idx = pd.date_range(INCEPTION, END, freq="D")
    pv = pd.Series(1000.0, index=idx)
    flat = pd.Series(1.0, index=idx)
    cape_patch = ({"side_effect": Exception("no data")} if cape_raises
                  else {"return_value": 30.0})
    with patch.object(rpt, "get_portfolio_value_series", return_value=pv), \
         patch.object(rpt, "get_portfolio_account_id", return_value=1), \
         patch.object(rpt, "get_external_cashflow_series",
                      side_effect=lambda s, e, account_id=None: pd.Series(dtype=float)), \
         patch.object(rpt, "get_inception_date", return_value=INCEPTION), \
         patch.object(rpt, "get_sp500_series", return_value=flat), \
         patch.object(rpt, "get_custom_blended_series", return_value=flat), \
         patch.object(rpt, "brinson_fachler_period", side_effect=Exception("no db")), \
         patch.object(rpt, "current_cape", **cape_patch), \
         patch.object(rpt, "get_cape_series",
                      return_value=pd.Series([10.0, 20.0, 30.0, 40.0])):
        return rpt._build_executive_summary(START, END)


CAPE_DISCLOSURE = ("The CAPE valuation reading could not be computed for this report "
                   "(Exception), so this summary states no valuation regime.")


def test_a_failed_cape_reading_is_disclosed_in_the_summary():
    narrative = _exec_summary(cape_raises=True)["narrative"]
    assert CAPE_DISCLOSURE in narrative, narrative


def test_a_working_cape_reading_carries_no_disclosure():
    narrative = _exec_summary(cape_raises=False)["narrative"]
    assert CAPE_DISCLOSURE not in narrative
    assert any("CAPE stands at 30.0x" in s for s in narrative), narrative


FI_DISCLOSURE = ("The fixed-income sleeve's factor regression could not be computed for "
                 "this report (RuntimeError), so this section carries no fixed-income note.")
XREF_DISCLOSURE = ("The Brinson-Fachler attribution cross-reference could not be computed "
                   "for this report (RuntimeError), so the intercept is shown without the "
                   "attribution drivers behind it.")


def test_a_failed_fi_regression_is_disclosed_in_the_factor_section(use_demo_db):
    with patch.object(rpt, "regress_fi_sleeve", side_effect=RuntimeError("no FI data")):
        section = rpt._build_factor_section("2026-06-30")
    if section is None:
        pytest.skip("demo book produced no factor section to disclose into")
    assert FI_DISCLOSURE in section["prose"], section["prose"]


def test_a_failed_bf_cross_reference_is_disclosed_in_the_benchmark_section(use_demo_db):
    with patch.object(rpt, "brinson_fachler_period", side_effect=RuntimeError("no BF")):
        section = rpt._build_benchmark_section("2026-04-01", "2026-06-30")
    if section is None:
        pytest.skip("demo book produced no benchmark section to disclose into")
    assert XREF_DISCLOSURE in section["prose"], section["prose"]


def test_working_computations_add_no_disclosure(use_demo_db):
    factor = rpt._build_factor_section("2026-06-30")
    bench = rpt._build_benchmark_section("2026-04-01", "2026-06-30")
    for sec in (factor, bench):
        if sec is not None:
            assert not any("could not be computed for this report" in s
                           for s in sec["prose"]), sec["prose"]
