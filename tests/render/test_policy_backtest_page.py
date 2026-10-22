"""The Policy Backtest page (#667), rebuilt for readability and analysis as the owner
decided it on October 22, 2026.

Rendered on copies of the committed demo book and the frozen test book, offline, and on a
scratch copy of the personal book where one exists. On the committed books every figure
the page prints is checked against src.policy_backtest.run() on the same book, and the
proxy table's tracking statistics against a computation made here from the history file.
The personal render asserts that the page renders and leads with its disclosure; it reads
no figure, and nothing personal is in this file.

The page, top to bottom:
  * the disclosure first, in four short sentences: hypothetical, back-tested results over
    the window; not the paper portfolio's record, earned by no account; the stand-ins,
    named in the sleeve table rather than in the banner; after fund expenses, before
    trading costs and taxes, with the policy set knowing the period;
  * the title and a one-line subtitle naming the window once; five tiles for the policy;
    "Return and risk" and "Against each comparison", with the summary read;
  * the growth, drawdown and rolling charts, one height and one palette, the policy's
    stress episodes shaded on each; the episode table; each chart's read;
  * the calendar years as a grouped bar chart and a full-height table with the policy's
    differences; the sleeve weights, the rebalances with their turnover; the sleeves on
    their own; the sleeves, funds and proxies at full height, Sources and Method in
    expanders, and the data last;
  * "hypothetical" in the page title, the disclosure, every chart title and once in
    every table caption, never in a section heading, and no caption begins with the
    bare word "Hypothetical.";
  * no sentence compares the policy with either benchmark except by stating the figures;
  * no quarter lock changes, and nothing in src/ imports the engine, so the quarterly
    PDF cannot reach it.
"""
from __future__ import annotations

import ast
import base64
import colorsys
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
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
READS = (pb.summary_read, pb.growth_read, pb.drawdown_read, pb.rolling_read,
         pb.calendar_read, pb.rebalance_read)
ROWS = ["Policy", "S&P 500 (SPY)", "60/40 (60% SPY / 40% AGG)"]


@dataclass
class Rendered:
    at: object
    bt: pb.Backtest
    spec: dict
    locks_before: str
    locks_after: str
    tables: "list[dict]" = field(default_factory=list)


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


def _render(mp, tmp_dir: Path, which: str, history: "pd.DataFrame | None" = None) -> Rendered:
    """The page on a copy of ``which`` book, offline; with ``history`` served in place of
    the committed monthly file when given. Each st.dataframe call's height and column
    settings are recorded, since the rendered element does not carry them."""
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    import src.db
    from tests.conftest import _pin_mode, block_network, point_at_frozen_book

    if history is not None:
        mp.setattr(pb, "load_history", lambda path=pb.HISTORY: history)
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
    tables: "list[dict]" = []
    real = st.dataframe

    def recording(data=None, *args, **kwargs):
        tables.append({"index": data.index.name, "rows": len(data),
                       "height": kwargs.get("height", "auto"),
                       "column_config": kwargs.get("column_config") or {}})
        return real(data, *args, **kwargs)

    mp.setattr(st, "dataframe", recording)
    before = _locks(book)
    st.cache_data.clear()
    at = AppTest.from_file(str(PAGE), default_timeout=300)
    at.run()
    st.cache_data.clear()
    return Rendered(at, pb.run(), pb.load_spec(), before, _locks(book), tables)


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


TABLES = ("dataframe", "table")


def _tables(at) -> "list[pd.DataFrame]":
    """Every table the page shows, in order: the scrolling ones and the static ones."""
    return [n.value for n in _elements(at) if n.type in TABLES]


def _table(at, first_column: str) -> pd.DataFrame:
    found = [t for t in _tables(at) if t.index.name == first_column]
    assert len(found) == 1, [t.index.name for t in _tables(at)]
    return found[0]


def _charts(at) -> "list[dict]":
    return [json.loads(c.proto.spec) for c in at.get("plotly_chart")]


def _chart(at, starts: str) -> dict:
    found = [c for c in _charts(at) if c["layout"]["title"]["text"].startswith(starts)]
    assert len(found) == 1, [c["layout"]["title"]["text"] for c in _charts(at)]
    return found[0]


def _ys(trace) -> "list[float]":
    y = trace["y"]
    if isinstance(y, dict):                      # plotly 6 typed array
        y = np.frombuffer(base64.b64decode(y["bdata"]), dtype=y["dtype"]).tolist()
    return [float(v) for v in y]


def _month(m: str) -> str:
    return pd.Period(m, "M").strftime("%B %Y")


def _end(m: str) -> str:
    return pd.Period(m, "M").end_time.strftime("%Y-%m-%d")


def _pct(x: float, signed: bool = False) -> str:
    return f"{x * 100:+.2f}%" if signed else f"{x * 100:.2f}%"


def _caption_after(at, index_name: str) -> str:
    """The caption printed after the table whose first column is ``index_name``."""
    shown = _elements(at)
    i = next(i for i, n in enumerate(shown)
             if n.type in TABLES and n.value.index.name == index_name)
    return next(n.value for n in shown[i + 1:] if n.type == "caption")


# ── It renders, and leads with the disclosure ────────────────────────────────

def test_it_renders_without_an_exception(page):
    assert not page.at.exception, page.at.exception


def test_the_disclosure_comes_first_above_the_title(page):
    shown = [n for n in _elements(page.at) if _text(n)]
    assert "endow-page-header" in shown[0].value, "premise: the home link is first"
    first, title = shown[1], shown[2]
    assert first.type == "info" and first.value.startswith(
        "**Hypothetical, back-tested results:**"), first.value
    assert title.type == "title" and title.value == "Policy Backtest (hypothetical)"


def test_the_disclosure_is_four_short_sentences_without_the_proxy_list(page):
    """Each thing the owner listed, with every month and fund read from run() and
    series.json; the proxies are named in the sleeve table, not here."""
    bt, spec = page.bt, page.spec
    text = page.at.info[0].value
    first, last = _month(bt.returns.index[0]), _month(bt.returns.index[-1])
    held = [f for s in bt.sleeves for f, _w in s.mix]
    index = [f for f in held if spec["funds"][f].get("reason") == "index change"]
    assert index == ["SPHQ", "IDHQ"], "premise: the demo policy holds both"
    assert text == (
        f"**Hypothetical, back-tested results:** this book's current SAA targets and "
        f"tolerance bands applied to monthly returns from {first} to {last}. They are not "
        "the paper portfolio's record, and no account earned them. Funds are represented "
        "by named proxies before their launch, and SPHQ and IDHQ by their broad markets "
        "before they followed quality indexes, each named in the sleeve table below. "
        "Returns are after fund expenses and before trading costs and taxes, and the "
        "policy was set with knowledge of this period.")
    assert len(re.split(r"(?<=\.)\s+", text)) == 4
    for f in held:
        if spec["funds"][f].get("proxy"):
            assert f"{f} by {spec['funds'][f]['proxy']} through" not in text


def test_the_subtitle_names_the_window_once(page):
    window = f"{_month(page.bt.returns.index[0])} to {_month(page.bt.returns.index[-1])}"
    subtitle = [n for n in _elements(page.at) if _text(n)][3]
    assert subtitle.type == "caption"
    assert subtitle.value == ("This book's SAA policy beside the S&P 500 (SPY) and the 60/40 "
                              f"(60% SPY / 40% AGG), {window}; the tiles are the policy's.")
    assert subtitle.value.count(window) == 1


# ── Hypothetical, where the owner listed it ──────────────────────────────────

def test_hypothetical_is_in_the_title_every_chart_and_once_in_every_table_caption(page):
    at = page.at
    assert [t.value for t in at.title] == ["Policy Backtest (hypothetical)"]
    page_config = re.search(r'page_title="([^"]+)"', PAGE.read_text(encoding="utf-8"))
    assert page_config.group(1) == "Policy Backtest (hypothetical)"
    charts = _charts(at)
    assert len(charts) == 5
    for chart in charts:
        assert "hypothetical" in chart["layout"]["title"]["text"], chart["layout"]["title"]
    shown = _elements(at)
    tables = [i for i, n in enumerate(shown) if n.type in TABLES]
    assert len(tables) == len(_tables(at)) == 7
    for i in tables:
        following = shown[i + 1]
        assert following.type == "caption", (shown[i].value.index.name, following.type)
        assert len(re.findall("hypothetical", following.value, re.IGNORECASE)) == 1, (
            following.value)


def test_no_section_heading_holds_hypothetical_and_no_caption_opens_bare(page):
    at = page.at
    headings = [h.value for h in at.subheader] + [e.label for e in at.expander]
    assert headings == [
        "Return and risk", "Against each comparison", "Growth", "Drawdowns",
        "Rolling 5-year returns", "Calendar years", "Sleeve weights over time",
        "Sleeves on their own", "Sleeves, funds and proxies", "The data", "Sources",
        "Method"]
    assert not [h for h in headings if "hypothetical" in h.lower()]
    assert not [c.value for c in at.caption if re.match(r"Hypothetical\.", c.value)]


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


# ── The tiles and the two tables up top ──────────────────────────────────────

def test_five_tiles_for_the_policy_with_no_deltas(page):
    st = page.bt.statistics[pb.POLICY]
    tiles = [(m.label, m.value, m.proto.delta) for m in page.at.metric]
    assert tiles == [
        ("Annualized return", _pct(st.annual_return), ""),
        ("Volatility", _pct(st.annual_volatility), ""),
        ("Sharpe ratio", f"{st.sharpe:.2f}", ""),
        ("Maximum drawdown", _pct(st.drawdown.depth), ""),
        (f"\\${pb.GROWTH_OF:,} became", f"${st.ending_value:,.0f}", ""),
    ]


def test_return_and_risk_is_run_s(page):
    bt = page.bt
    n, last = len(bt.returns), _month(bt.returns.index[-1])
    table = _table(page.at, "Series")
    assert list(table.columns) == ["Annualized return", "Volatility", "Sharpe ratio",
                                   "Deepest drawdown", "Worst month", "Positive months",
                                   "Ending value"]
    assert list(table.index) == ROWS
    for row, name in zip(table.values.tolist(), bt.returns.columns):
        st, r = bt.statistics[name], bt.returns[name]
        dd = st.drawdown
        positive = int((r > 0).sum())
        worst = r.idxmin()
        assert row == [
            _pct(st.annual_return), _pct(st.annual_volatility), f"{st.sharpe:.2f}",
            f"{_pct(dd.depth)}: {_month(dd.peak)} to {_month(dd.trough)}, "
            + (f"recovered {_month(dd.recovered)}" if dd.recovered else f"not recovered by {last}"),
            f"{_pct(r[worst], signed=True)} ({_month(worst)})",
            f"{_pct(positive / n)} ({positive} of {n})",
            f"${pb.GROWTH_OF * float((1 + r).prod()):,.0f}",
        ], name
    caption = _caption_after(page.at, "Series")
    assert caption.startswith(f"Each series' hypothetical figures, {_month(bt.returns.index[0])} "
                              f"to {last}, {n} months.")


def test_against_each_comparison_is_run_s(page):
    bt = page.bt
    table = _table(page.at, "Comparison")
    assert list(table.index) == ROWS[1:]
    assert list(table.columns) == ["Active return", "Beta", "Correlation", "Up-capture",
                                   "Down-capture", "Tracking error"]
    p = bt.returns[pb.POLICY]
    for row, name in zip(table.values.tolist(), [pb.SP500, pb.SIXTY_FORTY]):
        c = bt.returns[name]
        ann = lambda r: (1 + r).prod() ** (12 / len(r)) - 1      # noqa: E731
        up, down = c >= 0, c < 0
        assert row == [
            _pct(ann(p) - ann(c), signed=True),
            f"{p.cov(c) / c.var():.2f}",
            f"{p.corr(c):.2f}",
            _pct(ann(p[up]) / ann(c[up])),
            _pct(ann(p[down]) / ann(c[down])),
            _pct((p - c).std(ddof=1) * math.sqrt(12)),
        ], name


# ── Each paragraph is the engine's, and its figures are run()'s ──────────────

def _paragraphs(at) -> "list[str]":
    return [m.value for m in at.markdown]


def _figures(bt: pb.Backtest) -> "dict[str, set]":
    """Every figure a paragraph may print, formatted as the page prints it: the floats
    from run(), and the counts derived here from run()'s series."""
    floats, decimals = [], set()
    for st in bt.statistics.values():
        floats += [st.annual_return, st.drawdown.depth, st.ending_value,
                   *st.calendar_years.values()]
        floats += [e.depth for e in st.episodes.values() if e is not None]
        if st.annual_volatility is not None:
            floats.append(st.annual_volatility)
            decimals.add(f"{st.sharpe:.2f}")
    for c in bt.comparisons.values():
        floats += [x for x in (c.active_return, c.tracking_error, c.up_policy,
                               c.up_comparison, c.up_capture, c.down_policy,
                               c.down_comparison, c.down_capture) if x is not None]
        decimals |= {f"{x:.2f}" for x in (c.beta, c.correlation) if x is not None}
    r = bt.rolling
    floats += [x for x in r.share_below.values() if x is not None]
    for points in (r.best, r.worst, r.latest):
        floats += [p.value for p in points.values() if p is not None]
    floats += [x.turnover for x in bt.simulation.rebalances]
    done = bt.simulation.rebalances
    if len(done) > 1:
        decimals.add(f"{pb.months_between(done[0].month, done[-1].month) / (len(done) - 1):.1f}")
    ints = {len(bt.returns), r.months, r.months // 12, r.windows, len(done)}
    ints |= {int(m[:4]) for m in bt.returns.index} | {int(bt.returns.index[0][:4]) - 1}
    for name, c in bt.comparisons.items():
        ints |= {c.up_months, c.down_months, r.below[name], r.windows - r.below[name]}
    policy = bt.statistics[pb.POLICY]
    full = [y for y in policy.calendar_years if y not in policy.partial_years]
    ints.add(len(full))
    for name in bt.comparisons:
        below = sum(policy.calendar_years[y] < bt.statistics[name].calendar_years[y]
                    for y in full)
        ints |= {below, len(full) - below}
    ints |= {sum(s.name in x.triggers for x in done) for s in bt.sleeves}
    for st in bt.statistics.values():
        dd = st.drawdown
        if dd.depth < 0:
            ints.add(pb.months_between(dd.peak, dd.trough))
        if dd.recovered:
            ints.add(pb.months_between(dd.trough, dd.recovered))
        for e in st.episodes.values():
            if e is not None:
                ints.add(pb.months_between(e.peak, e.trough))
                if e.recovered:
                    ints.add(pb.months_between(e.trough, e.recovered))
    return {"pct": {_pct(x) for x in floats} | {_pct(x, signed=True) for x in floats},
            "money": {f"${x:,.0f}" for x in floats} | {f"${pb.GROWTH_OF:,}"},
            "decimal": decimals, "int": {str(i) for i in ints}}


def test_each_paragraph_is_the_engines_and_its_figures_are_run_s(page):
    """Each of the six reads is printed whole, with only its dollar signs escaped for
    markdown; every percentage, dollar amount, decimal and count in it is one of run()'s
    figures, the counts derived here from run()'s series."""
    printed = _paragraphs(page.at)
    allowed = _figures(page.bt)
    for read in READS:
        text = read(page.bt)
        assert text.replace("$", "\\$") in printed, read.__name__
        rest = text.replace("S&P 500", "").replace("60/40", "")
        for token in re.findall(r"[+-]?\d+\.\d{2}%", rest):
            assert token in allowed["pct"], (read.__name__, token)
        rest = re.sub(r"[+-]?\d+\.\d{2}%", "", rest)
        for token in re.findall(r"\$[\d,]+", rest):
            assert token in allowed["money"], (read.__name__, token)
        rest = re.sub(r"\$[\d,]+", "", rest)
        for token in re.findall(r"\d+\.\d+", rest):
            assert token in allowed["decimal"], (read.__name__, token)
        rest = re.sub(r"\d+\.\d+", "", rest)
        for token in re.findall(r"(?<![\d%])\d+(?![\d%])", rest):
            assert token in allowed["int"], (read.__name__, token)


def test_the_reads_sit_under_what_they_read(page):
    """The summary under the two tables, each chart's read under it, the calendar read
    under its table, the rebalance read under the rebalance table."""
    shown = _elements(page.at)
    order = [n.type if n.type != "markdown" else n.value[:20] for n in shown]
    position = {read.__name__: next(i for i, n in enumerate(shown) if n.type == "markdown"
                                    and n.value == read(page.bt).replace("$", "\\$"))
                for read in READS}
    heading = {h.value: next(i for i, n in enumerate(shown)
                             if n.type == "subheader" and n.value == h.value)
               for h in page.at.subheader}
    assert heading["Against each comparison"] < position["summary_read"] < heading["Growth"]
    assert heading["Growth"] < position["growth_read"] < heading["Drawdowns"]
    assert heading["Drawdowns"] < position["drawdown_read"] < heading["Rolling 5-year returns"]
    assert heading["Rolling 5-year returns"] < position["rolling_read"] < heading["Calendar years"]
    assert heading["Calendar years"] < position["calendar_read"] < heading["Sleeve weights over time"]
    assert heading["Sleeve weights over time"] < position["rebalance_read"] < heading[
        "Sleeves on their own"], order


# ── The charts ───────────────────────────────────────────────────────────────

def _bands(chart: dict) -> "list[tuple[str, str]]":
    return [(s["x0"][:10], s["x1"][:10]) for s in chart["layout"].get("shapes", [])
            if s.get("type") == "rect"]


def _policy_bands(bt: pb.Backtest) -> "list[tuple[str, str]]":
    spans = []
    for e in bt.statistics[pb.POLICY].episodes.values():
        if e is not None and (_end(e.peak), _end(e.trough)) not in spans:
            spans.append((_end(e.peak), _end(e.trough)))
    return spans


def test_the_three_time_charts_share_a_height_a_palette_and_the_bands(page):
    bt = page.bt
    charts = [_chart(page.at, "Growth of"), _chart(page.at, "Drawdown from"),
              _chart(page.at, "Rolling 5-year")]
    assert {c["layout"]["height"] for c in charts} == {380}
    first, last = _end((pd.Period(bt.returns.index[0], "M") - 1).strftime("%Y-%m")), \
        _end(bt.returns.index[-1])
    for chart in charts:
        assert [t["line"]["color"] for t in chart["data"]] == ["#2E4057", "#B8742A", "#5C7A5C"]
        assert [t["name"] for t in chart["data"]] == ROWS
        assert [x[:10] for x in chart["layout"]["xaxis"]["range"]] == [first, last]
        assert _bands(chart) == _policy_bands(bt)
        labels = [a["text"] for a in chart["layout"].get("annotations", [])
                  if not a["text"].startswith("Policy ")]
        assert labels == [str(y) for y, e in bt.statistics[pb.POLICY].episodes.items()
                          if e is not None]
    assert len(_policy_bands(bt)) == 3, "premise: three episodes on the committed books"


def test_the_growth_chart_ticks_only_at_five_values(page):
    growth = _chart(page.at, "Growth of")
    assert growth["layout"]["title"]["text"] == f"Growth of ${pb.GROWTH_OF:,}, hypothetical (log scale)"
    y = growth["layout"]["yaxis"]
    assert y["type"] == "log" and y["tickmode"] == "array"
    assert y["tickvals"] == [5_000, 10_000, 20_000, 50_000, 100_000]
    assert y["ticktext"] == ["$5,000", "$10,000", "$20,000", "$50,000", "$100,000"]
    # Every tick is inside the axis, and so is every value.
    values = [v for trace in growth["data"] for v in _ys(trace)]
    assert 10 ** y["range"][0] <= min(5_000, min(values))
    assert 10 ** y["range"][1] >= max(100_000, max(values))
    for trace, name in zip(growth["data"], page.bt.returns.columns):
        r = page.bt.returns[name]
        values = _ys(trace)
        assert values[0] == pb.GROWTH_OF and len(values) == len(r) + 1
        assert values[-1] == pytest.approx(pb.GROWTH_OF * float((1 + r).prod()), rel=1e-12)


def test_the_drawdown_chart_marks_the_policy_s_trough(page):
    bt = page.bt
    chart = _chart(page.at, "Drawdown from")
    dd = bt.statistics[pb.POLICY].drawdown
    notes = [a for a in chart["layout"]["annotations"] if a["text"].startswith("Policy ")]
    assert len(notes) == 1
    assert notes[0]["text"] == f"Policy {_pct(dd.depth)}, {_month(dd.trough)}"
    assert notes[0]["x"][:10] == _end(dd.trough)
    assert notes[0]["y"] == pytest.approx(dd.depth * 100)
    for trace, name in zip(chart["data"], bt.returns.columns):
        assert min(_ys(trace)) == pytest.approx(bt.statistics[name].drawdown.depth * 100,
                                                rel=1e-12)


def test_the_rolling_chart_draws_a_zero_line(page):
    bt = page.bt
    chart = _chart(page.at, "Rolling 5-year")
    lines = [s for s in chart["layout"]["shapes"] if s.get("type") == "line"]
    assert len(lines) == 1 and lines[0]["y0"] == lines[0]["y1"] == 0
    for trace, name in zip(chart["data"], bt.returns.columns):
        r = bt.returns[name]
        y = _ys(trace)
        assert len(y) == len(r) - pb.ROLLING_MONTHS + 1
        assert y[-1] == pytest.approx(
            ((1 + r.iloc[-pb.ROLLING_MONTHS:]).prod() ** (12 / pb.ROLLING_MONTHS) - 1) * 100,
            rel=1e-9)


def test_the_episode_table_is_run_s(page):
    bt = page.bt
    last = _month(bt.returns.index[-1])
    table = _table(page.at, "Episode")
    expected = []
    for y in pb.WINDOWS:
        for name, label in zip(bt.returns.columns, ROWS):
            e = bt.statistics[name].episodes[y]
            expected.append([str(y), label, _pct(e.depth), _month(e.peak), _month(e.trough),
                             str(pb.months_between(e.peak, e.trough)),
                             _month(e.recovered) if e.recovered else f"Not by {last}"])
    assert [[i, *v] for i, v in zip(table.index, table.values.tolist())] == expected
    assert list(table.columns) == ["Series", "Fall", "Peak", "Trough", "Months to trough",
                                   "Recovered"]


# ── Calendar years ───────────────────────────────────────────────────────────

def test_the_calendar_chart_groups_the_three_series_and_marks_partial_years(page):
    bt = page.bt
    chart = _chart(page.at, "Calendar-year returns")
    years = sorted({int(m[:4]) for m in bt.returns.index})
    partial = set(bt.statistics[pb.POLICY].partial_years)
    assert chart["layout"]["barmode"] == "group"
    assert [t["type"] for t in chart["data"]] == ["bar"] * 3
    assert [t["marker"]["color"] for t in chart["data"]] == ["#2E4057", "#B8742A", "#5C7A5C"]
    for trace, name in zip(chart["data"], bt.returns.columns):
        assert trace["x"] == [str(y) for y in years]
        assert _ys(trace) == pytest.approx(
            [bt.statistics[name].calendar_years[y] * 100 for y in years])
        assert trace["marker"]["pattern"]["shape"] == ["/" if y in partial else "" for y in years]
    assert chart["layout"]["xaxis"]["ticktext"] == [
        f"{y}<br>partial" if y in partial else str(y) for y in years]


def test_the_calendar_table_is_full_height_with_both_differences(page):
    bt = page.bt
    years = sorted({int(m[:4]) for m in bt.returns.index})
    labels = []
    for y in years:
        months = [m for m in bt.returns.index if m.startswith(f"{y}-")]
        labels.append(str(y) if len(months) == 12 else
                      f"{y}, partial ({_month(months[0]).split()[0]} to "
                      f"{_month(months[-1]).split()[0]})")
    table = _table(page.at, "Year")
    assert list(table.index) == labels
    assert list(table.columns) == ["Policy", "S&P 500 (SPY)", "60/40", "Policy − S&P 500",
                                   "Policy − 60/40"]
    p = bt.statistics[pb.POLICY].calendar_years
    for name, label in zip(bt.returns.columns, ["Policy", "S&P 500 (SPY)", "60/40"]):
        assert list(table[label]) == [_pct(bt.statistics[name].calendar_years[y], signed=True)
                                      for y in years]
    for name, column in ((pb.SP500, "Policy − S&P 500"), (pb.SIXTY_FORTY, "Policy − 60/40")):
        assert list(table[column]) == [
            f"{(p[y] - bt.statistics[name].calendar_years[y]) * 100:+.2f} pp" for y in years]
    call = next(t for t in page.tables if t["index"] == "Year")
    assert call["rows"] == len(years) and call["height"] == "content"
    width = call["column_config"]["_index"]["width"]
    assert width >= 6 * max(map(len, labels)) + 24, "the Year column fits its longest label"


# ── Weights, rebalances and the sleeves ──────────────────────────────────────

def test_the_weights_chart_stacks_the_sleeves_in_the_policy_s_order_by_family(page):
    from src import policy
    bt = page.bt
    chart = _chart(page.at, "Sleeve weights")
    assert [t["name"] for t in chart["data"]] == [s.name for s in bt.sleeves]
    assert {t.get("stackgroup") for t in chart["data"]} == {"weights"}
    lines = [s for s in chart["layout"]["shapes"] if s.get("type") == "line"]
    assert [s["x0"][:10] for s in lines] == [_end(r.month) for r in bt.simulation.rebalances]
    assert all(s["line"]["width"] == 1 for s in lines)
    # The shares at each month's start add up to the whole portfolio.
    totals = np.sum([_ys(t) for t in chart["data"]], axis=0)
    assert totals == pytest.approx(100.0)
    # Colors: one hue per family, read from the book's parent asset classes.
    names = {r["asset_class_id"]: r["name"] for r in policy.asset_classes()}
    parent = {r["name"]: names.get(r["parent_id"]) for r in policy.asset_classes()}
    family = {s.name: ("US" if parent[s.name] == "Equity" and s.name.startswith("US") else
                       "Intl" if parent[s.name] == "Equity" else parent[s.name])
              for s in bt.sleeves}
    assert set(family.values()) == {"US", "Intl", "Income", "Real Assets"}

    def hue(color: str) -> float:
        r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
        return colorsys.rgb_to_hls(r, g, b)[0] * 360

    def apart(a: float, b: float) -> float:
        return min(abs(a - b), 360 - abs(a - b))

    colors = {t["name"]: t["fillcolor"] for t in chart["data"]}
    hues: "dict[str, list[float]]" = {}
    for name, f in family.items():
        hues.setdefault(f, []).append(hue(colors[name]))
    for f, h in hues.items():
        assert max(apart(a, b) for a in h for b in h) < 15, (f, h)
    centers = {f: h[0] for f, h in hues.items()}
    assert min(apart(centers[a], centers[b]) for a in centers for b in centers if a != b) > 45


def test_the_rebalance_table_carries_the_turnover(page):
    bt = page.bt
    log = _table(page.at, "Month-end check")
    assert bt.simulation.rebalances, "premise: the policy rebalanced"
    assert list(log.columns) == ["Sleeves outside their bands", "Turnover"]
    assert [[i, *v] for i, v in zip(log.index, log.values.tolist())] == [
        [_month(r.month), ", ".join(r.triggers), _pct(r.turnover)]
        for r in bt.simulation.rebalances]


def test_the_sleeves_on_their_own_are_run_s_at_full_height(page):
    bt = page.bt
    last = _month(bt.returns.index[-1])
    table = next(t for t in _tables(page.at)
                 if t.index.name == "Sleeve" and "Volatility" in t.columns)
    assert list(table.index) == [s.name for s in bt.sleeves]
    assert list(table.columns) == ["Annualized return", "Volatility", "Deepest drawdown",
                                   "Correlation with S&P 500 (SPY)"]
    spy = bt.returns[pb.SP500]
    for row, s in zip(table.values.tolist(), bt.sleeves):
        held = bt.simulation.sleeve_returns[s.name]
        st = bt.sleeve_statistics[s.name]
        dd = st.drawdown
        assert row == [
            _pct((1 + held).prod() ** (12 / len(held)) - 1),
            _pct(held.std(ddof=1) * math.sqrt(12)),
            f"{_pct(dd.depth)}: {_month(dd.peak)} to {_month(dd.trough)}, "
            + (f"recovered {_month(dd.recovered)}" if dd.recovered else f"not recovered by {last}"),
            f"{held.corr(spy):.2f}",
        ], s.name
    # Static, so every row and column shows; the proxy table scrolls sideways but shows
    # every row.
    assert [t.value.index.name for t in page.at.table] == ["Series", "Comparison", "Sleeve"]
    calls = [t for t in page.tables if t["index"] == "Sleeve"]
    assert [(t["rows"], t["height"]) for t in calls] == [
        (sum(len(s.mix) for s in bt.sleeves), "content")]


def test_the_proxy_table_reads_the_policy_series_json_and_the_history_file(page):
    """Each row: the sleeve's target and band, its fund, the series used, and the fund's
    tracking against its proxy, computed here from the file: over the months the history
    file holds both, and for a splice from its splice month (decided by the owner on
    October 19, 2026)."""
    bt, spec = page.bt, page.spec
    history = pb.load_history()
    table = next(t for t in _tables(page.at) if t.index.name == "Sleeve"
                 and "Measured over" in t.columns)
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
                months = history
                if entry["reason"] == "index change":
                    splice = (pd.Period(entry["proxy_until"], "M") + 1).strftime("%Y-%m")
                    months = history.loc[history.index >= splice]
                both = months[[fund, proxy]].dropna()
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


def test_a_splice_is_measured_from_its_splice_month_and_the_caption_names_each_window(page):
    """Decided by the owner on October 19, 2026: SPHQ's statistics run from July 2010 and
    IDHQ's from March 2012, when each began following a quality index, to the last
    month. The caption is kept as it was."""
    bt, spec = page.bt, page.spec
    history = pb.load_history()
    last = history.index[-1]
    assert (bt.tracking["SPHQ"].first, bt.tracking["SPHQ"].last) == ("2010-07", last)
    assert (bt.tracking["IDHQ"].first, bt.tracking["IDHQ"].last) == ("2012-03", last)
    for fund, t in bt.tracking.items():
        if spec["funds"][fund]["reason"] == "inception":
            assert t.first == history[[fund, t.proxy]].dropna().index[0], fund
    caption = next(c.value for c in page.at.caption
                   if c.value.startswith("Hypothetical backtest inputs."))
    assert caption == (
        "Hypothetical backtest inputs. Tracking statistics compare each fund with its "
        "proxy over the months the history file holds both; a dash marks a fund with no "
        "proxy, or a statistic the overlap cannot give. A splice is measured from the "
        "month its fund began following a quality index, against the strategy the sleeve "
        f"holds: SPHQ July 2010 to {_month(last)} and IDHQ March 2012 to {_month(last)}.")


def test_sources_and_method_are_expanders_under_the_sleeve_table_and_the_data_is_last(page):
    bt, spec = page.bt, page.spec
    at = page.at
    assert [e.label for e in at.expander] == ["Sources", "Method"]
    sources = [m.value for m in at.expander[0].markdown if m.value.startswith("- **")]
    held = list(dict.fromkeys(f for s in bt.sleeves for f, _w in s.mix))
    for fund in held:
        entry = spec["funds"][fund]
        line = next(l for l in sources if l.startswith(f"- **{fund}**: "))
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
    assert any(l.startswith("- **AGG**") for l in sources)
    assert any(l.startswith(f"- **{spec['bill']['series']}**") for l in sources)
    shown = _elements(at)
    last_heading = [n for n in shown if n.type == "subheader"][-1]
    assert last_heading.value == "The data"
    last = _month(bt.returns.index[-1])
    assert any(f"which runs through {last};" in c.value and f"`{pb.REFRESH_COMMAND}`" in c.value
               for c in at.caption)


def test_the_method_cites_morningstar_with_its_date(page):
    """A methodology claim says what its source says, cited with the source's date: up
    markets "with returns at or above 0%", down markets "less than 0" (Custom Calculation
    Data Points, October 2016, pages 29 and 7)."""
    notes = [m.value for m in page.at.expander[1].markdown]
    capture = next(n for n in notes if n.startswith("- Up-capture is "))
    assert pb.CAPTURE_SOURCE["url"] in capture and pb.CAPTURE_SOURCE["date"] in capture
    assert "“markets with returns at or above 0%”" in capture
    assert "“in which the market return is less than 0”" in capture


# ── No volatility (#672) ─────────────────────────────────────────────────────

SERIES_ROWS = dict(zip(ROWS, (pb.POLICY, pb.SP500, pb.SIXTY_FORTY)))


@pytest.mark.parametrize("case", ["one month", "SPY never moves"])
def test_with_no_volatility_the_page_prints_a_dash_and_the_reason(case, tmp_path):
    """Decided by the owner on October 19, 2026 (#672): with fewer than two monthly
    returns, or none that differ, the volatility and the Sharpe ratio each print "—"
    with the engine's reason; every other figure is run()'s as before. The demo book,
    offline, served a history cut to its first backtest month, or with SPY's return a
    constant 1% a month."""
    history = pb.load_history()
    if case == "one month":
        history = history.loc[:"2006-03"]
        expect = {label: pb.FEWER_THAN_TWO_MONTHS for label in SERIES_ROWS}
    else:
        history = history.copy()
        history["SPY"] = 0.01
        expect = {"S&P 500 (SPY)": pb.NO_VOLATILITY}
    with pytest.MonkeyPatch.context() as mp:
        shown = _render(mp, tmp_path, "demo", history=history)
    assert not shown.at.exception, shown.at.exception
    bt = shown.bt
    table = _table(shown.at, "Series")
    for label, name in SERIES_ROWS.items():
        st, row = bt.statistics[name], table.loc[label]
        assert row["Annualized return"] == _pct(st.annual_return)
        if label in expect:
            assert st.no_volatility == expect[label]
            assert row["Volatility"] == row["Sharpe ratio"] == f"— ({expect[label]})"
        else:
            assert st.no_volatility is None
            assert row["Volatility"] == _pct(st.annual_volatility)
            assert row["Sharpe ratio"] == f"{st.sharpe:.2f}"
    for read in READS:
        assert read(bt).replace("$", "\\$") in _paragraphs(shown.at), read.__name__
    if case == "one month":
        assert list(bt.returns.index) == ["2006-03"]
        assert [(m.label, m.value) for m in shown.at.metric][1:3] == [
            ("Volatility", "—"), ("Sharpe ratio", "—")]
        captions = [c.value for c in shown.at.caption]
        assert any(c.startswith("Each series' hypothetical figures, March 2006 to March "
                                "2006, 1 month.") for c in captions), captions
        assert ("No rolling 5-year return is shown: the history holds 1 month from March 2006 "
                "to March 2006, fewer than 60.") in _paragraphs(shown.at)
        assert not [c for c in _charts(shown.at)
                    if c["layout"]["title"]["text"].startswith("Rolling")]


# ── The personal book, on a scratch copy ─────────────────────────────────────

@pytest.mark.skipif(not PERSONAL.exists(), reason="no personal book here (CI)")
def test_a_scratch_copy_of_the_personal_book_renders(tmp_path):
    """Structure only: no figure of the personal book is read or asserted."""
    with pytest.MonkeyPatch.context() as mp:
        shown = _render(mp, tmp_path, "personal")
    assert not shown.at.exception, shown.at.exception
    alert = [n for n in _elements(shown.at) if _text(n)][1]
    assert alert.type == "info" and alert.value.startswith(
        "**Hypothetical, back-tested results:**")
    assert "They are not this portfolio's record, and no account earned them." in alert.value
    assert [t.value for t in shown.at.title] == ["Policy Backtest (hypothetical)"]
    assert len(_charts(shown.at)) == 5
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
