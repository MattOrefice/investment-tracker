"""#372: the Macro page's credit-spread panels (IG, HY, CCC OAS).

aaf5d32 made _window_pctile return a WindowedPctile; three panels kept using the
whole tuple, and round() on it crashed the page whenever FRED returned data. They
render only when FRED answers for today, which no offline test did, so nothing saw it.

Both halves are pinned: with FRED answering, all three render; with FRED
unreachable, they report "data temporarily unavailable" and the page does not crash.
The committed macro_cache rows alone never render them (macro.get_series serves only
a row fetched today), which is why the crash needed a live FRED to show.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import src.config as config
import src.db as db
import src.macro as macro
import src.prices as prices

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def macro_page(tmp_path, monkeypatch):
    """A demo.db copy (its 127 committed FRED rows included), offline, fresh caches."""
    import streamlit as st
    copy = tmp_path / "demo.db"
    shutil.copyfile(_ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(config, "IS_DEMO", True)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(prices._SESSION, "get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    st.cache_data.clear()

    def render(fred):
        monkeypatch.setattr(macro, "fetch_fred_series", fred)
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(_ROOT / "pages" / "3_Macro.py"), default_timeout=600).run()
        texts = [getattr(e, "value", "") for e in list(at.markdown) + list(at.caption)]
        return at, " ".join(t for t in texts if isinstance(t, str))

    yield render
    st.cache_data.clear()


def _answered(series_id, start_date, end_date=None):
    idx = pd.date_range(max(pd.Timestamp(start_date), pd.Timestamp("1990-01-01")),
                        "2026-07-17", freq="W-FRI")
    return pd.Series(np.linspace(1.0, 3.0, len(idx)), index=idx, name=series_id)


def _unreachable(series_id, start_date, end_date=None):
    raise RuntimeError("FRED unreachable")


def test_the_credit_panels_render_when_fred_answers(macro_page):
    at, flat = macro_page(_answered)
    assert not at.exception, [str(e.value) for e in at.exception]
    for panel in ("IG Credit Spreads", "HY Credit Spreads", "CCC"):
        assert panel in flat
    # Each panel's current-reading caption carries its ordinal percentile.
    assert flat.count("percentile of available history") >= 3


def test_on_committed_fred_rows_alone_the_panels_are_unavailable_not_broken(macro_page):
    at, flat = macro_page(_unreachable)
    assert not at.exception, [str(e.value) for e in at.exception]
    assert "percentile of available history" not in flat
    assert "data temporarily unavailable" in flat
