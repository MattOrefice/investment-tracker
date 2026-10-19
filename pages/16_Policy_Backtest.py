"""Policy Backtest — the book's own SAA policy applied to history (#667, PR 3 of 3).

Hypothetical everywhere: the disclosure comes first, above the title, and every chart
title, table caption and the method note says so. Every figure and date is read from
src.policy_backtest.run() and data/backtest/series.json; none is typed. No sentence
compares the policy with the S&P 500 or the 60/40 except by stating the figures.

Each book runs its own policy (the owner's decision of October 18, 2026, recorded on
#667). The page fetches nothing, writes nothing and enters no quarter lock, and the
quarterly PDF does not include it (tests/render/test_policy_backtest_page.py).
"""
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
# identity never rests on its color alone.
_LINES = {
    pb.POLICY:      dict(color="#2E4057", width=2.5),
    pb.SP500:       dict(color="#B8742A", width=1.5, dash="dot"),
    pb.SIXTY_FORTY: dict(color="#5C7A5C", width=1.5, dash="dash"),
}
_SERIES_LABEL = {
    pb.POLICY: "Policy",
    pb.SP500: pb.SP500,
    pb.SIXTY_FORTY: f"{naive_60_40_label()} ({naive_60_40_legs()})",
}


def _pct(x: float, signed: bool = False) -> str:
    return f"{x * 100:+.2f}%" if signed else f"{x * 100:.2f}%"


def _months(n: int) -> str:
    """'1 month', '247 months'."""
    return f"{n} month" if n == 1 else f"{n} months"


def _month_end(months) -> pd.DatetimeIndex:
    """'YYYY-MM' labels as each month's last day, for a chart's x axis."""
    return pd.PeriodIndex(list(months), freq="M").to_timestamp(how="end").normalize()


def _chart(title: str, paths: "dict[str, pd.Series]", *, log: bool = False,
           money: bool = False) -> go.Figure:
    fig = go.Figure()
    for name, path in paths.items():
        hover = "$%{y:,.0f}" if money else "%{y:.2f}%"
        fig.add_trace(go.Scatter(
            x=_month_end(path.index), y=path.values, name=_SERIES_LABEL[name],
            line=_LINES[name], mode="lines",
            hovertemplate=f"<b>{_SERIES_LABEL[name]}</b><br>%{{x|%B %Y}}<br>{hover}<extra></extra>",
        ))
    fig.update_layout(
        title=dict(text=title, x=0, xanchor="left", font=dict(size=15)),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0),
        margin=dict(l=0, r=0, t=70, b=0),
        height=380,
        hovermode="x unified",
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(family="sans-serif", size=12, color="#333"),
        xaxis=dict(gridcolor="#E8E8E8", automargin=True),
        yaxis=dict(gridcolor="#E8E8E8", zeroline=True, zerolinecolor="#CCCCCC", automargin=True,
                   **(dict(type="log", tickprefix="$", tickformat=",.0f") if log else
                      dict(ticksuffix="%"))),
    )
    return fig


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

# ── The disclosure, above everything ─────────────────────────────────────────
st.info(pb.disclosure(bt, spec, IS_DEMO))

_, col, _ = st.columns([1, 8, 1])
with col:
    st.title("Policy Backtest (hypothetical)")
    st.caption(
        f"This book's SAA policy, {len(bt.sleeves)} sleeves with a target, rebalanced to "
        "target whenever a month-end check finds a sleeve outside its band, beside "
        f"{_SERIES_LABEL[pb.SP500]} and the {_SERIES_LABEL[pb.SIXTY_FORTY]}. "
        f"Hypothetical, {WINDOW}: the data runs through {pb.month_name(LAST)}."
    )

    sleeves_with_mix = [s for s in bt.sleeves if len(s.mix) > 1]
    with st.expander("Method (hypothetical results)", expanded=False):
        notes = [
            f"The policy starts at its targets at the end of "
            f"{pb.month_name(pb.wealth(bt.returns[pb.POLICY]).index[0])}. Each month, each "
            "holding earns its fund's total return for that month from the committed "
            "monthly history, and its proxy's before the fund's own record is used (the "
            "sleeve table names each).",
            "At each month-end check a sleeve is outside its band when its weight differs "
            "from its target by more than the band; a weight exactly at the band stays "
            "inside. When any sleeve is outside, every sleeve goes back to its target.",
        ]
        for s in sleeves_with_mix:
            split = " and ".join(f"{w * 100:.0f}% {f}" for f, w in s.mix)
            notes.append(f"{s.name} is held as {split}, reset to that split only when the "
                         "portfolio rebalances; between rebalances the funds drift.")
        notes += [
            "No contributions, trading costs or taxes.",
            f"{_SERIES_LABEL[pb.SP500]} is SPY's total return. The "
            f"{_SERIES_LABEL[pb.SIXTY_FORTY]} is reset to those weights at each calendar "
            f"quarter's end, as the app's {naive_60_40_label()} is, and bought at the "
            "policy's start.",
            "Annualized return is the geometric average of the monthly returns. Volatility "
            "is their sample standard deviation × √12. The Sharpe ratio is the annualized "
            f"return less the bill's ({spec['bill']['series']}) annualized return over the "
            "same months, divided by volatility; the bill's month is (1 + the month's "
            "average daily yield)^(1/12) − 1.",
            "Maximum drawdown is the deepest fall of month-end value from an earlier "
            "month-end high. Recovered is the first month-end at or above that high again.",
            "Tracking statistics are over the months the history holds both the fund and "
            "its proxy; for a fund spliced at an index change, from the month it began "
            "following a quality index. Tracking difference is the fund's annualized return "
            "less the proxy's; tracking error is the sample standard deviation of the monthly "
            "difference × √12; correlation is of the two monthly series.",
        ]
        for note in notes:
            st.markdown(f"- {note}")

    # ── Summary ──────────────────────────────────────────────────────────────
    st.subheader("Summary (hypothetical)")
    rows = []
    for name, stats in bt.statistics.items():
        dd = stats.drawdown
        fell = dd.depth < 0
        rows.append({
            "Series": _SERIES_LABEL[name],
            "Annualized return": _pct(stats.annual_return),
            # With no volatility (#672): a dash and the engine's reason, in both cells.
            "Annualized volatility": (f"— ({stats.no_volatility})" if stats.no_volatility
                                      else _pct(stats.annual_volatility)),
            "Sharpe ratio": (f"— ({stats.no_volatility})" if stats.no_volatility
                             else f"{stats.sharpe:.2f}"),
            "Maximum drawdown": _pct(dd.depth),
            "Peak": pb.month_name(dd.peak) if fell else "No fall",
            "Trough": pb.month_name(dd.trough) if fell else "No fall",
            "Recovered": ((pb.month_name(dd.recovered) if dd.recovered else
                           f"Not by {pb.month_name(LAST)}") if fell else "No fall"),
        })
    st.dataframe(pd.DataFrame(rows).set_index("Series"), width="stretch")
    st.caption(
        f"Hypothetical. {WINDOW}, {_months(len(bt.returns))}. The Sharpe ratio is measured "
        f"against {spec['bill']['name']} (FRED {spec['bill']['series']}). Peak, trough and "
        "recovered are month-ends."
    )

    # ── Charts ───────────────────────────────────────────────────────────────
    growth = {n: pb.wealth(bt.returns[n]) * pb.GROWTH_OF for n in bt.returns.columns}
    st.plotly_chart(_chart(f"Growth of ${pb.GROWTH_OF:,}, hypothetical (log scale)",
                           growth, log=True, money=True), width="stretch")
    st.caption(
        f"Hypothetical. Month-end value of \\${pb.GROWTH_OF:,} placed at the end of "
        f"{pb.month_name(pb.wealth(bt.returns[pb.POLICY]).index[0])}, with each month's "
        "return reinvested."
    )

    falls = {n: pb.drawdowns(bt.returns[n]) * 100 for n in bt.returns.columns}
    st.plotly_chart(_chart("Drawdown from the previous high, hypothetical", falls),
                    width="stretch")
    st.caption("Hypothetical. Each month-end's fall from the highest month-end value "
               "before it.")

    years_in_window = pb.ROLLING_MONTHS // 12
    window_label = (f"{years_in_window}-year" if pb.ROLLING_MONTHS % 12 == 0 else
                    f"{pb.ROLLING_MONTHS}-month")
    rolling = {n: pb.rolling_annual(bt.returns[n]) * 100 for n in bt.returns.columns}
    first_rolling = next(iter(rolling.values())).index
    if len(first_rolling):
        st.plotly_chart(_chart(f"Rolling {window_label} annualized return, hypothetical",
                               rolling), width="stretch")
        st.caption(
            f"Hypothetical. Each point is the annualized return over the "
            f"{pb.ROLLING_MONTHS} months ending that month; the first is "
            f"{pb.month_name(first_rolling[0])}."
        )
    else:
        st.caption(f"Hypothetical. No rolling {window_label} return is shown: the history "
                   f"holds {_months(len(bt.returns))}, fewer than {pb.ROLLING_MONTHS}.")

    # ── Calendar years ───────────────────────────────────────────────────────
    def _year_rows(years) -> pd.DataFrame:
        return pd.DataFrame([
            {"Year": pb.year_label(y, bt),
             **{_SERIES_LABEL[n]: _pct(bt.statistics[n].calendar_years[y], signed=True)
                for n in bt.returns.columns}}
            for y in years]).set_index("Year")

    windows = list(bt.statistics[pb.POLICY].windows)
    if windows:
        st.subheader(f"{_list([str(y) for y in windows])} (hypothetical)")
        st.dataframe(_year_rows(windows), width="stretch")
        st.caption("Hypothetical calendar-year returns for these years, from the table "
                   "below.")

    st.subheader("Calendar-year returns (hypothetical)")
    st.dataframe(_year_rows(bt.statistics[pb.POLICY].calendar_years), width="stretch")
    partial = [f"{y} ({pb.year_span(y, bt)})"
               for y in bt.statistics[pb.POLICY].partial_years]
    st.caption(
        "Hypothetical. Each year compounds the months the backtest holds in it"
        + (f". Partial years: {_list(partial)}." if partial else
           ", and every year holds twelve.")
    )

    # ── Rebalances ───────────────────────────────────────────────────────────
    st.subheader("Rebalances (hypothetical)")
    if bt.simulation.rebalances:
        st.dataframe(pd.DataFrame([
            {"Month-end check": pb.month_name(r.month),
             "Sleeves outside their bands": ", ".join(r.triggers)}
            for r in bt.simulation.rebalances]).set_index("Month-end check"),
            width="stretch")
    st.caption(f"Hypothetical. {pb.rebalance_sentence(bt)}")

    # ── Sleeves, funds and proxies ───────────────────────────────────────────
    st.subheader("Sleeves, funds and proxies (hypothetical)")
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
                "Tracking difference": (_pct(t.difference, signed=True)
                                        if t is not None and t.difference is not None else "—"),
                "Tracking error": (_pct(t.error)
                                   if t is not None and t.error is not None else "—"),
                "Correlation": (f"{t.correlation:.3f}"
                                if t is not None and t.correlation is not None else "—"),
            })
    st.dataframe(pd.DataFrame(sleeve_rows).set_index("Sleeve"), width="stretch")
    st.caption(
        "Hypothetical backtest inputs. Tracking statistics compare each fund with its "
        "proxy over the months the history file holds both; a dash marks a fund with no "
        "proxy, or a statistic the overlap cannot give."
        + (" A splice is measured from the month its fund began following a quality "
           f"index, against the strategy the sleeve holds: {_list(splices)}."
           if splices else "")
    )
    st.markdown("**Sources**, as each issuer printed them, with the source's date:")
    for line in pb.source_lines(bt, spec):
        st.markdown(f"- {line}")

    # ── The data ─────────────────────────────────────────────────────────────
    st.subheader("The data")
    st.caption(
        f"Hypothetical results from data/backtest/monthly_returns.csv, which runs through "
        f"{pb.month_name(LAST)}; data/backtest/series.json names each proxy. Refresh it "
        f"with `{pb.REFRESH_COMMAND}`, run with the network up and FRED_API_KEY set, then "
        "review the diff and commit it."
    )

    render_footer()
