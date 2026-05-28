"""SAA page — Strategic Asset Allocation."""
import streamlit as st

st.set_page_config(page_title="SAA", layout="wide")

import pandas as pd
import plotly.graph_objects as go
from src.asof import as_of_banner
from src.db import get_connection
from src.endowment_benchmarks import CATEGORIES, ENTITIES, get_endowment_data
from src.macro import percentile as macro_percentile
from src.prose_helpers import percentile_label
from src.shiller import get_cape_series
from src.ui_helpers import render_footer, render_page_header
render_page_header()


PARENT_COLORS = {
    "Equity":        "#3D5A80",
    "Fixed Income":  "#4A7C59",
    "Real Assets":   "#8B7355",
    "Cash":          "#AEAEAE",
}

_DISPLAY_NAMES = {"Income": "Fixed Income"}


def _dn(name: str) -> str:
    return _DISPLAY_NAMES.get(name, name)


def _safe_md(text):
    """Escape $ before passing to st.markdown so Streamlit doesn't treat them as LaTeX."""
    if text:
        st.markdown(text.replace("$", r"\$"))


@st.cache_data(ttl=86400, show_spinner=False)
def _load_cape_label() -> str:
    try:
        s = get_cape_series()
        cv = float(s.dropna().iloc[-1])
        pct = macro_percentile(s.dropna(), cv)
        return percentile_label(pct)
    except Exception:
        return "elevated historically"


def load_saa_data():
    with get_connection() as conn:
        parents = [
            dict(r) for r in conn.execute("""
                SELECT name, target_weight, rationale, benchmark_ticker
                FROM asset_classes
                WHERE parent_id IS NULL AND target_weight > 0
                ORDER BY target_weight DESC
            """).fetchall()
        ]
        sub_classes = [
            dict(r) for r in conn.execute("""
                SELECT ac.name, ac.target_weight, ac.tolerance_band,
                       ac.rationale, ac.benchmark_ticker, p.name AS parent_name
                FROM asset_classes ac
                JOIN asset_classes p ON ac.parent_id = p.asset_class_id
                ORDER BY p.target_weight DESC, COALESCE(ac.sort_order, 999) ASC
            """).fetchall()
        ]
    return parents, sub_classes


parents, sub_classes = load_saa_data()

# ── Header ─────────────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.title("Strategic Asset Allocation")
    st.caption("Target weights, tolerance bands, and rationale")
    st.caption(as_of_banner())
    st.divider()

# ── Investment thesis ──────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("Investment Thesis")
    _equity_wt   = next((p["target_weight"] for p in parents if p["name"] == "Equity"), 0.78)
    _intl_dev_wt = next((s["target_weight"] for s in sub_classes if s["name"] == "International Developed"), 0.20)
    _em_wt       = next((s["target_weight"] for s in sub_classes if s["name"] == "Emerging Markets"), 0.09)
    _real_wt     = next((s["target_weight"] for s in sub_classes if s["name"] == "Real Assets"), 0.10)
    _tips_wt     = next((s["target_weight"] for s in sub_classes if s["name"] == "TIPS"), 0.04)
    _core_fi_wt  = next((s["target_weight"] for s in sub_classes if s["name"] == "Core Fixed Income"), 0.06)
    _cape_lbl    = _load_cape_label()
    # TODO: Static refs "comparable only to the 1929 and 1999 peaks" and "CAPE readings
    # above 40" should be reviewed if CAPE falls materially below 40 and _cape_lbl no
    # longer reads "historically extreme". See test_saa_thesis_above_40_cape_reference_still_relevant.
    st.markdown(
        f"Strategic asset allocation reflects a US equity environment with extreme valuations "
        f"(CAPE {_cape_lbl} — comparable only to the 1929 and 1999 peaks) balanced against a "
        f"normalized 2/10 yield curve and HY credit spreads that do not yet signal stress. "
        f"The portfolio sustains {round(_equity_wt * 100)}% equity weight rather than timing "
        f"valuation — historical CAPE readings above 40 are associated with low or negative forward "
        f"10-year real returns, but valuation alone has historically been a poor market-timing signal."
    )
    st.markdown(
        "The framework is designed to deliver returns through factor and geographic diversification "
        "rather than market-timing calls. Style tilts (quality via SPHQ, value via VTV, small-cap "
        "value via AVUV) target factors with positive long-run premia and reduced sensitivity to "
        "multiple compression — particularly relevant given current US large-cap multiples."
    )
    st.markdown(
        f"International Developed ({round(_intl_dev_wt * 100)}%) and Emerging Markets "
        f"({round(_em_wt * 100)}%) provide valuation diversification at meaningfully lower CAPE "
        f"levels. Real Assets ({round(_real_wt * 100)}%) provides inflation-correlated "
        f"diversification with different risk drivers than equity or duration. Core Fixed Income "
        f"({round(_core_fi_wt * 100)}%) provides duration as recession ballast and rebalancing "
        f"optionality; TIPS ({round(_tips_wt * 100)}%) adds real-yield exposure to hedge the "
        f"unhedged inflation tail."
    )
    st.divider()

# ── Top-level allocation chart ──────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    fig = go.Figure()
    for p in parents:
        pct   = round(p["target_weight"] * 100)
        dname = _dn(p["name"])
        color = PARENT_COLORS.get(dname, "#888888")
        # Abbreviated labels for segments too narrow to hold full text
        if pct >= 14:
            label = f"<b>{dname}</b>  {pct}%"
        elif pct >= 7:
            label = f"<b>{dname}</b>"
        else:
            label = f"{pct:.0f}%"
        fig.add_trace(go.Bar(
            name=dname,
            x=[pct],
            y=[""],
            orientation="h",
            marker_color=color,
            marker_line_width=0,
            text=label,
            textposition="inside",
            insidetextanchor="middle",
            textfont=dict(color="white", size=13, family="Arial"),
        ))
    fig.update_layout(
        barmode="stack",
        height=90,
        paper_bgcolor="white",
        plot_bgcolor="white",
        showlegend=False,
        margin=dict(l=0, r=0, t=4, b=4),
        xaxis=dict(range=[0, 100], showticklabels=False, showgrid=False,
                   zeroline=False, fixedrange=True),
        yaxis=dict(showticklabels=False, showgrid=False,
                   zeroline=False, fixedrange=True),
    )
    st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})
    # Legend row anchors small-segment labels that can't fit inside the bar
    legend_parts = []
    for p in parents:
        dname = _dn(p["name"])
        c     = PARENT_COLORS.get(dname, "#888")
        pct   = round(p["target_weight"] * 100)
        legend_parts.append(
            f'<span style="color:{c}">■</span>'
            f'&nbsp;<span style="color:#555; font-size:0.85rem">{dname} {pct}%</span>'
        )
    st.markdown("&emsp;".join(legend_parts), unsafe_allow_html=True)
    st.divider()

# ── Sub-class table ────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("Sleeve Allocation")
    st.caption(
        "Targets shown. Current drift relative to targets and band-breach status are on the Performance page."
    )
    rows = [
        {
            "Sleeve":      sc["name"],
            "Category":    _dn(sc["parent_name"]),
            "Target (%)":  round(sc["target_weight"] * 100, 1),
            "Band (±%)":   round(sc["tolerance_band"] * 100, 1),
            "Benchmark":   sc["benchmark_ticker"] or "—",
        }
        for sc in sub_classes
    ]
    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        width='stretch',
        hide_index=True,
        column_config={
            "Target (%)": st.column_config.NumberColumn(format="%.1f"),
            "Band (±%)":  st.column_config.NumberColumn(format="%.1f"),
        },
    )
    total = df["Target (%)"].sum()
    if abs(total - 100) < 0.1:
        st.markdown(
            f'<span style="color:#2d6a4f; font-size:0.875rem">✓ Allocated: <b>{total:.1f}%</b></span>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<span style="color:#c0392b; font-size:0.875rem">'
            f'⚠ Allocated: <b>{total:.1f}%</b> — does not sum to 100%</span>',
            unsafe_allow_html=True,
        )
    with st.expander("Implementation note", expanded=False):
        st.caption(
            "This SAA reflects the policy framework applied to a paper-trade portfolio simulated "
            "from May 2025 inception. The author's brokerage account holds a partial implementation; "
            "analytics on this site treat the SAA as fully implemented at target weights using the "
            "listed ETFs and blended benchmarks. Methodology (Brinson-Fachler attribution, factor "
            "regressions, macro regime monitoring) is real; the position sizing is paper-portfolio."
        )
    st.divider()

# ── Rationale expanders ────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("Rationale")
    for sc in sub_classes:
        pct = round(sc["target_weight"] * 100, 1)
        bm  = sc["benchmark_ticker"] or "—"
        with st.expander(f"{sc['name']} — {pct}% (benchmark: {bm})"):
            _safe_md(sc["rationale"])
    st.divider()

# ── Endowment comparison panel ─────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("Endowment Context")
    st.caption(
        "Approximate FY2024 asset-class groupings: Yale Investments Office and "
        "Princeton University Investment Company (PRINCO). "
        "Source: public annual reports. Groupings are the author's classification."
    )

    _endo_colors = {
        "Public Equity":          "#2E4057",
        "Private Equity / VC":    "#8C3B3B",
        "Absolute Return / HF":   "#A67B5B",
        "Real Assets":            "#6B7F4A",
        "Fixed Income":           "#4A7C59",
        "Cash":                   "#AEAEAE",
    }

    _entity_names = list(ENTITIES.keys())
    _fig_endo = go.Figure()
    for cat in CATEGORIES:
        weights = [ENTITIES[e].get(cat, 0.0) for e in _entity_names]
        _fig_endo.add_trace(go.Bar(
            name=cat,
            y=_entity_names,
            x=weights,
            orientation="h",
            marker_color=_endo_colors.get(cat, "#888"),
            text=[f"{w:.0f}%" if w >= 6 else "" for w in weights],
            textposition="inside",
            insidetextanchor="middle",
            textfont=dict(color="white", size=11),
        ))

    st.write(
        "This portfolio's public-markets-only implementation versus institutional endowments' "
        "alternatives-heavy allocations:"
    )
    _fig_endo.update_layout(
        barmode="stack",
        height=180,
        paper_bgcolor="white",
        plot_bgcolor="white",
        margin=dict(l=0, r=0, t=40, b=4),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="left", x=0, font_size=11,
        ),
        xaxis=dict(
            range=[0, 100], ticksuffix="%",
            gridcolor="#EBEBEB", showgrid=True,
        ),
        yaxis=dict(showgrid=False),
    )
    st.plotly_chart(_fig_endo, width='stretch', config={"displayModeBar": False})

    st.markdown(
        "Endowments achieve institutional-grade returns through heavy allocations to private equity, "
        "venture capital, and hedge funds — strategies that depend on 25+ year manager relationships, "
        "proprietary deal flow, and multi-year illiquidity tolerance. None are replicable in a retail "
        "brokerage account. This portfolio substitutes liquid factor ETFs (AVUV for small-cap value, "
        "SPHQ for quality, VEA/IEMG for international and EM) to capture related risk premia through "
        "public markets — at the cost of forgoing the illiquidity premium. The comparison is contextual "
        "— to demonstrate institutional analytical framing — not aspirational. The goal is "
        "institutional analytical framing applied at retail scale — not return replication."
    )
    st.caption(
        "Sources: Yale Investments Office Annual Report FY2024 (yale.edu/investments); "
        "Princeton University Investment Company Annual Report FY2024 (princeton.edu/princo). "
        "Allocations are approximate rounded figures; groupings are author's classification "
        "for comparability."
    )
    render_footer()

