"""Wherever CAPE prints on a page, the date it was read prints with it, and its source
is multpl.com's computation of Shiller's CAPE (#478 I06, I07).

Rendered on the demo book, offline: the Macro page's CAPE panel (chart annotation,
metric, both captions), its trailing-P/E contrast, its header and its sources list, and
the SAA page's thesis sentence. A reading taken from Yale, the fallback, says so.

The Excess CAPE Yield caption needs FRED, so its render is a live_data test.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pandas as pd
import pytest

import src.db as db
import src.prices as prices

ROOT = Path(__file__).resolve().parent.parent.parent


def _render(page, tmp, cape_csv=None, fred_down=True):
    """(texts, metrics, chart annotations) of a demo page on a demo.db copy."""
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    import src.config as config
    import src.macro as macro
    import src.shiller as shiller
    mp = pytest.MonkeyPatch()
    copy = tmp / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    mp.setattr(db, "DB_PATH", copy)
    mp.setattr(db, "_migrated_paths", set())
    mp.setattr(db, "_RUNTIME_CACHE", None)
    mp.setattr(prices._SESSION, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    if fred_down:
        mp.setattr(macro, "fetch_fred_series",
                   lambda *a, **k: (_ for _ in ()).throw(RuntimeError("FRED down")))
    mp.setattr(config, "IS_DEMO", True)
    if cape_csv is not None:
        mp.setattr(shiller, "_CACHE_CSV", cape_csv)
    st.cache_data.clear()
    try:
        at = AppTest.from_file(str(ROOT / "pages" / page), default_timeout=600).run()
        assert not at.exception, [str(e.value) for e in at.exception]
        texts = [str(e.value) for e in list(at.caption) + list(at.markdown) + list(at.info)]
        metrics = {m.label: str(m.value) for m in at.metric}
        notes = [a.get("text", "") for c in at.get("plotly_chart")
                 for a in json.loads(c.proto.spec).get("layout", {}).get("annotations", [])]
        reading = shiller.latest_reading()           # read inside the same patched file
    finally:
        st.cache_data.clear()
        mp.undo()
    return texts, metrics, notes, reading


@pytest.fixture(scope="module")
def macro_page(tmp_path_factory):
    return _render("3_Macro.py", tmp_path_factory.mktemp("macro"))


@pytest.fixture(scope="module")
def macro_page_from_yale(tmp_path_factory):
    """The same page on a file whose latest reading came from the fallback."""
    tmp = tmp_path_factory.mktemp("macro_yale")
    df = pd.read_csv(ROOT / "data" / "shiller_cape.csv", dtype=str)
    df.loc[df.index[-1], "source"] = "yale"
    df.to_csv(tmp / "cape.csv", index=False)
    return _render("3_Macro.py", tmp, cape_csv=tmp / "cape.csv")


def test_every_cape_figure_on_the_macro_page_carries_its_read_date(macro_page):
    from src import shiller
    texts, metrics, notes, reading = macro_page
    assert reading.read_on is not None and reading.source == shiller.MULTPL, (
        "premise: the committed file's latest reading is dated, from multpl")
    figure = f"{reading.value:.1f}×"
    long, short = shiller.read_clause(reading), shiller.read_short(reading)
    printed = [t for t in texts + notes if figure in t]
    assert len(printed) >= 2, "premise: the chart annotation and the P/E contrast print it"
    undated = [t[:160] for t in printed if short not in t and long not in t]
    assert not undated, undated
    # The chart's own marker, which no caption stands in for.
    assert any(n.startswith(f"Current {figure}") and n.endswith(f"; {short})") for n in notes), notes
    # The tile: its label carries the month and the read date.
    month = f"{reading.month.strftime('%b')} {reading.month.year}"
    assert metrics.get(f"Shiller CAPE ({month}, {short})") == figure, metrics
    # The panel's two captions.
    panel = next(t for t in texts if "full history" in t and "Implied 10Y real return" in t)
    assert (f"· {reading.month.strftime('%B')} {reading.month.year} reading, {long} "
            f"· Source: {shiller.CREDIT}, monthly") in panel
    assert any(t.startswith(f"CAPE ({short}) is in the ") for t in texts)


def test_the_macro_page_credits_multpl_and_names_yale_only_as_the_fallback(macro_page):
    from src import shiller
    texts, _, _, reading = macro_page
    joined = " ".join(texts)
    assert f"Data: FRED, and for CAPE {shiller.CREDIT}." in joined
    assert (f"**Shiller CAPE** ({shiller.CREDIT}; Shiller's own Yale data file is the "
            f"fallback, and a figure read from it says so): latest reading "
            f"**{reading.month.strftime('%b')} {reading.month.year}**, "
            f"{shiller.read_clause(reading)}") in joined
    for old in ("Shiller dataset via multpl", "multpl.com / Robert Shiller",
                "Data: FRED & Shiller", "last observation"):
        assert old not in joined, old
    # Yale is named where the fallback is explained, and nowhere as this figure's source.
    assert all("fallback" in t for t in texts if "Yale" in t), [t[:120] for t in texts
                                                               if "Yale" in t]
    assert "from Yale" not in joined and "Source: Robert Shiller's Yale" not in joined


def test_a_figure_read_from_yale_says_so_on_the_macro_page(macro_page_from_yale):
    from src import shiller
    texts, metrics, notes, reading = macro_page_from_yale
    assert reading.source == shiller.YALE, "premise: the patched file is the one rendered"
    figure = f"{reading.value:.1f}×"
    panel = next(t for t in texts if "full history" in t and "Implied 10Y real return" in t)
    assert "from Robert Shiller's Yale data file, the fallback source" in panel
    assert "· Source: Robert Shiller's Yale data file, monthly" in panel
    assert shiller.CREDIT not in panel
    assert any(k.endswith("from Yale, the fallback)") and v == figure
               for k, v in metrics.items()), metrics
    for t in [t for t in texts + notes if figure in t]:
        assert "from Yale, the fallback" in t or "the fallback source" in t, t[:160]


def test_the_saa_thesis_dates_its_cape_reading(tmp_path_factory):
    from src import shiller
    texts, _, _, reading = _render("1_SAA.py", tmp_path_factory.mktemp("saa"))
    para = next(t for t in texts if t.startswith("US equity valuations are "))
    month = f"{reading.month.strftime('%B')} {reading.month.year}"
    assert (f"CAPE is {reading.value:.1f} as of {month} ({shiller.read_clause(reading)}), "
            f"the ") in para
    assert "((" not in para.replace(" ", "")


@pytest.mark.live_data
def test_the_ecy_caption_dates_the_cape_it_divides(tmp_path_factory):
    """The Excess CAPE Yield panel prints a yield derived from CAPE; it needs FRED's
    real 10-year, so it renders only with the network up."""
    from src import shiller
    texts, _, _, reading = _render("3_Macro.py", tmp_path_factory.mktemp("macro_live"),
                                   fred_down=False)
    caption = next((t for t in texts if "CAPE yield" in t), None)
    assert caption is not None, "the ECY panel did not render: is FRED reachable?"
    assert (f"CAPE yield {100 / reading.value:.2f}% (CAPE {shiller.read_short(reading)}) "
            "vs real rate ") in caption
