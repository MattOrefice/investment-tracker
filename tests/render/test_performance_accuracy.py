"""Performance page accuracy (2026-09-25 audit, item 7), rendered on the demo book.

The metric tiles are in tests/test_metric_arrows.py and Sortino's form in
tests/test_bound_layer2.py. Here: "20% of the SAA is non-equity (Fixed Income + Real
Assets + Cash)" with no cash in the SAA; a note that the 1 Year window "will diverge
from Since Inception once the portfolio crosses 18 months", untrue at 17; "RF = 4.5%
(current cash yield)" when 3-month bills were not at 4.5%; and the reconciliation and
risk-metric footnotes as walls of small grey text, now in expanders.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

import src.db as db
import src.prices as prices

ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(scope="module")
def performance(tmp_path_factory):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    import src.config as config
    mp = pytest.MonkeyPatch()
    copy = tmp_path_factory.mktemp("perf") / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    mp.setattr(db, "DB_PATH", copy)
    mp.setattr(db, "_migrated_paths", set())
    mp.setattr(db, "_RUNTIME_CACHE", None)
    mp.setattr(prices._SESSION, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    mp.setattr(config, "IS_DEMO", True)
    st.cache_data.clear()
    at = AppTest.from_file(str(ROOT / "pages" / "2_Performance.py"), default_timeout=600).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    yield at
    st.cache_data.clear()
    mp.undo()


def _texts(node):
    return [str(e.value) for e in list(node.caption) + list(node.markdown)]


def test_the_non_equity_sentence_names_only_what_the_saa_targets(performance):
    with db.get_connection() as conn:
        cash = conn.execute("SELECT target_weight FROM asset_classes WHERE name = 'Cash' "
                            "AND parent_id IS NULL").fetchone()[0]
    assert cash == 0, "premise: the demo SAA holds no cash"
    line = next(t for t in _texts(performance) if "of the SAA is non-equity" in t)
    assert "of the SAA is non-equity (Fixed Income + Real Assets)," in line, line


def test_the_reconciliation_and_the_metric_notes_sit_in_expanders(performance):
    boxes = {e.label: _texts(e) for e in performance.expander}
    rec = boxes["Reconciliation: cost basis to current value"]
    assert any(t.startswith("Reconciliation: **\\$") for t in rec), rec
    notes = boxes["How these metrics are computed"]
    assert any(t.startswith("Std Dev: annualized return volatility") for t in notes), notes
    outside = [t for t in _texts(performance.main)
               if t.startswith(("Reconciliation: **", "Std Dev: annualized"))]
    in_boxes = [t for v in boxes.values() for t in v
                if t.startswith(("Reconciliation: **", "Std Dev: annualized"))]
    assert sorted(outside) == sorted(in_boxes), "a copy is still rendered outside its expander"


def test_the_risk_free_rate_is_not_called_the_current_cash_yield(performance):
    text = " ".join(_texts(performance))
    assert "RF = 4.5%, a fixed assumption rather than the current bill yield" in text
    assert "current cash yield" not in text
    assert "downside deviation" in text


def test_the_one_year_note_says_what_the_windows_share(performance):
    text = " ".join(_texts(performance))
    assert "once the portfolio crosses 18 months" not in text
    assert "The 1 Year window is the last 12 of the " in text


@pytest.mark.parametrize("si_days, expected", [
    (200, "The portfolio is 7 months old, so the 1 Year window and Since Inception cover the same days."),
    (365, "The portfolio is 12 months old, so the 1 Year window and Since Inception cover the same days."),
    (517, "The 1 Year window is the last 12 of the 17 months since inception, so it shares most of its data with Since Inception."),
    (729, "The 1 Year window is the last 12 of the 24 months since inception, so it shares most of its data with Since Inception."),
    (730, None),
])
def test_the_overlap_note_at_its_edges(si_days, expected):
    from src.performance import one_year_overlap_note
    assert one_year_overlap_note(si_days) == expected
