"""Sleeve Correlation Matrix — rolling pairwise correlations across asset classes."""
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="Correlations", layout="wide")

from src.asof import as_of_banner
from src.factors import interpret_correlations
from src.prices import get_prices
from src.ui_helpers import render_footer, render_page_header
render_page_header()


TODAY = date.today().isoformat()

# Benchmark tickers per sleeve (excluding Cash — near-zero variance distorts correlations)
_SLEEVES: dict[str, list[tuple[str, float]]] = {
    "US Large Core":          [("SPY",  1.0)],
    "US Large Quality":       [("QUAL", 1.0)],
    "US Large Value":         [("IWD",  1.0)],
    "US Small Cap":           [("IWM",  1.0)],
    "Intl Developed":         [("EFA",  1.0)],
    "Emerging Markets":       [("EEM",  1.0)],
    "Core Fixed Income":      [("IEF",  1.0)],
    "TIPS":                   [("TIP",  1.0)],
    "Real Assets":            [("VNQ",  0.5), ("DBC", 0.5)],
}

_COLORS = {
    "primary": "#2E4057",
    "ref":     "#9E9E9E",
}

_LONG_HISTORY_START = "2007-01-01"   # DBC inception 2006; gives ~15-year overlap


@st.cache_data(ttl=3600, show_spinner=False)
def _load_daily_returns(start_date: str) -> pd.DataFrame:
    """
    Return a DataFrame of daily returns for each sleeve benchmark.
    Blended sleeves (Real Assets) use weighted-average returns.
    Returns only rows where ALL sleeves have data (trading-day intersection).
    Used by the heatmap view; the pair view uses _load_sleeve_returns instead.
    """
    end = TODAY
    ret_dict: dict[str, pd.Series] = {}

    for sleeve_name, components in _SLEEVES.items():
        blended: pd.Series | None = None
        for ticker, weight in components:
            try:
                p = get_prices(ticker, start_date, end)
                p.index = pd.to_datetime(p.index)
                prices = p["adj_close"].ffill()
                daily_ret = prices.pct_change().dropna()
                if blended is None:
                    blended = daily_ret * weight
                else:
                    blended = blended.add(daily_ret * weight, fill_value=0.0)
            except Exception:
                blended = None
                break
        if blended is not None:
            ret_dict[sleeve_name] = blended

    if not ret_dict:
        return pd.DataFrame()

    df = pd.DataFrame(ret_dict)
    # Keep only rows with data for all sleeves, then filter to business days
    # (zero-return rows on both sides indicate non-trading days)
    df = df.dropna()
    non_zero = (df.abs().sum(axis=1) > 0)
    return df[non_zero]


@st.cache_data(ttl=3600, show_spinner=False)
def _load_sleeve_returns(sleeve_name: str) -> pd.Series:
    """
    Load full-history daily returns for a single sleeve's benchmark.

    Fetches from the ticker's earliest available date (requesting from 1990-01-01
    so get_prices returns data from the ticker's actual inception). Used by the
    pair time-series view so each pair uses its own date intersection, not the
    9-sleeve common intersection that _load_daily_returns produces.
    """
    _PAIR_HISTORY_START = "1990-01-01"
    end = TODAY
    components = _SLEEVES.get(sleeve_name, [])
    blended: pd.Series | None = None
    for ticker, weight in components:
        try:
            p = get_prices(ticker, _PAIR_HISTORY_START, end)
            p.index = pd.to_datetime(p.index)
            prices = p["adj_close"].ffill()
            dr = prices.pct_change().dropna()
            blended = dr * weight if blended is None else blended.add(dr * weight, fill_value=0.0)
        except Exception:
            return pd.Series(dtype=float)
    if blended is None:
        return pd.Series(dtype=float)
    # Filter out non-trading-day zeros (ffill artefacts)
    return blended[blended != 0]


def _rolling_corr_matrix(returns: pd.DataFrame, window: int) -> pd.DataFrame:
    """Correlation matrix computed on the trailing `window` trading-day returns."""
    tail = returns.tail(window)
    if len(tail) < max(10, window // 4):
        return pd.DataFrame()
    return tail.corr()


def _corr_heatmap(corr: pd.DataFrame, title: str) -> go.Figure:
    labels = list(corr.columns)
    z      = corr.values.tolist()

    text = [[f"{corr.iloc[i, j]:.2f}" for j in range(len(labels))]
            for i in range(len(labels))]

    fig = go.Figure(go.Heatmap(
        z=z,
        x=labels,
        y=labels,
        text=text,
        texttemplate="%{text}",
        textfont=dict(size=11),
        colorscale=[
            [0.0,  "#7B2D2D"],   # strong negative → red
            [0.35, "#E8EFF7"],   # near-zero → light
            [0.5,  "#FFFFFF"],   # zero
            [0.65, "#E8EFF7"],
            [1.0,  "#1A3A5C"],   # strong positive → dark blue
        ],
        zmid=0,
        zmin=-1,
        zmax=1,
        colorbar=dict(title="ρ", thickness=14),
        hovertemplate="<b>%{y}</b> × <b>%{x}</b><br>ρ = %{z:.3f}<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text=title, font_size=14, x=0),
        height=460,
        margin=dict(l=0, r=0, t=36, b=0),
        paper_bgcolor="white",
        plot_bgcolor="white",
        xaxis=dict(tickfont_size=11, side="bottom"),
        yaxis=dict(tickfont_size=11, autorange="reversed"),
    )
    return fig


def _pair_corr_series(returns: pd.DataFrame, sleeve_a: str, sleeve_b: str,
                      window: int) -> pd.Series:
    """Rolling pairwise correlation between two sleeves."""
    return returns[sleeve_a].rolling(window).corr(returns[sleeve_b]).dropna()


# ── page ──────────────────────────────────────────────────────────────────────

_, col, _ = st.columns([1, 8, 1])
with col:
    st.markdown("## Sleeve Correlations")
    st.caption(
        "Rolling pairwise correlations between SAA sleeve benchmarks. "
        "Low or negative correlations indicate genuine diversification. "
        "Window selector controls the lookback for both the heatmap and pair time-series."
    )
    st.caption(as_of_banner())

    st.divider()

    # ── Controls ─────────────────────────────────────────────────────────────

    ctrl_l, ctrl_r = st.columns([2, 2])
    with ctrl_l:
        window = st.radio(
            "Rolling window (trading days)",
            options=[30, 60, 120, 252],
            index=1,
            format_func=lambda x: {30: "30d (~1 mo)", 60: "60d (~3 mo)",
                                    120: "120d (~6 mo)", 252: "252d (~1 yr)"}[x],
            horizontal=True,
            key="corr_window",
        )
    with ctrl_r:
        view = st.radio(
            "View",
            options=["Heatmap", "Pair time-series"],
            horizontal=True,
            key="corr_view",
        )

    # ── Load data ─────────────────────────────────────────────────────────────

    with st.spinner("Loading benchmark prices…"):
        # Start far enough back to support the longest window + time-series
        load_start = (date.fromisoformat(TODAY) - timedelta(days=365 * 15)).isoformat()
        load_start = max(load_start, _LONG_HISTORY_START)
        returns_df = _load_daily_returns(load_start)

    if returns_df.empty:
        st.error("Price data unavailable — check internet connection or API limits.")
        render_footer()
        st.stop()

    available_sleeves = list(returns_df.columns)

    # ── Heatmap view ──────────────────────────────────────────────────────────

    if view == "Heatmap":
        corr = _rolling_corr_matrix(returns_df, window)

        if corr.empty:
            st.warning(f"Insufficient data for a {window}-day window. Try a shorter window.")
        else:
            wlabel = {30: "30-day", 60: "60-day", 120: "120-day", 252: "252-day"}[window]
            fig = _corr_heatmap(corr, f"Trailing {wlabel} correlation matrix")
            st.plotly_chart(fig, width="stretch")

            st.caption(
                f"Matrix computed on the trailing {window} trading days ending {TODAY}. "
                "Diagonal entries = 1.0 (trivially). Off-diagonal entries: "
                "values near 0 indicate diversification; values near ±1 indicate "
                "high co-movement."
            )

            # Highlight notable pairs
            st.markdown("**Notable pairs (trailing window)**")
            pairs = []
            cols = list(corr.columns)
            for i in range(len(cols)):
                for j in range(i + 1, len(cols)):
                    pairs.append({
                        "Sleeve A":    cols[i],
                        "Sleeve B":    cols[j],
                        "Correlation": round(corr.iloc[i, j], 3),
                    })
            pairs_df = pd.DataFrame(pairs).sort_values("Correlation")
            lowest  = pairs_df.head(3)
            highest = pairs_df.tail(3).iloc[::-1]

            notable_l, notable_r = st.columns(2)
            with notable_l:
                st.caption("Most diversifying (lowest ρ)")
                st.dataframe(lowest, hide_index=True, width="stretch")
            with notable_r:
                st.caption("Most correlated (highest ρ)")
                st.dataframe(highest, hide_index=True, width="stretch")

            _corr_interp = interpret_correlations(corr)
            if _corr_interp:
                st.caption(_corr_interp)

    # ── Pair time-series view ─────────────────────────────────────────────────

    else:
        pair_l, pair_r = st.columns(2)
        with pair_l:
            sleeve_a = st.selectbox(
                "Sleeve A", available_sleeves, index=0, key="pair_a"
            )
        with pair_r:
            default_b = 6 if len(available_sleeves) > 6 else 1
            sleeve_b = st.selectbox(
                "Sleeve B", available_sleeves, index=default_b, key="pair_b"
            )

        if sleeve_a == sleeve_b:
            st.info("Select two different sleeves to compare.")
        else:
            # Load each sleeve independently to use their pair-specific date
            # intersection (not the 9-sleeve common intersection from returns_df,
            # which is constrained by QUAL's 2013-07-18 inception).
            with st.spinner("Loading pair history…"):
                ret_a = _load_sleeve_returns(sleeve_a)
                ret_b = _load_sleeve_returns(sleeve_b)

            pair_df   = pd.DataFrame({"A": ret_a, "B": ret_b}).dropna()
            pair_df   = pair_df[(pair_df.abs().sum(axis=1) > 0)]
            roll_corr = pair_df["A"].rolling(window).corr(pair_df["B"]).dropna()

            if roll_corr.empty or len(roll_corr) < 5:
                st.warning(
                    f"Not enough overlapping history for a {window}-day window. "
                    "Try a shorter window or different sleeves."
                )
            else:
                current_rho = float(roll_corr.iloc[-1])
                x_min = roll_corr.index.min().isoformat()[:10]
                x_max = roll_corr.index.max().isoformat()[:10]

                fig_pair = go.Figure()
                fig_pair.add_trace(go.Scatter(
                    x=roll_corr.index,
                    y=roll_corr.values,
                    mode="lines",
                    name=f"ρ ({sleeve_a} × {sleeve_b})",
                    line=dict(color=_COLORS["primary"], width=2),
                ))
                fig_pair.add_hline(
                    y=0, line_dash="dash", line_color=_COLORS["ref"], line_width=1,
                )
                fig_pair.add_hline(
                    y=current_rho,
                    line_dash="solid", line_color="#C0392B", line_width=1.5,
                    annotation_text=f"Current {current_rho:+.2f}",
                    annotation_position="right",
                    annotation_font_color="#C0392B",
                    annotation_font_size=11,
                )
                fig_pair.update_layout(
                    height=360,
                    margin=dict(l=0, r=80, t=24, b=0),
                    paper_bgcolor="white",
                    plot_bgcolor="#FAFAFA",
                    font=dict(color="#333333", size=12),
                    yaxis=dict(
                        range=[-1.05, 1.05],
                        gridcolor="#EBEBEB",
                        title="Rolling correlation (ρ)",
                    ),
                    xaxis=dict(
                        range=[x_min, x_max],
                        gridcolor="#EBEBEB",
                    ),
                    hovermode="x unified",
                )
                st.plotly_chart(fig_pair, width="stretch")

                wlabel = {30: "30-day", 60: "60-day", 120: "120-day",
                          252: "252-day"}[window]
                st.caption(
                    f"Rolling {wlabel} correlation between **{sleeve_a}** and "
                    f"**{sleeve_b}** benchmarks. Current ρ = **{current_rho:+.2f}**. "
                    "Dashed line at ρ = 0 marks the diversification threshold. "
                    "A correlation persistently near +1 implies the sleeves move "
                    "together and provide little diversification benefit."
                )

    st.divider()

    with st.expander("Methodology", expanded=False):
        st.markdown(
            "**Tickers used:** Each sleeve's SAA benchmark ticker is used for the return "
            "series (SPY, QUAL, IWD, IWM, EFA, EEM, IEF, TIP, and a 50% VNQ + 50% DBC blend "
            "for Real Assets). Cash / SPAXX is excluded — its near-zero daily variance "
            "makes correlation estimates unstable.  \n\n"
            "**Return computation:** Daily log-approximate returns via `adj_close.pct_change()`. "
            "Weekend and holiday rows (zero-return days on all series simultaneously) are "
            "filtered before computing correlations.  \n\n"
            "**Rolling window:** Pearson correlation over the trailing *N* trading days. "
            "At 30 days, estimates are noisy; at 252 days, they are stable but lag "
            "regime changes by up to a year. The 60-day default balances responsiveness "
            "and noise.  \n\n"
            "**Interpretation caveat:** Correlations are not stable. They spike toward +1 "
            "during acute stress events (2008, 2020 COVID crash) and can diverge "
            "substantially between calm and crisis periods. The heatmap reflects only "
            "the selected trailing window."
        )

    render_footer()
