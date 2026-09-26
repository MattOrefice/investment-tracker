"""Typed-in figures that drifted, and wording that contradicted the numbers beside it
(2026-09-25 audit, item 9). Rendered on the demo book where a page carries the text.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import src.db as db
import src.prices as prices

ROOT = Path(__file__).resolve().parent.parent.parent


def _render(page, tmp_path_factory, also=None):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    import src.config as config
    mp = pytest.MonkeyPatch()
    copy = tmp_path_factory.mktemp(page.split(".")[0]) / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    mp.setattr(db, "DB_PATH", copy)
    mp.setattr(db, "_migrated_paths", set())
    mp.setattr(db, "_RUNTIME_CACHE", None)
    mp.setattr(prices._SESSION, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    mp.setattr(config, "IS_DEMO", True)
    st.cache_data.clear()
    at = AppTest.from_file(str(ROOT / "pages" / page), default_timeout=600).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    text = " ".join(str(e.value) for e in list(at.caption) + list(at.markdown) + list(at.info))
    extra = also() if also else None          # read inside the same patched book
    st.cache_data.clear()
    mp.undo()
    return (at, text, extra) if also else (at, text)


@pytest.fixture(scope="module")
def factor_profile(tmp_path_factory):
    return _render("4_Factor_Profile.py", tmp_path_factory)


def _btc_years():
    from src import asset_evaluation as ae
    btc = ae.get_candidate_returns("BTC-USD", ae.SAMPLE_START)
    return (btc.index[-1] - btc.index[0]).days / 365.25


@pytest.fixture(scope="module")
def asset_evaluation(tmp_path_factory):
    return _render("5_Asset_Evaluation.py", tmp_path_factory, also=_btc_years)


# ── Factor Profile ────────────────────────────────────────────────────────────

def test_the_canada_box_carries_no_figures_from_one_old_window(factor_profile):
    _, text = factor_profile
    assert "Why not EFV / SCZ as the controls?" in text, "premise: the box renders"
    for stale in ("270 bps", "+20 bps", "90–250", "37.8%", "27.7%"):
        assert stale not in text, stale


def test_the_nport_clearance_is_the_latest_read_not_the_latest_filed(factor_profile):
    from src.factors import _NPORT_ASOF
    _, text = factor_profile
    assert f"Avantis N-PORT holdings disclosure for the period ending {_NPORT_ASOF}, the latest read here" in text
    assert "most recent Avantis N-PORT" not in text


def test_the_idhq_note_no_longer_denies_the_numbers_it_gives():
    from src.factors import _INTL_TILT_SLEEVES, build_intl_tilt_disclosure
    entry = next(s for s in _INTL_TILT_SLEEVES if s["fund"] == "IDHQ")
    text = " ".join(build_intl_tilt_disclosure({**entry, "fund_result": None,
                                                 "control_result": None}))
    assert "9.5%" in text and "2%" in text
    assert "rather than pinned as a number" not in text
    assert "the weights above are dated" in text


# ── Asset Evaluation ──────────────────────────────────────────────────────────

def test_sharpe_changes_are_in_sharpe_units(asset_evaluation):
    at, text, _ = asset_evaluation
    unc = next(m for m in at.metric if m.label == "Sharpe with BTC (unconstrained)")
    assert re.fullmatch(r"[+-]\d\.\d{3}", str(unc.delta)), unc.delta
    assert re.search(r"\(with BTC\), a change of [+-]\d\.\d{3}\.", text)
    assert re.search(r"→ \d\.\d{3}, [+-]\d\.\d{3} over the 2018-present sample", text)
    assert not re.search(r"(improvement of|, \+)\d+ bps", text)


def test_the_drawdown_verb_follows_the_numbers(asset_evaluation):
    _, text, _ = asset_evaluation
    m = re.search(r"Adding a 10% BTC allocation (deepens|narrows|leaves unchanged) portfolio "
                  r"maximum drawdown from (-?[\d.]+)% to (-?[\d.]+)%", text)
    assert m, "premise: the drawdown note renders"
    verb, a, b = m.group(1), float(m.group(2)), float(m.group(3))
    assert verb == ("deepens" if b < a else "narrows" if b > a else "leaves unchanged")


def test_the_counts_and_the_history_length_are_derived(asset_evaluation):
    _, text, years = asset_evaluation
    # "a 7-year": the typed figure. A bare "7-year history" is inside "8.7-year history".
    assert "nine SAA" not in text and "a 7-year history" not in text
    from src.prose_helpers import a_or_an
    assert f"for {a_or_an(f'{years:.1f}')} {years:.1f}-year history of a volatile" in text


def test_the_drawdown_argument_is_the_pdfs_and_names_its_subject(asset_evaluation):
    _, text, _ = asset_evaluation
    assert "three separate episodes" not in text
    assert re.search(r"Bitcoin's own maximum drawdown is -\d+\.\d% over 2018-01-01–present", text)


def test_bitcoin_is_property_for_tax_not_a_commodity(asset_evaluation):
    _, text, _ = asset_evaluation
    assert "the IRS treats bitcoin as property (Notice 2014-21)" in text
    assert "commodity under US tax law" not in text
    assert "direct commodity ownership" not in text


def test_no_sharpe_is_called_bps_in_the_pdf_or_its_template():
    src = (ROOT / "src" / "reports.py").read_text(encoding="utf-8")
    tpl = (ROOT / "templates" / "quarterly_report.html").read_text(encoding="utf-8")
    assert "delta_bps_con" not in src + tpl
    assert "{{ asset_eval.delta_sharpe_con }}" in tpl


# ── Benchmark Attribution and Correlations ────────────────────────────────────

def test_a_negative_size_loading_names_no_small_cap_fund():
    from src.factors import interpret_benchmark_attribution
    from tests.test_interpretation_snapshots import _make_bench_res
    neg = interpret_benchmark_attribution(_make_bench_res(b_smb=-0.2, t_smb=-3.0))
    pos = interpret_benchmark_attribution(_make_bench_res(b_smb=0.2, t_smb=3.0))
    assert "SMB: negative, less small-cap exposure than the SAA benchmark carries" in neg
    assert "AVUV" not in neg.split("SMB:")[1].split(";")[0]
    assert "SMB: positive small-cap tilt (AVUV)" in pos


def test_negative_pairs_are_counted_not_called_the_only_ones():
    from src.factors import interpret_correlations
    names = ["A", "B", "C", "RA"]
    m = pd.DataFrame(np.eye(4), index=names, columns=names)
    for x, y, r in (("A", "B", 0.9), ("A", "C", 0.8), ("B", "C", 0.7),
                    ("A", "RA", -0.3), ("B", "RA", -0.2), ("C", "RA", -0.1)):
        m.loc[x, y] = m.loc[y, x] = r
    text = interpret_correlations(m)
    assert "the only pairs" not in text
    assert "3 of the 6 pairs are negatively correlated, every one involving RA." in text


# ── percentiles as ordinals, articles by sound, units ─────────────────────────

@pytest.mark.parametrize("pct, word", [(0.0, "0th"), (0.01, "1st"), (0.02, "2nd"),
                                       (0.03, "3rd"), (0.05, "5th")])
def test_the_ecy_percentile_is_an_ordinal(pct, word):
    from src.macro import interpret_excess_cape
    text = interpret_excess_cape(-0.43, pct)
    assert f"at the {word} percentile" in text and "% percentile" not in text


def test_the_pdf_writes_percentiles_as_ordinals():
    code = [ln.split("#")[0] for ln in
            (ROOT / "src" / "reports.py").read_text(encoding="utf-8").splitlines()]
    assert not [ln for ln in code if re.search(r":\.0f\}th", ln)]


@pytest.mark.parametrize("n, article", [("8.7", "an"), ("7.0", "a"), ("11", "an"),
                                        ("18", "an"), ("87", "an"), ("1", "a"),
                                        ("100", "a"), ("800", "an")])
def test_a_or_an(n, article):
    from src.prose_helpers import a_or_an
    assert a_or_an(n) == article


def test_one_basis_point_is_singular(tmp_path_factory):
    _, text = _render("8_Research.py", tmp_path_factory)
    assert not re.search(r"(?<![\d.])1 bps", text)
    assert "saves 1 bp annually" in text, "premise: the demo has a 1 bp saving"
