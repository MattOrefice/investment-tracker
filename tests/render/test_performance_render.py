"""Render-aware tests for the Performance & Attribution page.

These tests assert on rendered widget content using streamlit.testing.v1.AppTest.
They confirm the page renders without NaN metrics, which was the observable
symptom of the Phase 8p duplicate-price-index bug ($30 current value, 0% TWR).
"""
from __future__ import annotations

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="module")
def performance_app() -> AppTest:
    """Run the Performance page once and return the rendered AppTest object."""
    at = AppTest.from_file("pages/2_Performance.py", default_timeout=120)
    at.run()
    return at


def test_performance_runs_without_exception(performance_app: AppTest) -> None:
    """Page must complete render without raising an unhandled exception."""
    assert not performance_app.exception, (
        f"Performance page raised: {performance_app.exception}"
    )


def test_performance_metrics_are_finite(performance_app: AppTest) -> None:
    """No metric on the Performance page may render 'nan'.

    Regression pin: Phase 8p. Root cause — duplicate price-date index in
    get_prices() caused reindex() to raise 'cannot reindex on an axis with
    duplicate labels'. The exception was silently caught, leaving every ETF
    with a NaN price series. SPAXX's $1 fallback produced a flat $30 series,
    giving 0% TWR and nan bps vs. benchmarks. The dedup guard in get_prices
    prevents this. If this test fails, check src/prices.py get_prices dedup.
    """
    metric_values = [m.value for m in performance_app.metric]
    nan_metrics = [v for v in metric_values if "nan" in str(v).lower()]
    assert not nan_metrics, (
        f"Performance page rendered NaN metrics: {nan_metrics}. "
        f"Phase 8p regression — paper-trade portfolio loader produced degenerate output."
    )


def test_performance_has_headline_metrics(performance_app: AppTest) -> None:
    """Performance page renders at least 4 metric widgets (the headline row). Pinned: Phase 4.

    Skipped when running against an empty portfolio (tracker.db with no trades) because
    the page hits the empty-state guard (st.stop()) before rendering metrics. Runs on
    Cloud against demo.db which has a full paper-trade portfolio.
    """
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    assert len(performance_app.metric) >= 4, (
        f"Expected at least 4 metric widgets, got {len(performance_app.metric)}"
    )


def test_period_returns_table_renders(performance_app: AppTest) -> None:
    """Period returns section renders a dataframe widget. Pinned: Phase 4."""
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    assert len(performance_app.dataframe) >= 1, (
        "No dataframe rendered — Period Returns table missing"
    )


def test_generate_report_expander_present(performance_app: AppTest) -> None:
    """Generate Quarterly Report expander must render (appears before empty-state guard). Pinned: Phase 6."""
    expander_labels = [e.label for e in performance_app.expander]
    assert any("report" in lbl.lower() for lbl in expander_labels), (
        f"Quarterly Report expander not found in: {expander_labels}"
    )


def test_quarterly_snapshot_renders_when_quarter_reportable(performance_app: AppTest) -> None:
    """Section 1a quarterly snapshot renders its '(locked)' header on the normal path.

    Guards the inception-aware suppression (personal-mode pre-inception fix): demo.db's
    inception predates the most-recent completed quarter, so the snapshot MUST still
    report and MUST NOT show the 'No completed quarter yet.' empty state. The empty
    state only fires when a portfolio's entire most-recent completed quarter predates
    its inception (covered by the pure-function tests in test_asof_reportable_quarter).
    """
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    markdowns = [m.value for m in performance_app.markdown]
    assert any("Quarterly report —" in m and "(locked)" in m for m in markdowns), (
        f"Section 1a '(locked)' quarterly header missing — demo path regressed. "
        f"Markdown headers: {[m for m in markdowns if 'Quarterly' in m]}"
    )
    assert not any("No completed quarter yet." in m for m in markdowns), (
        "Pre-inception empty state fired in demo mode — suppression mis-triggered."
    )


def test_period_returns_no_suppression_on_full_history(performance_app: AppTest) -> None:
    """Demo (full history): NO Period-Returns row is suppressed — every window start
    is after inception, so the insufficient-history caption must be absent. Guards
    that the short-history row suppression cannot over-fire on a portfolio with
    adequate history (the demo-stays-unchanged invariant)."""
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    captions = [c.value for c in performance_app.caption]
    assert not any("longer than the portfolio's history" in c for c in captions), (
        "Insufficient-history suppression fired on the full-history demo portfolio — "
        "over-firing; only short-history portfolios should suppress trailing-period rows."
    )


def test_methodology_expander_present(performance_app: AppTest) -> None:
    """Methodology validation expander must render. Pinned: Phase 4."""
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    expander_labels = [e.label for e in performance_app.expander]
    assert any("methodology" in lbl.lower() or "validation" in lbl.lower()
               for lbl in expander_labels), (
        f"Methodology expander not found in: {expander_labels}"
    )


def test_build_caption_suppressed_without_env(performance_app: AppTest) -> None:
    """Build caption must be absent when SHOW_BUILD_HASH env var is not set (Phase 15.1)."""
    import os
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    if os.environ.get("SHOW_BUILD_HASH"):
        pytest.skip("SHOW_BUILD_HASH is set — caption gating not active in this environment")
    captions = [c.value for c in performance_app.caption]
    assert not any("Build" in c for c in captions), (
        f"Build caption should be suppressed but found: {[c for c in captions if 'Build' in c]}"
    )


def test_risk_metrics_radio_has_five_options(performance_app: AppTest) -> None:
    """Risk-Adjusted Metrics window radio must offer exactly five period options. Pinned: Phase 8u.

    Options must match the BF Attribution period set in order:
    1 Month, 3 Months, YTD, 1 Year, Since Inception.
    """
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    risk_radios = [r for r in performance_app.radio if r.key == "risk_metrics_window"]
    assert risk_radios, "Risk-Adjusted Metrics window radio (key='risk_metrics_window') not found"
    opts = risk_radios[0].options
    assert len(opts) == 5, f"Expected 5 window options, got {len(opts)}: {opts}"
    assert list(opts) == ["1 Month", "3 Months", "YTD", "1 Year", "Since Inception"], (
        f"Window options order or labels incorrect: {list(opts)}"
    )


def test_risk_metrics_1m_window_no_nan() -> None:
    """Switching risk metrics to 1 Month window must produce no NaN metric values. Pinned: Phase 8u.

    Uses a fresh AppTest instance (not the module fixture) to avoid contaminating
    shared widget state. Sets the risk_metrics_window radio to '1 Month' and
    re-runs the page, then asserts all rendered metric values are finite.
    """
    at = AppTest.from_file("pages/2_Performance.py", default_timeout=120)
    at.run()
    if not at.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    risk_radios = [r for r in at.radio if r.key == "risk_metrics_window"]
    if not risk_radios:
        pytest.skip("Risk metrics radio not found")
    risk_radios[0].set_value("1 Month")
    at.run()
    assert not at.exception, f"Page raised after 1M radio switch: {at.exception}"
    nan_metrics = [m.value for m in at.metric if "nan" in str(m.value).lower()]
    assert not nan_metrics, f"1M window rendered NaN metrics: {nan_metrics}"


def test_risk_metrics_1m_sharpe_differs_from_si() -> None:
    """Sharpe at 1M window must differ from Sharpe at SI by ≥ 0.01. Pinned: Phase 8u.1.

    Window-distinguishing integration pin. If window filtering collapses (the
    view/copy bug recurs), all windows return SI values and Sharpe values are
    identical. The 1M portfolio Sharpe is substantially higher than SI Sharpe
    for the current demo portfolio (recent month outperformed the full inception
    period), so any identity in displayed values is a clear signal of collapse.

    Fix if failing: restore .copy() after each cutoff slice in compute_risk_metrics.
    """
    at = AppTest.from_file("pages/2_Performance.py", default_timeout=120)
    at.run()
    if not at.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")

    # Capture SI Sharpe (default window = Since Inception, radio index=4)
    sharpe_si_widgets = [m for m in at.metric if m.label == "Sharpe"]
    if not sharpe_si_widgets or sharpe_si_widgets[0].value == "—":
        pytest.skip("Sharpe metric unavailable in default (SI) window")
    sharpe_si = float(sharpe_si_widgets[0].value)

    # Switch to 1 Month and re-run
    risk_radios = [r for r in at.radio if r.key == "risk_metrics_window"]
    if not risk_radios:
        pytest.skip("risk_metrics_window radio not found")
    risk_radios[0].set_value("1 Month")
    at.run()
    assert not at.exception, f"Page raised after 1M radio switch: {at.exception}"

    sharpe_1m_widgets = [m for m in at.metric if m.label == "Sharpe"]
    if not sharpe_1m_widgets or sharpe_1m_widgets[0].value == "—":
        pytest.skip("Sharpe metric unavailable in 1M window")
    sharpe_1m = float(sharpe_1m_widgets[0].value)

    assert abs(sharpe_si - sharpe_1m) >= 0.01, (
        f"SI Sharpe ({sharpe_si}) and 1M Sharpe ({sharpe_1m}) are indistinguishable. "
        f"Window filter likely collapsed — all windows returning SI-length series. "
        f"Fix: restore .copy() after cutoff slice in src/performance.py."
    )


def test_reconciliation_no_latex_artifacts(performance_app: AppTest) -> None:
    """Reconciliation paragraph must escape dollar signs to prevent LaTeX rendering.

    Regression pin: Phase 8t. Phase 8s introduced **$...**  bold syntax in the
    reconciliation st.caption. Unescaped $ chars triggered Streamlit's LaTeX
    extension, rendering bold dollar amounts as math-mode gibberish (spaced-out
    characters, ∗∗ instead of bold, → rendered as math arrow). Fix: escape every
    $ in the f-string template as \\$. The raw caption value passed to st.caption
    must contain \\$ — if it contains a bare $N pattern (dollar + digit inside a
    bold span), LaTeX will corrupt the output on next render.
    """
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    captions = [c.value for c in performance_app.caption]
    recon = next((c for c in captions if "Reconciliation" in c), None)
    assert recon is not None, "Reconciliation paragraph not found in page captions"
    assert "\\$" in recon, (
        f"Reconciliation caption lacks escaped dollar signs (\\$). "
        f"Unescaped $ will trigger Streamlit LaTeX mode and corrupt bold amounts. "
        f"Caption starts: {recon[:120]}"
    )


def test_naive_benchmark_radio_has_two_options(performance_app: AppTest) -> None:
    """Naive benchmark radio must offer exactly two options. Pinned: Phase 10 Section 0.

    Options must be '60/40 (60% SPY / 40% AGG)' (default, index 0) and 'S&P 500 (SPY)'.
    If the radio is absent or has the wrong options, the toggle is broken.
    """
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    naive_radios = [r for r in performance_app.radio if r.key == "naive_benchmark"]
    assert naive_radios, "Naive benchmark radio (key='naive_benchmark') not found"
    opts = list(naive_radios[0].options)
    assert len(opts) == 2, f"Expected 2 naive benchmark options, got {len(opts)}: {opts}"
    assert opts[0] == "60/40 (60% SPY / 40% AGG)", f"First option wrong: {opts[0]!r}"
    assert opts[1] == "S&P 500 (SPY)", f"Second option wrong: {opts[1]!r}"


def test_two_stage_reconciliation_uses_price_series(performance_app: AppTest) -> None:
    """Stage 1/2 reconciliation caption must use price-series methodology with 0.00 bps residual.

    Phase 10.1 regression pin. Pre-fix code called compute_two_stage_attribution()
    with _r_p_bf (BF-internal price-appreciation return) instead of the portfolio
    price-series TWR, creating a ~316 bps Stage 2 residual vs. the price series.
    The reconciliation caption showed '⚠ residual: N bps'. Fixed code uses
    _benchmark_period_return(pv, period) throughout; algebra residual is 0.00 bps.
    """
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    captions = [c.value for c in performance_app.caption]
    # The stage-level reconciliation caption contains "price-series methodology"
    # (old code had "computed independently ... by portfolio price series")
    stage_recon = next((c for c in captions if "price-series methodology" in c), None)
    assert stage_recon is not None, (
        "Stage 1/2 reconciliation caption not found or still using old 'computed independently' "
        "format. Phase 10.1 fix not applied — check compute_two_stage_attribution() call site."
    )
    assert "⚠" not in stage_recon, (
        f"Stage 1/2 reconciliation caption shows a residual warning (⚠). "
        f"Price-series methodology should give exact algebra (0.00 bps residual). "
        f"Caption: {stage_recon}"
    )
    assert "0.00 bps" in stage_recon, (
        f"Stage 1/2 reconciliation must show '0.00 bps' algebra residual. "
        f"Caption: {stage_recon}"
    )


def test_two_stage_attribution_section_renders(performance_app: AppTest) -> None:
    """Two-Stage Attribution section must render with three metric tiles. Pinned: Phase 9.

    Verifies that the new Stage 1 / Stage 2 / Total tiles are present and that
    switching the BF window radio updates the tile values (wired to same radio).
    Skipped when no portfolio data exists (local empty-DB mode).
    """
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")

    metric_labels = [m.label for m in performance_app.metric]
    assert "Stage 1: SAA Design" in metric_labels, (
        f"'Stage 1: SAA Design' tile not found in metric labels: {metric_labels}"
    )
    assert "Stage 2: Implementation" in metric_labels, (
        f"'Stage 2: Implementation' tile not found in metric labels: {metric_labels}"
    )
    assert "Total: Portfolio vs. 60/40" in metric_labels, (
        f"'Total: Portfolio vs. 60/40' tile not found in metric labels: {metric_labels}"
    )

    # Switching the BF period radio must change Stage 1 value (window-sensitivity check)
    at = AppTest.from_file("pages/2_Performance.py", default_timeout=120)
    at.run()
    if not at.metric:
        pytest.skip("No portfolio data in fresh AppTest instance")

    def _get_stage1(app) -> str | None:
        hits = [m for m in app.metric if m.label == "Stage 1: SAA Design"]
        return hits[0].value if hits else None

    bf_radios = [r for r in at.radio if r.key == "bf_period"]
    if not bf_radios:
        pytest.skip("bf_period radio not found")

    stage1_3m = _get_stage1(at)    # default is 3M

    bf_radios[0].set_value("SI")
    at.run()
    assert not at.exception, f"Page raised after bf_period switch to SI: {at.exception}"
    stage1_si = _get_stage1(at)

    assert stage1_3m is not None and stage1_si is not None, (
        "Stage 1 tile missing after radio switch"
    )
    assert stage1_3m != stage1_si, (
        f"Stage 1 value unchanged after switching 3M → SI: both = {stage1_3m!r}. "
        "Window filtering may have collapsed — check _load_attribution or _benchmark_period_return."
    )


# ── Phase 39 — polish regression pins ────────────────────────────────────────

def test_kpi_banner_shows_vs_blended(performance_app: AppTest) -> None:
    """Summary banner must show vs-blended comparison first. Pinned: Phase 39."""
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    banner_texts = [m.value for m in performance_app.markdown]
    assert any("vs blended" in t for t in banner_texts), (
        "KPI banner missing 'vs blended' — possible Phase 39 regression (Item 5)"
    )


def test_kpi_banner_hyphenated_day(performance_app: AppTest) -> None:
    """Summary banner must use hyphenated '-day inception period'. Pinned: Phase 39."""
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    banner_texts = [m.value for m in performance_app.markdown]
    assert any("-day inception period" in t for t in banner_texts), (
        "KPI banner missing hyphenated '-day inception period' — possible Phase 39 regression (Item 9)"
    )


def test_reconciliation_no_yahoo_finance(performance_app: AppTest) -> None:
    """Reconciliation caption must not mention Yahoo Finance. Pinned: Phase 39."""
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    captions = [c.value for c in performance_app.caption]
    recon = next((c for c in captions if "Reconciliation" in c), None)
    if recon is None:
        pytest.skip("Reconciliation caption not found — skipped in empty-DB mode")
    assert "Yahoo Finance" not in recon, (
        f"Reconciliation caption still mentions Yahoo Finance — Phase 39 regression (Item 3). "
        f"Caption starts: {recon[:120]}"
    )


def test_implementation_alpha_wording(performance_app: AppTest) -> None:
    """Since-inception caption must use 'isolate implementation alpha from SAA-design effects'.
    Pinned: Phase 39 (pin corrected Phase 48.1 — page always had 'to isolate', test was
    pinned against commit message wording 'isolates' which was never in the page code)."""
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    captions = [c.value for c in performance_app.caption]
    assert any("isolate implementation alpha from SAA-design effects" in c for c in captions), (
        "Since-inception caption missing 'isolate implementation alpha from SAA-design effects' — "
        "Phase 48.1 corrected pin (Phase 39 Item 8). "
        "Caption must read: '...is the more meaningful to isolate implementation alpha from SAA-design effects.'"
    )


def test_cache_rows_not_in_validation_expander(performance_app: AppTest) -> None:
    """Operational cache metadata must be absent from the page. Pinned: Phase 39."""
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    all_markdown = [m.value for m in performance_app.markdown]
    assert not any("Price cache rows" in m for m in all_markdown), (
        "Operational 'Price cache rows' metadata still visible — Phase 39 regression (Item 10)"
    )


# ── Attribution-sink hardening: Stage-1/2 gap-exclusion caption (#4b) ────────

def test_stage_reconciliation_gap_caption_absent_on_clean_demo(performance_app: AppTest) -> None:
    """The gap-exclusion caption explaining 'vs. Stage 2' drift must NOT
    render on the clean demo — no benchmark_gaps/price_gaps exist over the
    demo's full history, so the caption is inert on this invariant path."""
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    captions = [c.value for c in performance_app.caption]
    assert not any("quantifies that exclusion" in c for c in captions), (
        "Gap-exclusion caption rendered on the clean (no-gap) demo — should only "
        f"appear under a benchmark/price gap. Captions: {captions}"
    )


def test_stage_reconciliation_gap_caption_appears_under_gap(monkeypatch) -> None:
    """When brinson_fachler_period reports a benchmark or price gap, the
    page-local caption explaining the expected 'vs. Stage 2' drift must
    render — confirming the conditional actually fires, not just that it's
    absent on the gap-free path above.
    """
    import streamlit as st
    import src.attribution as attr_mod

    st.cache_data.clear()
    real_bf_period = attr_mod.brinson_fachler_period

    def _inject_gap(start, end):
        bf_df = real_bf_period(start, end)
        if bf_df.empty:
            return bf_df
        bf_df = bf_df.copy()
        bf_df.attrs["benchmark_gaps"] = [("Real Assets", "VNQ", "2025-06-01")]
        return bf_df

    monkeypatch.setattr(attr_mod, "brinson_fachler_period", _inject_gap)

    at = AppTest.from_file("pages/2_Performance.py", default_timeout=120)
    at.run()
    if not at.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    assert not at.exception, f"Page raised with an injected benchmark gap: {at.exception}"

    captions = [c.value for c in at.caption]
    assert any("quantifies that exclusion" in c for c in captions), (
        f"Expected gap-exclusion caption not found under an injected gap. Captions: {captions}"
    )


# ── PR A: naive/blended reference-benchmark gap exclude-and-flag ────────────
# get_sp500_series / get_custom_blended_series / get_naive_60_40_series
# (src/benchmarks.py) shared an unguarded price path that could silently
# fabricate a return on a benchmark price gap. These tests confirm the fix is
# invisible on the clean demo and that an injected gap surfaces the notice and
# NaN-safe "—" tiles rather than "nan bps"/"nan%" anywhere.

def test_naive_gap_notice_absent_on_clean_demo(performance_app: AppTest) -> None:
    """No naive-baseline gap notice or unavailable note on the clean (no-gap)
    demo — the fix is inert on this invariant path."""
    if not performance_app.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    warnings = [w.value for w in performance_app.warning]
    infos = [i.value for i in performance_app.info]
    assert not any("benchmark cannot be priced" in w for w in warnings), (
        f"Naive-baseline gap notice rendered on the clean demo. Warnings: {warnings}"
    )
    assert not any("could not be priced" in i for i in infos), (
        f"Baseline-unavailable note rendered on the clean demo. Infos: {infos}"
    )
    all_metrics = " ".join(str(m.value) for m in performance_app.metric).lower()
    assert "nan" not in all_metrics, f"A metric leaked 'nan': {all_metrics}"


def test_naive_gap_notice_and_unavailable_tiles_under_total_gap(monkeypatch) -> None:
    """A total naive-baseline gap (get_naive_series NaN-sentineled) must
    surface the shared benchmark_gap_notice, an explicit baseline-unavailable
    note, and the Stage 1 sleeve breakdown replaced by an unavailable note —
    with no metric anywhere rendering the literal string 'nan'.

    PRE-FIX: get_naive_60_40_series fabricated a flat 0% return on a gap (or
    get_sp500_series fabricated a flat-1.0 line) with no flag anywhere — the
    tiles below would have shown a plausible-looking, wrong number instead of
    this explicit unavailable state.
    """
    import streamlit as st
    import src.benchmarks as bm_mod

    st.cache_data.clear()

    def _nan_naive(kind, start, end):
        idx = pd.date_range(start, end or start, freq="D")
        out = pd.Series(float("nan"), index=idx)
        out.attrs["benchmark_gaps"] = [("60/40 Naive", "SPY", str(idx[-1].date()))]
        return out

    monkeypatch.setattr(bm_mod, "get_naive_series", _nan_naive)

    at = AppTest.from_file("pages/2_Performance.py", default_timeout=120)
    at.run()
    if not at.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    assert not at.exception, f"Page raised with an injected naive-baseline gap: {at.exception}"

    warnings = [w.value for w in at.warning]
    assert any("benchmark cannot be priced" in w for w in warnings), (
        f"Expected the shared benchmark_gap_notice under a total naive gap. Warnings: {warnings}"
    )

    infos = [i.value for i in at.info]
    assert any("could not be priced" in i and "Stage 1 and Total" in i for i in infos), (
        f"Expected the baseline-unavailable note for the Stage 1/Total tiles. Infos: {infos}"
    )
    assert any("Stage 1 sleeve breakdown unavailable" in i for i in infos), (
        f"Expected the Stage 1 sleeve breakdown to be replaced by an unavailable note. Infos: {infos}"
    )

    all_metrics = " ".join(str(m.value) for m in at.metric).lower()
    assert "nan" not in all_metrics, f"A metric leaked 'nan' under the injected gap: {all_metrics}"

    captions = [c.value for c in at.caption]
    assert any("Reconciliation unavailable" in c for c in captions), (
        f"Expected the reconciliation caption to degrade explicitly, got: {captions}"
    )


def test_blended_gap_notice_and_banner_unavailable_under_total_gap(monkeypatch) -> None:
    """A total custom-blended-benchmark gap must surface the shared
    benchmark_gap_notice at the top-of-page load site, and the summary
    banner's 'vs blended' figure must render '—', never 'nan bps' — proving
    the .attrs capture (before the *start_val multiply and the st.cache_data
    round-trip in _load_benchmarks) actually reaches the page.

    PRE-FIX: get_custom_blended_series fabricated a flat return with no flag
    on a total component-fetch failure — no warning would have rendered here
    and the banner would have shown a plausible, wrong bps figure.
    """
    import streamlit as st
    import src.benchmarks as bm_mod

    st.cache_data.clear()

    def _nan_blended(start, end):
        idx = pd.date_range(start, end or start, freq="D")
        out = pd.Series(float("nan"), index=idx)
        out.attrs["benchmark_gaps"] = [("US Large Core", "SPY", str(idx[-1].date()))]
        return out

    monkeypatch.setattr(bm_mod, "get_custom_blended_series", _nan_blended)

    at = AppTest.from_file("pages/2_Performance.py", default_timeout=120)
    at.run()
    if not at.metric:
        pytest.skip("No portfolio data — skipped in local/empty-DB mode")
    assert not at.exception, f"Page raised with an injected blended gap: {at.exception}"

    warnings = [w.value for w in at.warning]
    assert any("benchmark cannot be priced" in w for w in warnings), (
        f"Expected the shared benchmark_gap_notice for the blended gap. Warnings: {warnings}"
    )

    banner = " ".join(str(m.value) for m in at.markdown)
    assert "nan" not in banner.lower(), f"Banner leaked 'nan': {banner}"
    assert "—" in banner, f"Expected the NaN-safe '—' sink somewhere in the banner: {banner}"
