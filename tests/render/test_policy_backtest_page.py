"""The Policy Backtest page (#667, PR 3 of 3), as the owner decided it on October 18, 2026.

Rendered on copies of the committed demo book and the frozen test book, offline, and on a
scratch copy of the personal book where one exists. On the committed books every figure
the page prints is checked against src.policy_backtest.run() on the same book, and the
proxy table's tracking statistics against a computation made here from the history file.
The personal render asserts that the page renders and leads with its disclosure; it reads
no figure, and nothing personal is in this file.

The page:
  * opens with the disclosure, above the title: hypothetical, back-tested results; this
    book's current SAA targets and bands applied to monthly returns from the engine's
    first month; not the paper portfolio's record, earned by no account; each fund's
    proxy before its launch, and SPHQ's and IDHQ's broad markets before they followed
    quality indexes; after fund expenses, before trading costs and taxes; the policy set
    with knowledge of the period;
  * says "hypothetical" in its title, every chart title, every table caption and the
    method note;
  * compares the policy with neither benchmark except by printing the figures;
  * changes no quarter lock, and nothing in src/ imports the engine, so the quarterly
    PDF cannot reach it.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src import policy_backtest as pb

ROOT = Path(__file__).resolve().parent.parent.parent
PAGE = ROOT / "pages" / "16_Policy_Backtest.py"
DEMO = ROOT / "data" / "demo.db"
PERSONAL = ROOT / "data" / "tracker.db"

# What no sentence on the page may say of the policy beside either benchmark: a sentence
# naming one of the three series may hold none of these words.
COMPARISONS = re.compile(
    r"\b(outperform\w*|underperform\w*|beat\w*|better|worse|ahead|behind|lag\w*|"
    r"trail\w*|exceed\w*|higher|lower|more|less|greater|smaller|edge|superior|"
    r"inferior|won|lost|relative|versus|vs)\b", re.IGNORECASE)
SERIES = re.compile(r"\b(polic\w*|S&P 500|SPY|60/40)\b", re.IGNORECASE)


@dataclass
class Rendered:
    at: object
    bt: pb.Backtest
    spec: dict
    locks_before: str
    locks_after: str


def _locks(book: Path) -> str:
    """Every quarter lock the book holds, hashed row by row."""
    con = sqlite3.connect(book)
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name = 'quarter_snapshots'"
                           ).fetchone():
            return "no quarter_snapshots table"
        rows = con.execute("SELECT * FROM quarter_snapshots ORDER BY 1").fetchall()
    finally:
        con.close()
    return f"{len(rows)}:" + hashlib.sha256(repr(rows).encode()).hexdigest()


def _render(mp, tmp_dir: Path, which: str) -> Rendered:
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    import src.db
    from tests.conftest import _pin_mode, block_network, point_at_frozen_book

    if which == "frozen":
        _pin_mode(mp, "demo")
        book = point_at_frozen_book(mp, tmp_dir)
    else:
        _pin_mode(mp, "demo" if which == "demo" else "personal")
        book = tmp_dir / "book.db"
        shutil.copyfile(DEMO if which == "demo" else PERSONAL, book)
        os.chmod(book, 0o644)
        mp.setattr(src.db, "DB_PATH", book)
        mp.setattr(src.db, "_migrated_paths", set())
        block_network(mp)
    before = _locks(book)
    st.cache_data.clear()
    at = AppTest.from_file(str(PAGE), default_timeout=300)
    at.run()
    st.cache_data.clear()
    return Rendered(at, pb.run(), pb.load_spec(), before, _locks(book))


@pytest.fixture(scope="module", params=["demo", "frozen"])
def page(request, tmp_path_factory) -> Rendered:
    with pytest.MonkeyPatch.context() as mp:
        yield _render(mp, tmp_path_factory.mktemp(request.param), request.param)


def _elements(at) -> list:
    """Every element the page shows, in order, without the blocks that hold them."""
    from streamlit.testing.v1.element_tree import Block
    return [n for n in at.main if not isinstance(n, Block)]


def _text(node) -> str:
    return str(node.value) if node.type in ("markdown", "caption", "info", "title",
                                            "subheader", "header") else ""


def _all_text(at) -> str:
    return " ".join(t for t in (_text(n) for n in _elements(at)) if t)


def _tables(at) -> "list[pd.DataFrame]":
    return [d.value for d in at.dataframe]


def _table(at, first_column: str) -> pd.DataFrame:
    found = [t for t in _tables(at) if t.index.name == first_column]
    assert len(found) == 1, [t.index.name for t in _tables(at)]
    return found[0]


def _charts(at) -> "list[dict]":
    return [json.loads(c.proto.spec) for c in at.get("plotly_chart")]


def _ys(trace) -> "list[float]":
    y = trace["y"]
    if isinstance(y, dict):                      # plotly 6 typed array
        y = np.frombuffer(base64.b64decode(y["bdata"]), dtype=y["dtype"]).tolist()
    return [float(v) for v in y]


def _month(m: str) -> str:
    return pd.Period(m, "M").strftime("%B %Y")


def _pct(x: float, signed: bool = False) -> str:
    return f"{x * 100:+.2f}%" if signed else f"{x * 100:.2f}%"


# ── It renders, and leads with the disclosure ────────────────────────────────

def test_it_renders_without_an_exception(page):
    assert not page.at.exception, page.at.exception


def test_the_disclosure_comes_first_above_the_title(page):
    shown = [n for n in _elements(page.at) if _text(n)]
    assert "endow-page-header" in shown[0].value, "premise: the home link is first"
    first, title = shown[1], shown[2]
    assert first.type == "info" and first.value.startswith(
        "**Hypothetical, back-tested results.**"), first.value
    assert title.type == "title" and title.value == "Policy Backtest (hypothetical)"


def test_the_disclosure_says_each_thing_the_owner_listed(page):
    """Each clause, with every month and every fund read from run() and series.json."""
    bt, spec = page.bt, page.spec
    text = page.at.info[0].value
    first, last = _month(bt.returns.index[0]), _month(bt.returns.index[-1])
    assert ("They apply this book's current SAA targets and tolerance bands to monthly "
            f"returns from {first} through {last}.") in text
    assert "They are not the paper portfolio's record, and no account earned them." in text
    held = [f for s in bt.sleeves for f, _w in s.mix]
    launch = [f"{f} by {spec['funds'][f]['proxy']} through "
              f"{_month(spec['funds'][f]['proxy_until'])}" for f in held
              if spec["funds"][f].get("reason") == "inception"]
    index = [f for f in held if spec["funds"][f].get("reason") == "index change"]
    assert launch and index == ["SPHQ", "IDHQ"], "premise: the demo policy holds both"
    for pair in launch:
        assert pair in text, pair
    assert ("SPHQ and IDHQ are represented by their broad markets before they followed "
            "quality indexes: SPHQ by SPY through June 2010 and IDHQ by EFA through "
            "February 2012.") in text
    assert "Returns are after fund expenses and before trading costs and taxes." in text
    assert text.endswith("The policy was set with knowledge of this period.")


def test_hypothetical_is_in_the_title_every_chart_title_table_caption_and_the_method(page):
    at = page.at
    assert [t.value for t in at.title] == ["Policy Backtest (hypothetical)"]
    charts = _charts(at)
    assert len(charts) == 3
    for chart in charts:
        assert "hypothetical" in chart["layout"]["title"]["text"], chart["layout"]["title"]
    shown = _elements(at)
    tables = [i for i, n in enumerate(shown) if n.type == "dataframe"]
    assert len(tables) == len(_tables(at)) == 5
    for i in tables:
        caption = next(n for n in shown[i + 1:] if n.type == "caption")
        assert caption.value.startswith("Hypothetical"), caption.value
    assert [e.label for e in at.expander] == ["Method (hypothetical results)"]
    for heading in at.subheader:
        assert "(hypothetical)" in heading.value or heading.value == "The data"


def test_no_sentence_compares_the_policy_with_either_benchmark(page):
    """The page states the three series' figures side by side and reads nothing into
    them (claim rules, as #426 applied them)."""
    sentences = re.split(r"(?<=[.;:])\s+", _all_text(page.at))
    named = [x for x in sentences if SERIES.search(x)]
    assert len(named) > 10, "premise: the page names the series in its sentences"
    assert [x for x in named if COMPARISONS.search(x)] == []


def test_rendering_changes_no_quarter_lock(page):
    assert page.locks_before.split(":")[0] != "0", "premise: the book holds a lock"
    assert page.locks_after == page.locks_before


# ── Every figure is run()'s ──────────────────────────────────────────────────

def test_the_summary_is_run_s_statistics(page):
    bt = page.bt
    table = _table(page.at, "Series")
    last = _month(bt.returns.index[-1])
    expected = []
    for name, st in bt.statistics.items():
        dd = st.drawdown
        expected.append([_pct(st.annual_return), _pct(st.annual_volatility),
                         f"{st.sharpe:.2f}", _pct(dd.depth), _month(dd.peak),
                         _month(dd.trough),
                         _month(dd.recovered) if dd.recovered else f"Not by {last}"])
    assert list(table.columns) == ["Annualized return", "Annualized volatility",
                                   "Sharpe ratio", "Maximum drawdown", "Peak", "Trough",
                                   "Recovered"]
    assert table.values.tolist() == expected
    assert list(table.index) == ["Policy", "S&P 500 (SPY)", "60/40 (60% SPY / 40% AGG)"]
    caption = next(c.value for c in page.at.caption if c.value.startswith("Hypothetical. ")
                   and "months" in c.value)
    assert caption.startswith(f"Hypothetical. {_month(bt.returns.index[0])} to {last}, "
                              f"{len(bt.returns)} months.")


def test_the_calendar_years_mark_each_partial_year_and_name_its_months(page):
    bt = page.bt
    years = sorted({int(m[:4]) for m in bt.returns.index})
    labels = []
    for y in years:
        months = [m for m in bt.returns.index if m.startswith(f"{y}-")]
        if len(months) == 12:
            labels.append(str(y))
        else:
            labels.append(f"{y}, partial ({_month(months[0]).split()[0]} to "
                          f"{_month(months[-1]).split()[0]})")
    full = next(t for t in _tables(page.at) if t.index.name == "Year" and len(t) == len(years))
    assert list(full.index) == labels
    assert labels[0].startswith(f"{years[0]}, partial (") and labels[-1].startswith(
        f"{years[-1]}, partial ("), "premise: the first and the current year are partial"
    for name, label in (("Policy", pb.POLICY), ("S&P 500 (SPY)", pb.SP500),
                        ("60/40 (60% SPY / 40% AGG)", pb.SIXTY_FORTY)):
        assert list(full[name]) == [_pct(bt.statistics[label].calendar_years[y], signed=True)
                                    for y in years]


def test_2008_2020_and_2022_stand_on_their_own(page):
    bt = page.bt
    windows = next(t for t in _tables(page.at) if t.index.name == "Year" and len(t) == 3)
    assert list(windows.index) == ["2008", "2020", "2022"]
    assert list(windows["Policy"]) == [_pct(bt.statistics[pb.POLICY].windows[y], signed=True)
                                       for y in (2008, 2020, 2022)]
    assert "2008, 2020, and 2022 (hypothetical)" in [h.value for h in page.at.subheader]


def test_the_rebalance_log_is_run_s(page):
    bt = page.bt
    log = _table(page.at, "Month-end check")
    assert bt.simulation.rebalances, "premise: the policy rebalanced"
    assert [[i, v] for i, v in zip(log.index, log["Sleeves outside their bands"])] == [
        [_month(r.month), ", ".join(r.triggers)] for r in bt.simulation.rebalances]
    k = len(bt.simulation.rebalances)
    assert (f"Hypothetical. {k} month-end checks from {_month(bt.returns.index[0])} to "
            f"{_month(bt.returns.index[-1])} found a sleeve outside its band; at each, every "
            "sleeve went back to its target.") in [c.value for c in page.at.caption]


def test_the_proxy_table_reads_the_policy_series_json_and_the_history_file(page):
    """Each row: the sleeve's target and band, its fund, the series used, and the fund's
    tracking against its proxy over the months the history file holds both, computed
    here from the file."""
    bt, spec = page.bt, page.spec
    history = pb.load_history()
    table = _table(page.at, "Sleeve")
    rows = []
    for s in bt.sleeves:
        for fund, share in s.mix:
            entry = spec["funds"][fund]
            proxy = entry.get("proxy")
            row = [f"{s.target * 100:.1f}%", f"±{s.band * 100:.1f} pp",
                   fund if len(s.mix) == 1 else f"{fund} ({share * 100:.0f}% of the sleeve)"]
            if proxy:
                row.append(f"{proxy} {_month(bt.returns.index[0])} to "
                           f"{_month(entry['proxy_until'])}, then {fund}")
                both = history[[fund, proxy]].dropna()
                f, p = both[fund], both[proxy]
                ann = lambda r: (1 + r).prod() ** (12 / len(r)) - 1    # noqa: E731
                row += [f"{_month(both.index[0])} to {_month(both.index[-1])}",
                        str(len(both)), _pct(ann(f) - ann(p), signed=True),
                        _pct((f - p).std(ddof=1) * math.sqrt(12)), f"{f.corr(p):.3f}"]
            else:
                row += [f"{fund} throughout", "—", "—", "—", "—", "—"]
            rows.append([s.name, *row])
    assert [[i, *v] for i, v in zip(table.index, table.values.tolist())] == rows
    assert any(r[6] == "—" for r in rows) and any(r[6] != "—" for r in rows), (
        "premise: the policy holds funds with and without a proxy")


def test_every_fund_proxy_and_splice_is_cited_with_its_source_s_date(page):
    bt, spec = page.bt, page.spec
    lines = [m.value for m in page.at.markdown if m.value.startswith("- **")]
    held = list(dict.fromkeys(f for s in bt.sleeves for f, _w in s.mix))
    for fund in held:
        entry = spec["funds"][fund]
        line = next(l for l in lines if l.startswith(f"- **{fund}**: "))
        assert f"inception {entry['inception']} (" in line and entry["source"] in line
        assert f"), {entry['source_date']})." in line, line
        if entry.get("proxy"):
            p = spec["proxies"][entry["proxy"]]
            assert (f"**{entry['proxy']}** stands in through {_month(entry['proxy_until'])}: "
                    f"inception {p['inception']} (") in line
            assert p["source"] in line and p["source_date"] in line
        if entry.get("change"):
            c = entry["change"]
            assert c["cited"] in line and c["source"] in line and c["source_date"] in line
    assert any(l.startswith("- **AGG**") for l in lines)
    assert any(l.startswith(f"- **{spec['bill']['series']}**") for l in lines)


def test_the_charts_draw_run_s_returns(page):
    bt = page.bt
    growth, falls, rolling = _charts(page.at)
    assert growth["layout"]["yaxis"]["type"] == "log"
    assert growth["layout"]["title"]["text"] == f"Growth of ${pb.GROWTH_OF:,}, hypothetical (log scale)"
    for trace, name in zip(growth["data"], bt.returns.columns):
        y = _ys(trace)
        r = bt.returns[name]
        assert y[0] == pb.GROWTH_OF and len(y) == len(r) + 1
        assert y[-1] == pytest.approx(pb.GROWTH_OF * float((1 + r).prod()), rel=1e-12)
        assert str(trace["x"][0])[:10] == (pd.Period(r.index[0], "M") - 1).end_time.strftime(
            "%Y-%m-%d")
    for trace, name in zip(falls["data"], bt.returns.columns):
        assert min(_ys(trace)) == pytest.approx(bt.statistics[name].drawdown.depth * 100,
                                                rel=1e-12)
    assert rolling["layout"]["title"]["text"] == "Rolling 5-year annualized return, hypothetical"
    for trace, name in zip(rolling["data"], bt.returns.columns):
        r = bt.returns[name]
        y = _ys(trace)
        assert len(y) == len(r) - pb.ROLLING_MONTHS + 1
        assert str(trace["x"][0])[:7] == r.index[pb.ROLLING_MONTHS - 1]
        assert y[-1] == pytest.approx(
            ((1 + r.iloc[-pb.ROLLING_MONTHS:]).prod() ** (12 / pb.ROLLING_MONTHS) - 1) * 100,
            rel=1e-9)


def test_the_page_names_the_month_the_data_runs_through_and_its_refresh(page):
    last = _month(page.bt.returns.index[-1])
    captions = [c.value for c in page.at.caption]
    assert any(f"which runs through {last};" in c and f"`{pb.REFRESH_COMMAND}`" in c
               for c in captions), captions[-1]


# ── The personal book, on a scratch copy ─────────────────────────────────────

@pytest.mark.skipif(not PERSONAL.exists(), reason="no personal book here (CI)")
def test_a_scratch_copy_of_the_personal_book_renders(tmp_path):
    """Structure only: no figure of the personal book is read or asserted."""
    with pytest.MonkeyPatch.context() as mp:
        shown = _render(mp, tmp_path, "personal")
    assert not shown.at.exception, shown.at.exception
    alert = [n for n in _elements(shown.at) if _text(n)][1]
    assert alert.type == "info" and alert.value.startswith(
        "**Hypothetical, back-tested results.**")
    assert "They are not this portfolio's record, and no account earned them." in alert.value
    assert [t.value for t in shown.at.title] == ["Policy Backtest (hypothetical)"]
    assert shown.locks_after == shown.locks_before


# ── Nothing else reaches it ──────────────────────────────────────────────────

def _imports(path: Path) -> "set[str]":
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out |= {f"{node.module}.{a.name}" for a in node.names}
    return out


def test_nothing_in_src_imports_the_backtest_so_the_quarterly_pdf_cannot_reach_it():
    """The page is the engine's only reader: no module in src/, the report and the PDF
    among them, imports it, and no template names it."""
    readers = [p.relative_to(ROOT).as_posix() for p in (ROOT / "src").rglob("*.py")
               if p.name != "policy_backtest.py"
               and "src.policy_backtest" in _imports(p)]
    assert readers == []
    for p in (ROOT / "templates").rglob("*"):
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="ignore")
            assert "policy_backtest" not in text and "Policy Backtest" not in text, p
    pages = [p.name for p in (ROOT / "pages").glob("*.py")
             if "src.policy_backtest" in _imports(p) or "src.policy_backtest" in
             {m.rsplit(".", 1)[0] for m in _imports(p)}]
    assert pages == [PAGE.name]


def test_the_page_and_the_engine_import_no_lock_or_report_code():
    for path in (PAGE, ROOT / "src" / "policy_backtest.py"):
        banned = {m for m in _imports(path)
                  if m.split(".")[:2] in (["src", "cache"], ["src", "reports"],
                                          ["src", "input_lock"], ["src", "quarter_lock"])}
        assert banned == set(), (path.name, banned)
