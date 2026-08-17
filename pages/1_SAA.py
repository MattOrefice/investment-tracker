"""SAA page — Strategic Asset Allocation."""
import streamlit as st

st.set_page_config(page_title="SAA", layout="wide")

from datetime import date

import pandas as pd
import plotly.graph_objects as go
from src.asof import as_of_banner
from src.db import get_connection
from src.endowment_benchmarks import CATEGORIES, ENTITIES, get_endowment_data
from src.holdings import get_portfolio_account, sleeve_weights_with_coverage
from src.coverage import unresolved_marker
from src.macro import percentile as macro_percentile
from src.prose_helpers import percentile_label
from src.rebalance import compute_drift, interpret_rebalance_status
from src.sleeve_config import international_sleeves, sleeve_holdings
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
        label = percentile_label(pct)
        # Committed-data staleness surface: the CAPE file is refreshed only by
        # tools/refresh_market_data.py, so a stale series must not read as a
        # current valuation stance — the label carries its own as-of.
        from src.asof import MARKET_DATA_STALE_DAYS_VALUATION, staleness_note
        from src.shiller import cape_frontier
        frontier = cape_frontier()
        if staleness_note("Shiller CAPE", frontier, MARKET_DATA_STALE_DAYS_VALUATION):
            label += f" (CAPE data through {frontier.isoformat()})"
        return label
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
                WHERE ac.target_weight > 0
                ORDER BY p.target_weight DESC, COALESCE(ac.sort_order, 999) ASC
            """).fetchall()
        ]
    return parents, sub_classes


parents, sub_classes = load_saa_data()


def _require_weight(rows, name: str, *, kind: str = "sleeve") -> float:
    """Target weight for a sleeve/parent, or raise. Deliberately NO default.

    Every lookup here previously carried a hardcoded fallback (0.78/0.98,
    0.20/0.98, 0.09/0.98, …) that was EXACTLY the live DB target — bit-identical,
    not merely close. So a renamed, split, or removed row would have templated the
    stale weight into the investment-thesis prose with no error and nothing
    visibly different: the fallback was indistinguishable from a correct lookup by
    value alone, and only membership told them apart. An unresolvable name must
    raise rather than render a plausible wrong number.
    """
    for r in rows:
        if r["name"] == name:
            return float(r["target_weight"])
    raise ValueError(
        f"SAA {kind} {name!r} is absent from asset_classes, but this page templates "
        f"its target weight into the investment-thesis prose below. Present: "
        f"{sorted(r['name'] for r in rows)}. If it was split or renamed, rewrite the "
        f"prose to name the new {kind}s — do not restore a default: the old one was "
        f"bit-identical to the live target and rendered a stale weight silently."
    )

# ── Header ─────────────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.title("Strategic Asset Allocation")
    st.caption("Target weights, tolerance bands, and rationale")
    # Deferred into a slot: the banner reports coverage, which is not known until
    # the sleeve weights load further down. The slot holds its position so the
    # rendered order is unchanged. Same pattern as pages/2_Performance.py:222.
    _asof_slot = st.empty()
    _asof_slot.caption(as_of_banner())
    st.divider()

# ── Investment thesis ──────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.subheader("Investment Thesis")
    _equity_wt   = _require_weight(parents, "Equity", kind="parent")
    # Summed over the developed-international sleeves PRESENT IN THIS BOOK, read
    # from the DB — one sleeve on the personal book, four on the demo book. The
    # combined figure is identical either way (the split is weight-neutral); the
    # derivation survives both taxonomies instead of hardcoding a sleeve set.
    _intl_sleeve_names = international_sleeves()
    _intl_dev_wt = sum(_require_weight(sub_classes, _s) for _s in _intl_sleeve_names)
    _em_wt       = _require_weight(sub_classes, "Emerging Markets")
    _real_wt     = _require_weight(sub_classes, "Real Assets")
    _tips_wt     = _require_weight(sub_classes, "TIPS")
    _core_fi_wt  = _require_weight(sub_classes, "Core Fixed Income")
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
    # Factor-tilt prose is derived from the sleeves PRESENT IN THIS BOOK, so it
    # describes what is actually held rather than a frozen taxonomy. On the demo
    # book the international tilt sleeves (quality/value/small-value) exist and
    # are named; on the personal book developed international is a single
    # cap-weighted sleeve, so the prose says exactly that instead of claiming
    # tilt holdings the household does not own.
    def _and_join(items: list[str]) -> str:
        items = list(items)
        if len(items) <= 1:
            return items[0] if items else ""
        if len(items) == 2:
            return f"{items[0]} and {items[1]}"
        return ", ".join(items[:-1]) + f", and {items[-1]}"

    _holds = sleeve_holdings()
    _us_tilts = [t for _s in ("US Large Quality", "US Large Value", "US Small Cap")
                 for t in _holds.get(_s, [])]
    _intl_core_names = {"International Core", "International Developed"}
    _intl_tilt_tickers = [t for _s in _intl_sleeve_names if _s not in _intl_core_names
                          for t in _holds.get(_s, [])]

    if _intl_tilt_tickers:
        st.markdown(
            "The framework is designed to deliver returns through factor and geographic diversification "
            "rather than market-timing calls. Style tilts target factors with positive long-run premia — "
            "quality, value, and small-cap value — and they are expressed on both sides of the book: "
            f"{_and_join(_us_tilts)} in the US; {_and_join(_intl_tilt_tickers)} internationally, in the "
            "same proportions. The premia these screens target are documented in international data on the "
            "same terms as domestic. Expressing them only at home would be home bias rather than a view."
        )
    else:
        _intl_core_tickers = [t for _s in _intl_sleeve_names for t in _holds.get(_s, [])]
        st.markdown(
            "The framework is designed to deliver returns through factor and geographic diversification "
            "rather than market-timing calls. Style tilts target factors with positive long-run premia — "
            "quality, value, and small-cap value — expressed in the US book: "
            f"{_and_join(_us_tilts)}. Developed international is held as a single cap-weighted sleeve "
            f"({_and_join(_intl_core_tickers)}); the factor tilts are expressed domestically. The premia "
            "these screens target are documented in international data on the same terms as domestic."
        )
    st.markdown(
        "These are structural positions, not a response to current valuations. A tilt whose case "
        "depended on today's multiples would be a tactical trade in strategic clothing."
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
        # Suppress in-bar text on segments under 12%: narrow slices render
        # illegible (vertical letter-stacks on mobile, cramped on desktop). The
        # legend directly below carries the exact value for every segment.
        if pct < 12:
            label = ""
        elif pct >= 14:
            label = f"<b>{dname}</b>  {pct}%"
        else:  # 12–13%
            label = f"<b>{dname}</b>"
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
        f"Actual weights are the **{get_portfolio_account()['display_name']}** "
        "self-directed taxable book (traded ledger); targets are SAA policy. "
        "Retirement and externally-managed accounts are on the Household View."
    )

    _band_line = ""
    _cash_note = ""
    _gap_note = None
    try:
        _sw, _sw_cov = sleeve_weights_with_coverage(date.today().isoformat())
        _gap_note = unresolved_marker(_sw_cov)
        # Rewrite the banner now that coverage is known. Until this point it has
        # the frontier only, which is honest but says nothing about completeness.
        _asof_slot.caption(as_of_banner(coverage=_sw_cov))
        if not _sw.empty:
            _weights = dict(zip(_sw.index, _sw["Actual Weight"].values))
            _targets = {sc["name"]: sc["target_weight"] for sc in sub_classes}
            _bands   = {sc["name"]: sc["tolerance_band"] for sc in sub_classes}
            _band_line = interpret_rebalance_status(compute_drift(_weights, _targets, _bands))
            _cash_mv  = _sw.attrs.get("cash_mv", 0.0)
            _cash_pct = _sw.attrs.get("cash_weight_of_total", 0.0) * 100
            if _cash_mv > 0:
                # Sleeve count from the book itself (12 demo / 9 personal) —
                # the hardcoded "9" was stale on the demo render.
                _n_strategic = sum(1 for sc in sub_classes if sc["target_weight"] > 0)
                _cash_note = (
                    f"Operational cash: **${_cash_mv:,.0f}** · {_cash_pct:.1f}% of total — "
                    f"SPAXX float, untargeted and held outside the ex-cash SAA (the {_n_strategic} "
                    "strategic sleeves above are measured as a share of invested value)."
                )
    except Exception:
        _band_line = ""

    if _gap_note:
        # A holding that could not be priced counted as zero, so the weights below
        # are measured against a smaller book than is actually held — and because
        # they are a share of an ex-cash denominator that shrank with it, the
        # PRICED sleeves are overstated too, not just the unpriced ones. That
        # second half is the non-obvious one and the reason this is a warning
        # rather than a footnote.
        st.warning(
            f"⚠ **Weights exclude part of the book.** {_gap_note} Every sleeve "
            "weight and drift figure below is measured against a smaller book "
            "than you hold — the priced sleeves are overstated, not just the "
            "unpriced ones."
        )
        # The band verdict is computed from those same weights, so neither "in
        # band" nor "out of band" is a claim the data supports. Withdraw it rather
        # than qualify it: a qualified verdict is still read as a verdict.
        st.caption(
            "**Band status:** unavailable — the book could not be fully priced. "
            "Full drift detail and corrective contributions are on the Capital "
            "Deployment page."
        )
    elif _band_line:
        _icon = "✓" if "within their tolerance" in _band_line else "⚠"
        st.caption(
            f"**Band status:** {_icon} {_band_line} "
            "Full drift detail and corrective contributions are on the Capital Deployment page."
        )
    else:
        st.caption(
            "Targets shown. Current drift relative to targets and band-breach status "
            "are on the Capital Deployment page."
        )
    if _cash_note:
        st.caption(_cash_note)
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
            # Numeric columns right-align by default; whole-percent bands drop the
            # trailing ".0" and both columns are tightened so the figures line up
            # cleanly. Display only — the underlying target and band values are
            # unchanged (targets keep their ex-cash decimal; bands stay ±0.03/±0.02).
            "Target (%)": st.column_config.NumberColumn(format="%.1f", width="small"),
            "Band (±%)":  st.column_config.NumberColumn(format="%.0f", width="small"),
        },
    )
    # Sum the UNROUNDED policy weights, not the display-rounded column: twelve
    # 1dp-rounded values summed to exactly 99.9 and sat on the tolerance
    # boundary, flagging a book whose true targets sum to 100.0000%. Red now
    # means genuinely mis-seeded, not an unlucky sleeve count.
    total = sum(sc["target_weight"] for sc in sub_classes) * 100
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

