"""Policy Backtest — the book's own SAA policy applied to history (#667), rebuilt for
readability and analysis as the owner decided on October 22, 2026.

Hypothetical everywhere: the disclosure comes first, above the title, and "hypothetical"
is in the page title, every chart title and once in every table caption, never in a
section heading. Every figure and date is read from src.policy_backtest.run() and
data/backtest/series.json; none is typed. Each paragraph is the engine's, and no sentence
compares the policy with the S&P 500 or the 60/40 except by stating both figures.

Each book runs its own policy (the owner's decision of October 18, 2026, recorded on
#667). The page fetches nothing, writes nothing and enters no quarter lock, and the
quarterly PDF does not include it (tests/render/test_policy_backtest_page.py).
"""
import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Policy Backtest (hypothetical)", layout="wide")

from src import policy_backtest as pb
from src.benchmarks import naive_60_40_label, naive_60_40_legs
from src.config import IS_DEMO
from src.prose_helpers import _list
from src.ui_helpers import render_footer, render_page_header
render_page_header()

# The app's three-series palette and dashes (pages/2_Performance.py), so a line's
# identity never rests on its color alone. The three time charts share it, one height
# and one span of months, so a band sits at the same place on each.
_LINES = {
    pb.POLICY:      dict(color="#2E4057", width=2.5),
    pb.SP500:       dict(color="#B8742A", width=1.5, dash="dot"),
    pb.SIXTY_FORTY: dict(color="#5C7A5C", width=1.5, dash="dash"),
}
_HEIGHT = 380
_SERIES_LABEL = {
    pb.POLICY: "Policy",
    pb.SP500: pb.SP500,
    pb.SIXTY_FORTY: f"{naive_60_40_label()} ({naive_60_40_legs()})",
}
# The calendar table's columns, short enough for every column to show; the difference
# columns name each comparison without its ticker.
_COLUMN = {pb.POLICY: "Policy", pb.SP500: pb.SP500, pb.SIXTY_FORTY: naive_60_40_label()}
_SHORT = {pb.SP500: "S&P 500", pb.SIXTY_FORTY: naive_60_40_label()}
# The growth chart's only ticks, as multiples of GROWTH_OF: $5,000 to $100,000.
_GROWTH_TICKS = (0.5, 1, 2, 5, 10)
# The stress episodes' bands, light and behind the lines.
_BAND = "rgba(46, 64, 87, 0.09)"
# The weights chart's families, each one hue from dark to light (the policy's order
# within a family runs dark to light). A sleeve outside the four is gray.
_FAMILIES = {
    "US equity":            ("#1F3A5F", "#C3D4E8"),
    "International equity": ("#2F5233", "#C8DCC8"),
    "Fixed income":         ("#8C5A1E", "#E8C690"),
    "Real assets":          ("#5E3A6E", "#C9B0D4"),
    "Other":                ("#555555", "#CCCCCC"),
}


def _md(text: str) -> str:
    """A paragraph for st.markdown: a dollar sign is a dollar, not the start of math."""
    return text.replace("$", "\\$")


def _month_end(months) -> pd.DatetimeIndex:
    """'YYYY-MM' labels as each month's last day, for a chart's x axis."""
    return pd.PeriodIndex(list(months), freq="M").to_timestamp(how="end").normalize()


def _chart(title: str, paths: "dict[str, pd.Series]", span, *, log: bool = False,
           money: bool = False) -> go.Figure:
    """One of the three time charts: the three series over ``span`` (two month-ends)."""
    fig = go.Figure()
    for name, path in paths.items():
        hover = "$%{y:,.0f}" if money else "%{y:.2f}%"
        fig.add_trace(go.Scatter(
            x=_month_end(path.index), y=path.values, name=_SERIES_LABEL[name],
            line=_LINES[name], mode="lines",
            hovertemplate=f"<b>{_SERIES_LABEL[name]}</b><br>%{{x|%B %Y}}<br>{hover}<extra></extra>",
        ))
    if log:
        # Every tick shows: the axis spans them all, and every value when one lies outside.
        ticks = [pb.GROWTH_OF * k for k in _GROWTH_TICKS]
        low = min([ticks[0], *(float(p.min()) for p in paths.values())])
        high = max([ticks[-1], *(float(p.max()) for p in paths.values())])
        yaxis = dict(type="log", tickmode="array", tickvals=ticks,
                     ticktext=[pb.money(t) for t in ticks],
                     range=[math.log10(low) - 0.03, math.log10(high) + 0.03])
    else:
        yaxis = dict(ticksuffix="%", zeroline=True, zerolinecolor="#CCCCCC")
    fig.update_layout(
        title=dict(text=title, x=0, xanchor="left", font=dict(size=15)),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0),
        margin=dict(l=0, r=0, t=70, b=0),
        height=_HEIGHT,
        hovermode="x unified",
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(family="sans-serif", size=12, color="#333"),
        xaxis=dict(gridcolor="#E8E8E8", automargin=True, range=list(span)),
        yaxis=dict(gridcolor="#E8E8E8", automargin=True, **yaxis),
    )
    return fig


def _bands(fig: go.Figure, bt: pb.Backtest) -> None:
    """The policy's stress episodes, each a band from its peak to its trough labeled
    with its year; one band labeled with both years when two years share a fall."""
    spans: "dict[tuple[str, str], list[str]]" = {}
    for year, e in bt.statistics[pb.POLICY].episodes.items():
        if e is not None:
            spans.setdefault((e.peak, e.trough), []).append(str(year))
    for (peak, trough), years in spans.items():
        x0, x1 = _month_end([peak, trough])
        fig.add_vrect(x0=x0, x1=x1, fillcolor=_BAND, line_width=0, layer="below",
                      annotation_text=", ".join(years), annotation_position="top left",
                      annotation_font=dict(size=11, color="#555555"))


def _family(name: str, parent: "str | None") -> str:
    """A sleeve's family, read from the book: its parent asset class, with the equity
    sleeves split as sleeve_config.international_sleeves splits them, by name."""
    if parent == "Equity":
        return "US equity" if name.startswith("US") else "International equity"
    return {"Income": "Fixed income", "Real Assets": "Real assets"}.get(parent, "Other")


def _shade(dark: str, light: str, t: float) -> str:
    a, b = (tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in (dark, light))
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(a, b))


def _sleeve_colors(bt: pb.Backtest) -> "dict[str, str]":
    """Each sleeve's color: its family's hue, stepped from dark to light in the policy's
    order."""
    from src import policy
    rows = policy.asset_classes()
    names = {r["asset_class_id"]: r["name"] for r in rows}
    parent = {r["name"]: names.get(r["parent_id"]) for r in rows}
    members: "dict[str, list[str]]" = {}
    for s in bt.sleeves:
        members.setdefault(_family(s.name, parent.get(s.name)), []).append(s.name)
    out = {}
    for family, sleeves in members.items():
        dark, light = _FAMILIES[family]
        for i, name in enumerate(sleeves):
            out[name] = _shade(dark, light, 0.75 * i / max(len(sleeves) - 1, 1))
    return out


def _fall_cell(dd: pb.Drawdown, last: str) -> str:
    """'-49.04%: October 2007 to February 2009, recovered April 2011'."""
    if dd.depth >= 0:
        return "No fall"
    back = (f"recovered {pb.month_name(dd.recovered)}" if dd.recovered else
            f"not recovered by {pb.month_name(last)}")
    return f"{pb.pct(dd.depth)}: {pb.month_name(dd.peak)} to {pb.month_name(dd.trough)}, {back}"


spec = pb.load_spec()
try:
    bt = pb.run(spec=spec)
except pb.PolicyBacktestError as exc:
    # The disclosure still comes first: the page shows no result, and says why.
    st.info("This page shows **hypothetical, back-tested results** of this book's SAA "
            f"policy. They cannot be computed for this book ({exc}).")
    render_footer()
    st.stop()

FIRST, LAST = bt.returns.index[0], bt.returns.index[-1]
WINDOW = f"{pb.month_name(FIRST)} to {pb.month_name(LAST)}"
N = len(bt.returns)
BASE = pb.wealth(bt.returns[pb.POLICY]).index[0]
SPAN = (_month_end([BASE])[0], _month_end([LAST])[0])
POLICY_STATS = bt.statistics[pb.POLICY]

# ── The disclosure, above everything ─────────────────────────────────────────
st.info(pb.disclosure(bt, spec, IS_DEMO))

_, col, _ = st.columns([1, 8, 1])
with col:
    st.title("Policy Backtest (hypothetical)")
    st.caption(f"This book's SAA policy beside the {_SERIES_LABEL[pb.SP500]} and the "
               f"{_SERIES_LABEL[pb.SIXTY_FORTY]}, {WINDOW}; the tiles are the policy's.")

    # ── The policy in five tiles ─────────────────────────────────────────────
    vol, sharpe = POLICY_STATS.annual_volatility, POLICY_STATS.sharpe
    tiles = st.columns(5)
    tiles[0].metric("Annualized return", pb.pct(POLICY_STATS.annual_return))
    tiles[1].metric("Volatility", pb.pct(vol) if vol is not None else "—")
    tiles[2].metric("Sharpe ratio", f"{sharpe:.2f}" if sharpe is not None else "—")
    tiles[3].metric("Maximum drawdown", pb.pct(POLICY_STATS.drawdown.depth))
    tiles[4].metric(f"\\${pb.GROWTH_OF:,} became", pb.money(POLICY_STATS.ending_value))

    # ── Return and risk ──────────────────────────────────────────────────────
    st.subheader("Return and risk")
    rows = []
    for name, s in bt.statistics.items():
        rows.append({
            "Series": _SERIES_LABEL[name],
            "Annualized return": pb.pct(s.annual_return),
            # With no volatility (#672): a dash and the engine's reason, in both cells.
            "Volatility": (f"— ({s.no_volatility})" if s.no_volatility
                           else pb.pct(s.annual_volatility)),
            "Sharpe ratio": (f"— ({s.no_volatility})" if s.no_volatility
                             else f"{s.sharpe:.2f}"),
            "Deepest drawdown": _fall_cell(s.drawdown, LAST),
            "Worst month": (f"{pb.pct(s.worst_month.value, signed=True)} "
                            f"({pb.month_name(s.worst_month.month)})"),
            "Positive months": (f"{pb.pct(s.positive_share)} "
                                f"({round(s.positive_share * N)} of {N})"),
            "Ending value": pb.money(s.ending_value),
        })
    # The short tables are static, so every column shows and long cells wrap.
    st.table(pd.DataFrame(rows).set_index("Series"))
    st.caption(
        f"Each series' hypothetical figures, {WINDOW}, {pb.count(N, 'month')}. Volatility is "
        "the sample standard deviation of the monthly returns × √12. The Sharpe ratio is "
        f"measured against {spec['bill']['name']} (FRED {spec['bill']['series']}). A "
        "drawdown runs from a month-end high to the lowest month-end before the high is "
        "regained, and is recovered at the first month-end back at that high. A positive "
        f"month returned above 0%. Ending value is what \\${pb.GROWTH_OF:,} placed at the "
        f"end of {pb.month_name(BASE)} became."
    )

    # ── Against each comparison ──────────────────────────────────────────────
    st.subheader("Against each comparison")

    def _ratio(x, as_pct: bool = False) -> str:
        if x is None:
            return "—"
        return pb.pct(x) if as_pct else f"{x:.2f}"

    st.table(pd.DataFrame([{
        "Comparison": _SERIES_LABEL[name],
        "Active return": pb.pct(c.active_return, signed=True),
        "Beta": _ratio(c.beta),
        "Correlation": _ratio(c.correlation),
        "Up-capture": _ratio(c.up_capture, as_pct=True),
        "Down-capture": _ratio(c.down_capture, as_pct=True),
        "Tracking error": _ratio(c.tracking_error, as_pct=True),
    } for name, c in bt.comparisons.items()]).set_index("Comparison"))
    st.caption(
        f"The policy's hypothetical monthly returns beside each comparison's, {WINDOW}. "
        "Active return is the policy's annualized return minus the comparison's. Up- and "
        "down-capture are defined, with Morningstar's source, in the method note below. "
        "A dash marks a figure the months cannot give."
    )
    st.markdown(_md(pb.summary_read(bt)))

    # ── Growth ───────────────────────────────────────────────────────────────
    st.subheader("Growth")
    growth = {n: pb.wealth(bt.returns[n]) * pb.GROWTH_OF for n in bt.returns.columns}
    fig = _chart(f"Growth of ${pb.GROWTH_OF:,}, hypothetical (log scale)", growth, SPAN,
                 log=True, money=True)
    _bands(fig, bt)
    st.plotly_chart(fig, width="stretch")
    st.markdown(_md(pb.growth_read(bt)))

    # ── Drawdowns ────────────────────────────────────────────────────────────
    st.subheader("Drawdowns")
    falls = {n: pb.drawdowns(bt.returns[n]) * 100 for n in bt.returns.columns}
    fig = _chart("Drawdown from the previous high, hypothetical", falls, SPAN)
    _bands(fig, bt)
    deepest = POLICY_STATS.drawdown
    if deepest.depth < 0:
        fig.add_annotation(
            x=_month_end([deepest.trough])[0], y=deepest.depth * 100,
            text=f"Policy {pb.pct(deepest.depth)}, {pb.month_name(deepest.trough)}",
            showarrow=True, arrowhead=0, arrowcolor="#555555", ax=70, ay=0,
            xanchor="left", font=dict(size=11, color="#333333"),
            bgcolor="rgba(255, 255, 255, 0.85)")
    st.plotly_chart(fig, width="stretch")

    years = list(POLICY_STATS.episodes)
    if years:
        rows = []
        for y in years:
            for name in bt.returns.columns:
                e = bt.statistics[name].episodes[y]
                rows.append({
                    "Episode": str(y),
                    "Series": _SERIES_LABEL[name],
                    "Fall": pb.pct(e.depth) if e else "No fall reaches into it",
                    "Peak": pb.month_name(e.peak) if e else "—",
                    "Trough": pb.month_name(e.trough) if e else "—",
                    "Months to trough": str(e.months_to_trough) if e else "—",
                    "Recovered": ("—" if e is None else pb.month_name(e.recovered)
                                  if e.recovered else f"Not by {pb.month_name(LAST)}"),
                })
        st.dataframe(pd.DataFrame(rows).set_index("Episode"), width="stretch",
                     height="content")
        st.caption(
            f"Each series' deepest hypothetical fall reaching into {_list([str(y) for y in years])}: "
            "from a month-end high (the peak) to the lowest month-end before that high is "
            "regained (the trough), and the first month-end back at it (recovered). A fall "
            "reaches into a year when a month of that year lies between its peak and its "
            "trough, both included. The shaded bands are the policy's."
        )
    st.markdown(_md(pb.drawdown_read(bt)))

    # ── Rolling returns ──────────────────────────────────────────────────────
    label = pb.rolling_label()
    st.subheader(f"Rolling {label} returns")
    if bt.rolling.windows:
        rolling = {n: pb.rolling_annual(bt.returns[n]) * 100 for n in bt.returns.columns}
        fig = _chart(f"Rolling {label} annualized return, hypothetical", rolling, SPAN)
        fig.add_hline(y=0, line_width=1, line_color="#888888")
        _bands(fig, bt)
        st.plotly_chart(fig, width="stretch")
    st.markdown(_md(pb.rolling_read(bt)))

    # ── Calendar years ───────────────────────────────────────────────────────
    st.subheader("Calendar years")
    years = list(POLICY_STATS.calendar_years)
    partial = set(POLICY_STATS.partial_years)
    fig = go.Figure()
    for name in bt.returns.columns:
        fig.add_trace(go.Bar(
            x=[str(y) for y in years],
            y=[bt.statistics[name].calendar_years[y] * 100 for y in years],
            name=_SERIES_LABEL[name],
            marker=dict(color=_LINES[name]["color"],
                        pattern=dict(shape=["/" if y in partial else "" for y in years],
                                     fillmode="overlay", fgcolor="white", solidity=0.35)),
            hovertemplate=(f"<b>{_SERIES_LABEL[name]}</b><br>%{{x}}<br>%{{y:+.2f}}%"
                           "<extra></extra>"),
        ))
    fig.update_layout(
        title=dict(text="Calendar-year returns, hypothetical (hatched: a partial year)",
                   x=0, xanchor="left", font=dict(size=15)),
        barmode="group",
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0),
        margin=dict(l=0, r=0, t=70, b=0),
        height=_HEIGHT,
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(family="sans-serif", size=12, color="#333"),
        xaxis=dict(type="category", tickmode="array", tickvals=[str(y) for y in years],
                   ticktext=[f"{y}<br>partial" if y in partial else str(y) for y in years]),
        yaxis=dict(gridcolor="#E8E8E8", ticksuffix="%", zeroline=True,
                   zerolinecolor="#888888", automargin=True),
    )
    st.plotly_chart(fig, width="stretch")

    table = []
    for y in years:
        mine = bt.statistics[pb.POLICY].calendar_years[y]
        row = {"Year": pb.year_label(y, bt)}
        row.update({_COLUMN[n]: pb.pct(bt.statistics[n].calendar_years[y], signed=True)
                    for n in bt.returns.columns})
        row.update({f"Policy − {_SHORT[n]}":
                    f"{(mine - bt.statistics[n].calendar_years[y]) * 100:+.2f} pp"
                    for n in bt.comparisons})
        table.append(row)
    # The Year column fits its longest label, about 6.5 pixels a character.
    labels = [r["Year"] for r in table]
    st.dataframe(pd.DataFrame(table).set_index("Year"), width="stretch", height="content",
                 column_config={"_index": st.column_config.Column(
                     "Year", width=round(6.5 * max(map(len, labels))) + 24)})
    st.caption(
        "Each calendar year's hypothetical return: the months the backtest holds in it, "
        f"compounded. The {naive_60_40_label()} is {naive_60_40_legs()}. The last two "
        "columns are the policy's return minus each comparison's, in percentage points. A "
        "partial year names its months."
    )
    st.markdown(_md(pb.calendar_read(bt)))

    # ── Sleeve weights over time ─────────────────────────────────────────────
    st.subheader("Sleeve weights over time")
    starts = bt.simulation.start_weights
    weights = pd.DataFrame({s.name: starts[[f"{s.name}/{f}" for f, _w in s.mix]].sum(axis=1)
                            for s in bt.sleeves})
    # Each month's start is the month-end before it.
    starts_at = (pd.PeriodIndex(list(weights.index), freq="M") - 1).to_timestamp(
        how="end").normalize()
    colors = _sleeve_colors(bt)
    fig = go.Figure()
    for s in bt.sleeves:
        fig.add_trace(go.Scatter(
            x=starts_at, y=weights[s.name] * 100, name=s.name, stackgroup="weights", mode="lines",
            line=dict(width=0.5, color="white"), fillcolor=colors[s.name],
            hovertemplate=f"<b>{s.name}</b><br>%{{x|%B %Y}}<br>%{{y:.1f}}%<extra></extra>",
        ))
    for r in bt.simulation.rebalances:
        fig.add_vline(x=_month_end([r.month])[0], line_width=1, line_color="#333333",
                      opacity=0.55)
    fig.update_layout(
        title=dict(text="Sleeve weights at each month's start, hypothetical (lines: "
                        "rebalances)", x=0, xanchor="left", font=dict(size=15)),
        legend=dict(orientation="v", traceorder="normal", x=1.01, xanchor="left", y=1.0),
        margin=dict(l=0, r=0, t=50, b=0),
        height=460,
        hovermode="x unified",
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(family="sans-serif", size=12, color="#333"),
        xaxis=dict(gridcolor="#E8E8E8", automargin=True, range=list(SPAN)),
        yaxis=dict(gridcolor="#E8E8E8", ticksuffix="%", range=[0, 100], automargin=True),
    )
    st.plotly_chart(fig, width="stretch")

    if bt.simulation.rebalances:
        st.dataframe(pd.DataFrame([
            {"Month-end check": pb.month_name(r.month),
             "Sleeves outside their bands": ", ".join(r.triggers),
             "Turnover": pb.pct(r.turnover)}
            for r in bt.simulation.rebalances]).set_index("Month-end check"),
            width="stretch", height="content")
        st.caption(
            "Each hypothetical rebalance: the month-end check that found a sleeve outside "
            "its band, the sleeves outside, and the turnover, half the sum of each sleeve's "
            "distance from its target just before the reset."
        )
    st.markdown(_md(pb.rebalance_read(bt)))

    # ── Sleeves on their own ─────────────────────────────────────────────────
    st.subheader("Sleeves on their own")
    st.table(pd.DataFrame([{
        "Sleeve": name,
        "Annualized return": pb.pct(s.annual_return),
        "Volatility": (f"— ({s.no_volatility})" if s.no_volatility
                       else pb.pct(s.annual_volatility)),
        "Deepest drawdown": _fall_cell(s.drawdown, LAST),
        f"Correlation with {pb.SP500}": _ratio(s.correlation),
    } for name, s in bt.sleeve_statistics.items()]).set_index("Sleeve"))
    two_funds = any(len(s.mix) > 1 for s in bt.sleeves)
    st.caption(
        f"Each sleeve's hypothetical monthly returns as the backtest held it, {WINDOW}: its "
        "fund, with the series the table below names standing in before it"
        + (", and a sleeve of two funds as they drifted between rebalances" if two_funds
           else "")
        + f". Correlation is with the {pb.SP500}'s monthly returns over the same months."
    )

    # ── Sleeves, funds and proxies ───────────────────────────────────────────
    st.subheader("Sleeves, funds and proxies")
    sleeve_rows, splices = [], []
    for s in bt.sleeves:
        for fund, share in s.mix:
            t = bt.tracking.get(fund)
            since = pb.tracking_from(fund, spec)
            if t is not None and since is not None:
                splices.append(
                    f"{fund} {pb.month_name(t.first)} to {pb.month_name(t.last)}" if t.first
                    else f"{fund} from {pb.month_name(since)}, where the history holds no "
                         "month of both")
            sleeve_rows.append({
                "Sleeve": s.name,
                "Target": f"{s.target * 100:.1f}%",
                "Band": f"±{s.band * 100:.1f} pp",
                "Fund": fund if len(s.mix) == 1 else f"{fund} ({share * 100:.0f}% of the sleeve)",
                "Series used": pb.series_used(fund, bt, spec),
                "Measured over": (f"{pb.month_name(t.first)} to {pb.month_name(t.last)}"
                                  if t is not None and t.first else "—"),
                "Months": str(t.months) if t is not None else "—",
                "Tracking difference": (pb.pct(t.difference, signed=True)
                                        if t is not None and t.difference is not None else "—"),
                "Tracking error": (pb.pct(t.error)
                                   if t is not None and t.error is not None else "—"),
                "Correlation": (f"{t.correlation:.3f}"
                                if t is not None and t.correlation is not None else "—"),
            })
    st.dataframe(pd.DataFrame(sleeve_rows).set_index("Sleeve"), width="stretch",
                 height="content")
    st.caption(
        "Hypothetical backtest inputs. Tracking statistics compare each fund with its "
        "proxy over the months the history file holds both; a dash marks a fund with no "
        "proxy, or a statistic the overlap cannot give."
        + (" A splice is measured from the month its fund began following a quality "
           f"index, against the strategy the sleeve holds: {_list(splices)}."
           if splices else "")
    )
    with st.expander("Sources"):
        st.markdown("As each issuer printed them, with the source's date:")
        for line in pb.source_lines(bt, spec):
            st.markdown(f"- {line}")

    sleeves_with_mix = [s for s in bt.sleeves if len(s.mix) > 1]
    with st.expander("Method", expanded=False):
        notes = [
            f"The policy starts at its targets at the end of {pb.month_name(BASE)}. Each "
            "month, each holding earns its fund's total return for that month from the "
            "committed monthly history, and its proxy's before the fund's own record is "
            "used (the sleeve table names each).",
            "At each month-end check a sleeve is outside its band when its weight differs "
            "from its target by more than the band; a weight exactly at the band stays "
            "inside. When any sleeve is outside, every sleeve goes back to its target. The "
            "turnover at a rebalance is half the sum of the absolute differences between "
            "each sleeve's weight just before the reset and its target.",
        ]
        for s in sleeves_with_mix:
            split = " and ".join(f"{w * 100:.0f}% {f}" for f, w in s.mix)
            notes.append(f"{s.name} is held as {split}, reset to that split only when the "
                         "portfolio rebalances; between rebalances the funds drift.")
        notes += [
            "No contributions, trading costs or taxes.",
            f"The {_SERIES_LABEL[pb.SP500]} is SPY's total return. The "
            f"{_SERIES_LABEL[pb.SIXTY_FORTY]} is reset to those weights at each calendar "
            f"quarter's end, as the app's {naive_60_40_label()} is, and bought at the "
            "policy's start.",
            "Annualized return is the geometric average of the monthly returns. Volatility "
            "is their sample standard deviation × √12. The Sharpe ratio is the annualized "
            f"return minus the bill's ({spec['bill']['series']}) annualized return over the "
            "same months, divided by volatility; the bill's month is (1 + the month's "
            "average daily yield)^(1/12) − 1.",
            "A drawdown is a fall of month-end value from an earlier month-end high; the "
            "deepest is the maximum drawdown, and it is recovered at the first month-end at "
            "or above that high again. A year's stress episode is the deepest fall whose "
            "months from peak to trough, both included, include a month of that year. The "
            "bands on the charts are the policy's.",
            "Active return is the policy's annualized return minus the comparison's. Beta is "
            "the covariance of the two monthly series divided by the comparison's variance; "
            "correlation is of the two monthly series; tracking error is the sample standard "
            "deviation of the monthly difference × √12.",
            "Up-capture is the policy's annualized compound return over the months the "
            "comparison returned at or above 0%, divided by the comparison's own over those "
            "months; down-capture is the same over the months it returned below 0%. "
            f"{pb.CAPTURE_SOURCE['name']} ([{pb.CAPTURE_SOURCE['url'].split('/')[2]}]"
            f"({pb.CAPTURE_SOURCE['url']}), {pb.CAPTURE_SOURCE['date']}) defines the up "
            "capture ratio over “markets with returns at or above 0%” and the down capture "
            "ratio over the periods “in which the market return is less than 0”.",
            f"A rolling {label} return is the annualized return over the "
            f"{pb.ROLLING_MONTHS} months ending each month. A window counts as below a "
            "comparison's only when its return is strictly below it.",
            "A sleeve on its own is its holdings' value at each month's end divided by their "
            "value at its start, as the backtest held them, compounded over the same months.",
            "Tracking statistics are over the months the history holds both the fund and "
            "its proxy; for a fund spliced at an index change, from the month it began "
            "following a quality index. Tracking difference is the fund's annualized return "
            "minus the proxy's; tracking error is the sample standard deviation of the "
            "monthly difference × √12; correlation is of the two monthly series.",
        ]
        for note in notes:
            st.markdown(f"- {note}")

    # ── The data ─────────────────────────────────────────────────────────────
    st.subheader("The data")
    st.caption(
        f"The results are computed from data/backtest/monthly_returns.csv, which runs "
        f"through {pb.month_name(LAST)}; data/backtest/series.json names each proxy. "
        f"Refresh it with `{pb.REFRESH_COMMAND}`, run with the network up and FRED_API_KEY "
        "set, then review the diff and commit it."
    )

    render_footer()
