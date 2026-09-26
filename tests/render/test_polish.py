"""Formatting polish (2026-09-25 audit, item 13), checked on the rendered demo pages.

Each rule is a census over the pages that carry the text, so a new instance anywhere
in them fails, not only the ones the audit saw: the significance legend lost its first
star to markdown; p = 0.010 sat beside ***; "$+327" and "$-34"; "-0.00"; two minus
signs; code identifiers in prose; "incl"; "Intl" beside "International"; the S&P and
the blend drawn in greys; Tax Lots splitting its banner onto two lines.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import pytest

import src.db as db
import src.prices as prices

ROOT = Path(__file__).resolve().parent.parent.parent
PAGES = ["2_Performance.py", "4_Factor_Profile.py", "5_Asset_Evaluation.py",
         "6_Benchmark_Attribution.py", "7_Risk.py", "9_Correlations.py", "12_Tax_Lots.py"]


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    import src.config as config
    mp = pytest.MonkeyPatch()
    copy = tmp_path_factory.mktemp("polish") / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    mp.setattr(db, "DB_PATH", copy)
    mp.setattr(db, "_migrated_paths", set())
    mp.setattr(db, "_RUNTIME_CACHE", None)
    mp.setattr(prices._SESSION, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    mp.setattr(config, "IS_DEMO", True)
    out = {}
    for page in PAGES:
        st.cache_data.clear()
        at = AppTest.from_file(str(ROOT / "pages" / page), default_timeout=600).run()
        assert not at.exception, (page, [str(e.value) for e in at.exception])
        texts = [str(e.value) for e in list(at.caption) + list(at.markdown) + list(at.info)]
        for d in at.dataframe:
            texts.append(d.value.to_string())
        out[page] = (at, texts)
    st.cache_data.clear()
    yield out
    mp.undo()


def _hits(rendered, pattern):
    return {p: [m.group(0) for t in texts for m in re.finditer(pattern, t)]
            for p, (_, texts) in rendered.items()
            if any(re.search(pattern, t) for t in texts)}


def test_the_significance_legend_keeps_its_first_star(rendered):
    legends = [t for p in ("4_Factor_Profile.py", "7_Risk.py")
               for t in rendered[p][1] if "p < 0.10" in t]
    assert legends, "premise: the legend renders"
    assert all(t.startswith(r"\* p < 0.10") and r"\*\*\* p < 0.01" in t for t in legends), legends


@pytest.mark.parametrize("p, shown", [(0.0099, "<0.01"), (0.0104, "0.010"), (0.0496, "<0.05"),
                                      (0.0995, "<0.10"), (0.2, "0.200"), (0.0004, "0.000")])
def test_a_p_value_never_rounds_onto_a_threshold_its_stars_are_below(p, shown):
    from src.factors import fmt_p
    assert fmt_p(p) == shown


def test_the_sign_goes_before_the_dollar(rendered):
    assert not _hits(rendered, r"\$[+-]\d")
    risk = " ".join(rendered["7_Risk.py"][1])
    assert re.search(r"[+-]\$\d", risk), "premise: Risk shows a signed dollar impact"


def test_no_negative_zero(rendered):
    assert not _hits(rendered, r"(?<![\d.])-0\.0+(?![\d]*[1-9])\b")


def test_one_minus_sign(rendered):
    assert not _hits(rendered, r"−\d")


def test_no_code_identifiers_in_prose(rendered):
    prose = {p: [t for t in texts if "\n" not in t or "|" not in t]
             for p, (_, texts) in rendered.items()}
    found = {p: [m.group(0) for t in ts for m in re.finditer(
                 r"\badj_close\b|\bpct_change\b|\bget_[a-z_]+\b|src/[a-z_./]+|\b[a-z]+_[a-z_]+\(\)", t)]
             for p, ts in prose.items()}
    assert not {p: v for p, v in found.items() if v}


def test_including_not_incl(rendered):
    assert not _hits(rendered, r"\bincl\b")


def test_international_not_intl(rendered):
    assert not _hits(rendered, r"\bIntl\b")
    macro = (ROOT / "pages" / "3_Macro.py").read_text(encoding="utf-8")
    assert "(US − Intl)" not in macro and "(US − International)" in macro


def test_the_benchmarks_are_drawn_in_distinct_colours(rendered):
    at, _ = rendered["2_Performance.py"]
    colours = {}
    for chart in at.get("plotly_chart"):
        for tr in json.loads(chart.proto.spec)["data"]:
            if tr.get("name") in ("S&P 500 (SPY)", "Custom Blended") and "line" in tr:
                colours[tr["name"]] = tr["line"]["color"]
    assert set(colours) == {"S&P 500 (SPY)", "Custom Blended"}, colours
    rgb = [int(colours["S&P 500 (SPY)"][i:i + 2], 16) for i in (1, 3, 5)]
    assert max(rgb) - min(rgb) > 40, f"the S&P line is a grey again: {colours}"
    assert colours["S&P 500 (SPY)"] != colours["Custom Blended"]


def test_tax_lots_states_its_banner_on_one_line(rendered):
    _, texts = rendered["12_Tax_Lots.py"]
    banner = [t for t in texts if "Latest locked quarterly report" in t]
    assert len(banner) == 1 and banner[0].startswith(("Prices through", "Live data")), banner


# The census above cannot see a site the demo data does not push to -0.00 (offline,
# neither of these does), so each formatter is checked at the edge too.

@pytest.mark.parametrize("v, sign, shown", [(-0.001, "", "0.00"), (-0.001, "+", "+0.00"),
                                            (-0.006, "", "-0.01"), (0.004, "+", "+0.00")])
def test_a_correlation_never_reads_negative_zero(v, sign, shown):
    from src.asset_evaluation import fmt_corr
    assert fmt_corr(v, sign) == shown


def test_the_stage2_check_never_reads_negative_zero():
    from src.attribution import stage2_reconciliation
    line, _ = stage2_reconciliation(-0.001, "2026-01-01", {})
    assert line == "vs. Stage 2: ✓ +0.00 bps", line
    line, _ = stage2_reconciliation(-0.001, "2026-01-01", {"2026-02-02": 100.0})
    assert "vs. Stage 2: +0.00 bps, not comparable" in line, line
