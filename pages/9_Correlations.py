"""Sleeve Correlation Matrix — rolling pairwise correlations across asset classes."""
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="Correlations", layout="wide")

from src import asset_evaluation as ae
from src.asof import as_of_banner
from src.factors import interpret_correlations
from src.prices import get_prices
from src.ui_helpers import render_footer, render_page_header
render_page_header()


TODAY = date.today().isoformat()

# Canonical sleeve→benchmark mapping (single source of truth in asset_evaluation;
# excludes Cash — near-zero variance distorts correlations).
_SLEEVES: dict[str, list[tuple[str, float]]] = ae.SLEEVE_BENCHMARKS

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
        zmin=-0.3,
        zmax=1.0,
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

    with st.expander("How to read this page", expanded=False):
        st.markdown(
            "**Heatmap:** Each cell is the Pearson correlation between two sleeve "
            "benchmarks over the rolling window. Values near +1 mean co-movement; "
            "near 0 means genuine diversification; below 0 means return offsets.\n\n"
            "**Window selector:** 60-day default balances responsiveness against noise. "
            "At 30 days, a single volatile week can dominate; at 252 days, the estimate "
            "lags regime changes by up to a year.\n\n"
            "**Pair time-series:** Select any two sleeves to see how their rolling "
            "correlation has evolved over time.\n\n"
            "**Key caveat:** Correlations are not stationary. During equity drawdowns — "
            "2008, 2020, 2022 — they tend to spike toward +1 across asset classes "
            "simultaneously, meaning the diversification shown in the heatmap can "
            "evaporate precisely when it is most needed."
        )

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
            options=["Heatmap", "Pair time-series", "Average over time"],
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
            st.caption(
                "Rolling correlations are not stationary: during equity drawdowns, "
                "pairs that appear uncorrelated in calm periods tend to converge toward +1, "
                "reducing diversification benefit precisely when it matters most."
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

            _corr_col_cfg = {
                "Correlation": st.column_config.NumberColumn(format="%.3f")
            }
            notable_l, notable_r = st.columns(2)
            with notable_l:
                st.caption("Most diversifying (lowest ρ)")
                st.dataframe(lowest, hide_index=True, width="stretch",
                             column_config=_corr_col_cfg)
            with notable_r:
                st.caption("Most correlated (highest ρ)")
                st.dataframe(highest, hide_index=True, width="stretch",
                             column_config=_corr_col_cfg)

            _corr_interp = interpret_correlations(corr)
            if _corr_interp:
                st.markdown(_corr_interp)

            # ── Bond-equity correlation regime block ──────────────────────────
            # Equity sleeve names DERIVED from ae.SLEEVES (itself DB-derived), so
            # this resolves to the sleeves THIS book actually has — six on the
            # personal book (one developed-international sleeve), eleven on demo
            # (the four-sleeve split) — instead of hardcoding demo's names, which
            # _load_sleeve_returns cannot resolve against a personal DB.
            _CORE_FI  = "Core Fixed Income"
            _NON_EQUITY = {_CORE_FI, "TIPS", "Real Assets"}
            _EQ_NAMES = [s for s in ae.SLEEVES if s not in _NON_EQUITY]
            if _CORE_FI in corr.columns:
                _be_curr_pairs = [
                    float(corr.loc[_CORE_FI, eq])
                    for eq in _EQ_NAMES if eq in corr.columns
                ]
                avg_be = sum(_be_curr_pairs) / len(_be_curr_pairs) if _be_curr_pairs else None

                # Pre-2020 baseline: per-pair max histories via _load_sleeve_returns
                # so IEF × SPY extends to ~2002 rather than being capped at QUAL's 2013
                # inception (the heatmap common-intersection constraint).
                _fi_ret = _load_sleeve_returns(_CORE_FI)
                _fi_pre = _fi_ret[_fi_ret.index < pd.Timestamp("2020-01-01")]
                _be_pre_pairs: list[float] = []
                for _eq in _EQ_NAMES:
                    _eq_ret = _load_sleeve_returns(_eq)
                    _eq_pre = _eq_ret[_eq_ret.index < pd.Timestamp("2020-01-01")]
                    _pair_pre = pd.concat([_fi_pre, _eq_pre], axis=1, sort=False).dropna()
                    _pair_pre = _pair_pre[_pair_pre.abs().sum(axis=1) > 0]
                    if len(_pair_pre) >= 30:
                        _be_pre_pairs.append(float(_pair_pre.iloc[:, 0].corr(_pair_pre.iloc[:, 1])))
                pre2020_be = (
                    sum(_be_pre_pairs) / len(_be_pre_pairs)
                    if _be_pre_pairs else None
                )

                if avg_be is not None and pre2020_be is not None:
                    st.markdown(
                        f"Bond–equity correlations have shifted materially since 2020. "
                        f"Core Fixed Income's average pairwise correlation with the equity "
                        f"sleeves was ρ ≈ {pre2020_be:.2f} prior to 2020 — consistent with "
                        f"the negative-correlation assumption that underpins most 60/40 "
                        f"frameworks. "
                        f"The trailing {window}-day estimate is now ρ ≈ {avg_be:.2f}. "
                        f"This is a regime, not a permanent structural change: when "
                        f"inflation is the dominant macro risk, rising rates move bonds and "
                        f"equities in the same direction, and the 2022 joint drawdown — "
                        f"when Core Fixed Income declined alongside equities — is the "
                        f"relevant stress test for this allocation."
                    )
                    st.caption(
                        "The same rolling-window methodology underlies the correlation "
                        "analysis in the Asset Evaluation page, where the post-2020 "
                        "correlation shift is the central analytical finding."
                    )

    # ── Pair time-series view ─────────────────────────────────────────────────

    elif view == "Pair time-series":
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

                # Pre-2020 context: rolling average prior to 2020-01-01
                _pre2020_roll = roll_corr[roll_corr.index < pd.Timestamp("2020-01-01")]
                if len(_pre2020_roll) >= 5:
                    _pre2020_rho = float(_pre2020_roll.mean())
                    _diff = current_rho - _pre2020_rho
                    _be_label = (
                        "elevated relative to" if _diff > 0.10
                        else "below" if _diff < -0.10
                        else "near"
                    )
                    _context = (
                        f" The pre-2020 average for this pair was ρ ≈ {_pre2020_rho:.2f}; "
                        f"the current reading of {current_rho:+.2f} is {_be_label} "
                        f"the pre-2020 baseline."
                    )
                else:
                    _context = ""

                st.caption(
                    f"Rolling {wlabel} correlation between **{sleeve_a}** and "
                    f"**{sleeve_b}** benchmarks · {x_min} to {x_max}. "
                    f"Current ρ = **{current_rho:+.2f}**."
                    + _context
                    + " Dashed line at ρ = 0 is a reference; "
                    "values above zero co-move, values below offset."
                )

    # ── Average-over-time view ────────────────────────────────────────────────

    else:
        extended = st.checkbox(
            "Extended history (8 sleeves, excludes Quality → reaches ~2007)",
            value=False, key="corr_avg_extended",
            help="Drops US Large Quality (QUAL, 2013 inception) so the common "
                 "history extends back to ~2007 and the 2008 crisis becomes visible.",
        )
        _exclude   = ["US Large Quality"] if extended else None
        _set_label = "8 sleeves (excludes Quality)" if extended else "9 sleeves"

        with st.spinner("Computing average pairwise correlation…"):
            avg_df = ae.average_pairwise_rolling_correlation(
                window=window, start_date="2007-01-01", exclude=_exclude,
            )
            sleeve_ret = ae.get_sleeve_returns(start_date="2007-01-01", exclude=_exclude)
            decomp     = ae.rolling_equity_vs_bondequity(sleeve_ret, window)

        if avg_df.empty or "mean" not in avg_df.columns or avg_df["mean"].dropna().empty:
            st.warning(
                f"Insufficient data for a {window}-day window. Try a shorter window."
            )
        else:
            mean_s      = avg_df["mean"].dropna()
            current_avg = float(mean_s.iloc[-1])
            x_min       = mean_s.index.min().isoformat()[:10]
            x_max       = mean_s.index.max().isoformat()[:10]
            wlabel      = {30: "30-day", 60: "60-day", 120: "120-day",
                           252: "252-day"}[window]

            fig_avg = go.Figure()
            # min–max dispersion band (drawn first, behind the mean line)
            if {"min", "max"}.issubset(avg_df.columns):
                _band = avg_df.dropna(subset=["min", "max"])
                fig_avg.add_trace(go.Scatter(
                    x=_band.index, y=_band["max"], mode="lines",
                    line=dict(width=0), hoverinfo="skip", showlegend=False,
                ))
                fig_avg.add_trace(go.Scatter(
                    x=_band.index, y=_band["min"], mode="lines",
                    line=dict(width=0), fill="tonexty",
                    fillcolor="rgba(46,64,87,0.12)",
                    name="Pairwise min–max range", hoverinfo="skip",
                ))
            fig_avg.add_trace(go.Scatter(
                x=mean_s.index, y=mean_s.values, mode="lines",
                name="Average pairwise ρ",
                line=dict(color=_COLORS["primary"], width=2),
            ))
            fig_avg.add_hline(
                y=0, line_dash="dash", line_color=_COLORS["ref"], line_width=1,
            )
            fig_avg.add_hline(
                y=current_avg, line_dash="solid", line_color="#C0392B", line_width=1.5,
                annotation_text=f"Current {current_avg:+.2f}",
                annotation_position="right", annotation_font_color="#C0392B",
                annotation_font_size=11,
            )
            fig_avg.update_layout(
                height=360, margin=dict(l=0, r=80, t=24, b=0),
                paper_bgcolor="white", plot_bgcolor="#FAFAFA",
                font=dict(color="#333333", size=12),
                yaxis=dict(range=[-1.05, 1.05], gridcolor="#EBEBEB",
                           title="Avg pairwise correlation (ρ)"),
                xaxis=dict(range=[x_min, x_max], gridcolor="#EBEBEB"),
                hovermode="x unified",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font_size=11),
            )
            st.plotly_chart(fig_avg, width="stretch")

            st.caption(
                f"Mean of all pairwise rolling {wlabel} correlations across the "
                f"{_set_label} · {x_min} to {x_max}. Current average ρ = "
                f"**{current_avg:+.2f}**. Shaded band = min–max of the pairwise "
                "correlations at each date (the dispersion the average hides)."
            )

            st.markdown(ae.interpret_rolling_correlation(current_avg, mean_s))

            # Equity vs bond-equity decomposition (separate chart for legibility)
            if not decomp.empty:
                st.markdown("**Equity vs bond–equity correlation**")
                fig_dec = go.Figure()
                if "intra_equity" in decomp.columns:
                    _ie = decomp["intra_equity"].dropna()
                    fig_dec.add_trace(go.Scatter(
                        x=_ie.index, y=_ie.values, mode="lines",
                        name="Avg intra-equity ρ",
                        line=dict(color=_COLORS["primary"], width=2),
                    ))
                if "bond_equity" in decomp.columns:
                    _be = decomp["bond_equity"].dropna()
                    fig_dec.add_trace(go.Scatter(
                        x=_be.index, y=_be.values, mode="lines",
                        name="Avg bond–equity ρ",
                        line=dict(color="#C0392B", width=2),
                    ))
                fig_dec.add_hline(
                    y=0, line_dash="dash", line_color=_COLORS["ref"], line_width=1,
                )
                fig_dec.update_layout(
                    height=300, margin=dict(l=0, r=80, t=24, b=0),
                    paper_bgcolor="white", plot_bgcolor="#FAFAFA",
                    font=dict(color="#333333", size=12),
                    yaxis=dict(range=[-1.05, 1.05], gridcolor="#EBEBEB",
                               title="Correlation (ρ)"),
                    xaxis=dict(gridcolor="#EBEBEB"),
                    hovermode="x unified",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font_size=11),
                )
                st.plotly_chart(fig_dec, width="stretch")
                st.caption(
                    "Intra-equity ρ = average correlation among the six equity sleeves; "
                    "bond–equity ρ = average correlation between the bond sleeves (Core "
                    "Fixed Income, TIPS) and the equity sleeves. The post-2020 rise in "
                    "bond–equity correlation — bonds no longer offsetting equity drawdowns — "
                    "is the 60/40-relevant regime shift (the 2022 joint drawdown)."
                )

            st.caption(
                "**History constraint:** the full 9-sleeve common window starts ~2013 "
                "(QUAL inception), so the default view covers the 2020 and 2022 stress "
                "episodes but not 2008. Enable *Extended history* to drop Quality and reach "
                "~2007, making the 2008 crisis visible. Correlations are non-stationary — "
                "the trailing window reflects only recent co-movement."
            )

    st.divider()

    with st.expander("Methodology", expanded=False):
        st.markdown(
            "**Tickers used:** Each sleeve's SAA benchmark ticker is used for the return "
            "series (SPY, QUAL, IWD, IWM, EFA, EEM, IEF, TIP, and a 60% VNQ + 40% DBC blend "
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
