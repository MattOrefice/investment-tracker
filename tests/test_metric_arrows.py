"""Layer 3: a metric tile's arrow must agree with the sign of what it compares.

Streamlit draws a delta's arrow from its string: up unless it starts with "-". The
Performance page put benchmark returns and labels in the delta slot, so a Q2 that
trailed the S&P 500 by 290 bp showed a green up-arrow under "15.12% S&P 500", the YTD
tile a green arrow under the since-inception return, and the Stage 2 tile a grey
up-arrow under a negative figure (2026-09-25 audit, item 7). This file used to check
the format of those strings on a copy of them. It now reads the rendered page: no
return tile carries a delta, the benchmark returns are captions, and any delta left
is a signed figure, so its arrow is its sign.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

import src.db as db
import src.prices as prices

ROOT = Path(__file__).resolve().parent.parent

RETURN_TILES = ("return", "vs. S&P 500", "vs. Custom Blended", "value", "YTD return",
                "Stage 1", "Stage 2", "Total: Portfolio vs.")


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


def test_no_return_tile_carries_a_delta(performance):
    tiles = [m for m in performance.metric if any(k in m.label for k in RETURN_TILES)]
    assert len(tiles) >= 10, [m.label for m in tiles]
    assert not [(m.label, m.delta) for m in tiles if m.delta], \
        "a return tile carries a delta, and its arrow reads as a verdict"


def test_the_benchmark_returns_are_captions(performance):
    caps = [str(c.value) for c in performance.caption]
    for head in ("S&P 500: ", "Blended: "):
        assert sum(c.startswith(head) for c in caps) == 2, (head, caps)   # quarter and SI


def test_any_delta_left_leads_with_its_sign(performance):
    """The arrow is drawn from the first character, so a delta must start with its sign."""
    deltas = [(m.label, str(m.delta)) for m in performance.metric if m.delta]
    assert deltas, "premise: the page still shows a delta somewhere (the FI duration)"
    assert all(d.startswith(("+", "-")) for _l, d in deltas), deltas
