"""Tests for src/benchmarks.py — Custom Blended benchmark and per-sleeve benchmark returns.

Neither get_custom_blended_series() nor get_sleeve_benchmark_returns() had a dedicated
correctness test before this file: every reference to get_custom_blended_series() elsewhere
in the suite either mocks it outright (test_factors.py, test_reports.py) or consumes it as
an opaque data input for a different function's identity check (test_attribution.py,
test_risk_metrics.py), so the allocation/mark-to-market arithmetic itself was never
independently verified.

── Benchmark-gap tripwire ────────────────────────────────────────────────────
Mirrors test_attribution.py's portfolio-side price-gap suite for the BENCHMARK
side (r_b): brinson_fachler()'s internal algebra check (sum of effects ==
r_p_total - r_b_total) computes r_b_total from the same benchmark-return dict
as the effects, so it holds algebraically for ANY r_b vector — the residual is
identically R_b x (sum(w_b) - sum(w_p)), which the pipeline pins to zero by
renormalizing both weight vectors. No identity-style test can therefore detect
a silently-zeroed benchmark return; these tests instead construct a genuine
benchmark data gap and check get_sleeve_benchmark_returns()/
brinson_fachler_period()'s behavior in response.

Each test's docstring states what the assertion would find against the PRE-FIX
code (a fabricated flat-1.0 series reading as a 0.0 return, no attrs flag, the
sleeve kept in the decomposition with its entire benchmark return booked as
selection skill) — that is the proof the test actually catches the bug, not
just a description of the fix.
"""
from __future__ import annotations

import logging
import sys
import pathlib

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import src.benchmarks as bm

# Committed-demo-data window shared by the pipeline tests below (same window as
# the r_b tripwire at the bottom of this file — covered end-to-end by the
# committed price cache, no live fetch required).
_W = ("2025-05-01", "2026-06-10")


class _FakeConn:
    """Minimal stand-in for the sqlite3 connection context manager, returning
    hand-picked asset_classes rows instead of touching a real database."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, *args, **kwargs):
        return self

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fail_fetch_for(monkeypatch, *tickers):
    """Patch src.benchmarks' module-local get_prices to raise for the given
    tickers only, delegating everything else to the real function — a targeted
    benchmark data gap. src.attribution's own get_prices binding is untouched,
    so the portfolio side of the pipeline stays healthy."""
    real_get_prices = bm.get_prices

    def _targeted(ticker, *args, **kwargs):
        if ticker in tickers:
            raise RuntimeError(f"simulated fetch failure for {ticker}")
        return real_get_prices(ticker, *args, **kwargs)

    monkeypatch.setattr(bm, "get_prices", _targeted)


def _bf_or_skip(*window):
    """brinson_fachler_period with the standard data-unavailable skip guards
    (mirrors the portfolio-side gap tests in test_attribution.py).

    Use ONLY for the PRE-injection probe. Post-injection calls must be bare —
    after the probe has proven data availability, an exception or empty frame
    can only come from the code under test (e.g. one-sided renormalization
    tripping the BF algebra assert) and must FAIL the test, not skip it.
    """
    from src.attribution import brinson_fachler_period

    try:
        bf_df = brinson_fachler_period(*window)
    except Exception as exc:
        pytest.skip(f"BF data unavailable: {exc}")
    if bf_df.empty:
        pytest.skip("BF result empty — skipped in local/empty-DB mode")
    return bf_df


def _series_or_skip(fn, *args):
    """Pre-injection probe for a benchmark-series builder, mirroring
    _bf_or_skip. Use ONLY before injecting a failure — post-injection calls
    must be bare so a regression fails the test, not skips it."""
    try:
        s = fn(*args)
    except Exception as exc:
        pytest.skip(f"Benchmark data unavailable: {exc}")
    if s.dropna().empty:
        pytest.skip("Series empty — skipped in local/empty-DB mode")
    return s


def test_custom_blended_series_hand_calculation(monkeypatch):
    """Hand-calculated 2-sleeve blend, fully synthetic (no DB, no price fetch).

    SleeveA (60%) tracks TICKA: 100 -> 110 -> 121 (+10%, +10%).
    SleeveB (40%) tracks TICKB: 50 -> 45 -> 40.5 (-10%, -10%).

    Day 0: $1 = 0.60 in TICKA (0.006 sh) + 0.40 in TICKB (0.008 sh).
    Day 1: 0.006*110 + 0.008*45   = 0.66 + 0.36  = 1.02
    Day 2: 0.006*121 + 0.008*40.5 = 0.726 + 0.324 = 1.05
    """
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    price_a = pd.Series([100.0, 110.0, 121.0], index=dates)
    price_b = pd.Series([50.0, 45.0, 40.5], index=dates)

    monkeypatch.setattr(bm, "_SLEEVE_BENCHMARKS", {
        "SleeveA": [("TICKA", 1.0)],
        "SleeveB": [("TICKB", 1.0)],
    })
    monkeypatch.setattr(bm, "get_connection", lambda: _FakeConn([
        {"name": "SleeveA", "target_weight": 0.6},
        {"name": "SleeveB", "target_weight": 0.4},
    ]))

    def _fake_price_series(ticker, start_date, end_date, col="adj_close"):
        return {"TICKA": price_a, "TICKB": price_b}[ticker]

    monkeypatch.setattr(bm, "_get_price_series", _fake_price_series)

    result = bm.get_custom_blended_series("2026-01-01", "2026-01-03")

    expected = [1.00, 1.02, 1.05]
    assert result.tolist() == pytest.approx(expected, abs=1e-9), (
        f"get_custom_blended_series hand-calculation mismatch: "
        f"got {result.tolist()}, expected {expected}"
    )


# ── Benchmark-gap mechanism tests (exclude-and-flag, mirroring PR #52) ───────


def test_component_gap_emits_nan_sentinel_and_flag(monkeypatch, caplog):
    """A sleeve whose only benchmark component cannot be priced carries an
    explicit NaN sentinel plus a (sleeve, ticker, date) record on
    attrs['benchmark_gaps'], and the failure is logged naming the ticker.

    PRE-FIX: the column came back as a flat-1.0 series reading as an exact 0.0
    return (the total_frac==0 fabrication), there was no attrs flag and no
    warning — all three assertions fail against that code.
    """
    _fail_fetch_for(monkeypatch, "QUAL")

    with caplog.at_level(logging.WARNING):
        df = bm.get_sleeve_benchmark_returns(*_W)

    assert df["US Large Quality"].isna().all(), (
        "expected an explicit NaN sentinel for an unpriceable benchmark sleeve, "
        f"got values like {df['US Large Quality'].iloc[-1]!r} (a fabricated flat "
        "return)"
    )
    gaps = df.attrs.get("benchmark_gaps", [])
    assert any(s == "US Large Quality" and t == "QUAL" for s, t, _ in gaps), (
        f"expected QUAL's gap recorded in attrs['benchmark_gaps'], got: {gaps}"
    )
    assert any("QUAL" in rec.message for rec in caplog.records), (
        "expected a logged warning naming the unpriceable benchmark ticker"
    )


def test_benchmark_gap_excludes_sleeve_and_flags(monkeypatch):
    """A benchmark-gapped strategic sleeve is EXCLUDED from that period's
    attribution (both weight vectors renormalized over the surviving universe,
    so the BF identity holds exactly) and flagged on attrs['benchmark_gaps'].

    PRE-FIX: the sleeve stayed in the frame with w_b > 0 and a fabricated
    r_b == 0.0, booking its entire benchmark return (~26% for QUAL over this
    window) as phantom selection skill and overstating active return by
    w_b x r_b_true (~403 bps), with no flag anywhere — the exclusion and attrs
    assertions fail against that code.
    """
    from src.attribution import brinson_fachler_period

    _bf_or_skip(*_W)  # probe: committed data present before injecting
    _fail_fetch_for(monkeypatch, "QUAL")

    bf_df = brinson_fachler_period(*_W)   # bare: a failure here IS the bug

    assert "US Large Quality" not in bf_df["sleeve"].tolist(), (
        "expected the benchmark-gapped sleeve to be excluded from the period, "
        f"got rows: {bf_df[bf_df['sleeve'] == 'US Large Quality'][['sleeve', 'w_b', 'r_b']].to_dict('records')}"
    )
    gaps = bf_df.attrs.get("benchmark_gaps") or []
    assert any(s == "US Large Quality" and t == "QUAL" for s, t, _ in gaps), (
        f"expected QUAL's benchmark gap in .attrs['benchmark_gaps'], got: {gaps}"
    )
    # Both weight vectors renormalize over the surviving universe — this is
    # what keeps the BF algebra check exact after exclusion.
    assert abs(bf_df["w_p"].sum() - 1.0) < 1e-6
    assert abs(bf_df["w_b"].sum() - 1.0) < 1e-6
    active = float(
        (bf_df["w_p"] * bf_df["r_p"]).sum() - (bf_df["w_b"] * bf_df["r_b"]).sum()
    )
    assert abs(float(bf_df["total_effect"].sum()) - active) < 1e-4, (
        "BF identity must hold over the surviving universe"
    )
    # And no fabricated zero remains anywhere.
    assert bf_df[(bf_df["w_b"] > 0) & (bf_df["r_b"] == 0.0)].empty


def test_partial_component_gap_degrades_and_flags(monkeypatch):
    """A multi-component sleeve (Real Assets = VNQ 0.6 / DBC 0.4) with ONE
    unpriceable component degrades to its surviving components — retained in
    the attribution, NOT excluded — but the substitution is flagged on
    attrs['benchmark_gaps'] (the BIL-style degrade-and-flag tier).

    PRE-FIX: the renormalization happened silently — a plausible NONZERO wrong
    r_b invisible to the exact-zero tripwire, with no flag anywhere — so the
    attrs assertions fail against that code.
    """
    _bf_or_skip(*_W)  # probe before injecting
    _fail_fetch_for(monkeypatch, "DBC")

    df = bm.get_sleeve_benchmark_returns(*_W)
    assert not df["Real Assets"].isna().all(), (
        "partial component failure must degrade to the surviving components, "
        "not blank the whole sleeve"
    )
    src_gaps = df.attrs.get("benchmark_gaps", [])
    assert any(s == "Real Assets" and t == "DBC" for s, t, _ in src_gaps), (
        f"expected DBC's gap recorded at the source, got: {src_gaps}"
    )

    from src.attribution import brinson_fachler_period

    bf_df = brinson_fachler_period(*_W)   # bare: a failure here IS the bug
    assert "Real Assets" in bf_df["sleeve"].tolist(), (
        "a partially-priceable benchmark blend must not exclude the sleeve"
    )
    gaps = bf_df.attrs.get("benchmark_gaps") or []
    assert any(s == "Real Assets" and t == "DBC" for s, t, _ in gaps), (
        f"expected DBC's benchmark gap surfaced on the BF frame, got: {gaps}"
    )


def test_bil_benchmark_gap_degrades_without_exclusion(monkeypatch):
    """A BIL (cash-sleeve benchmark) gap is flagged but NEVER excludes: cash's
    benchmark return is not consumed by the decomposition (cash leaves the BF
    universe before brinson_fachler) and purging it would zero the cash-drag
    bridge. All strategic sleeves stay present and cash_drag is unchanged —
    mirroring the portfolio side's BIL degrade-not-exclude convention.

    PRE-FIX: the cash column was zero-filled with no flag, so the attrs
    assertion fails against that code (the retention assertions pass either
    way — they pin the exemption so a careless purge can't regress it).
    """
    baseline = _bf_or_skip(*_W)
    base_drag = float(baseline.attrs.get("cash_drag", 0.0))

    _fail_fetch_for(monkeypatch, "BIL")

    df = bm.get_sleeve_benchmark_returns(*_W)
    assert df["Cash / SPAXX"].isna().all(), (
        "expected the NaN sentinel for the unpriceable cash benchmark"
    )

    from src.attribution import brinson_fachler_period

    bf_df = brinson_fachler_period(*_W)   # bare: a failure here IS the bug
    assert len(bf_df) == len(baseline), (
        "a cash-benchmark gap must not exclude any sleeve: expected "
        f"{len(baseline)} sleeves, got {len(bf_df)}: {sorted(bf_df['sleeve'])}"
    )
    assert float(bf_df.attrs.get("cash_drag", 0.0)) == pytest.approx(
        base_drag, abs=1e-12
    ), "cash drag must be untouched by a benchmark-side BIL gap"
    gaps = bf_df.attrs.get("benchmark_gaps") or []
    assert any(s == "Cash / SPAXX" and t == "BIL" for s, t, _ in gaps), (
        f"expected the BIL benchmark gap recorded in attrs, got: {gaps}"
    )


def test_total_benchmark_outage_yields_sentinels_and_empty_attribution(monkeypatch):
    """When EVERY benchmark component fails to price, every sleeve carries the
    NaN sentinel with a gap record, and brinson_fachler_period returns an
    EMPTY frame (all strategic sleeves gapped -> nothing left to attribute)
    that still carries attrs['benchmark_gaps'].

    PRE-FIX: every sleeve came back as exactly 0.0 (this test's predecessor,
    test_degenerate_benchmark_pins_current_zero_fill_behavior, PINNED that
    fabrication), and the pipeline silently collapsed active return to R_p
    with the identity still green — the sentinel, gap, and empty-frame
    assertions all fail against that code.
    """
    _bf_or_skip(*_W)  # probe before injecting

    def _always_fails(ticker, *args, **kwargs):
        raise RuntimeError("simulated fetch failure")

    monkeypatch.setattr(bm, "get_prices", _always_fails)

    df = bm.get_sleeve_benchmark_returns(*_W)
    assert df.iloc[-1].isna().all(), (
        f"expected NaN sentinels for a total benchmark outage, got:\n{df.iloc[-1]}"
    )
    gapped_sleeves = {s for s, _, _ in df.attrs.get("benchmark_gaps", [])}
    assert gapped_sleeves == set(bm._SLEEVE_BENCHMARKS), (
        f"expected every sleeve flagged, got: {sorted(gapped_sleeves)}"
    )

    from src.attribution import brinson_fachler_period

    bf_df = brinson_fachler_period(*_W)
    assert bf_df.empty, (
        "with every strategic sleeve benchmark-gapped there is nothing to "
        f"attribute — expected an empty frame, got {len(bf_df)} rows"
    )
    assert bf_df.attrs.get("benchmark_gaps"), (
        "the empty frame must still surface the benchmark gaps"
    )


def test_missing_benchmark_mapping_for_weighted_sleeve_asserts(monkeypatch):
    """A sleeve carrying real benchmark weight (w_b > 0) whose name is missing
    from the static _SLEEVE_BENCHMARKS map is CONFIG DRIFT, not a per-period
    data gap — brinson_fachler_period fails loudly instead of zero-filling.

    PRE-FIX: the missing column was silently zero-filled via
    bm_returns_raw.get(s, 0.0) and the decomposition completed with a
    fabricated r_b — pytest.raises finds no error against that code.
    """
    _bf_or_skip(*_W)  # probe: needs holdings for the pipeline to reach the check

    import src.attribution as attr_mod

    real_gsbr = attr_mod.get_sleeve_benchmark_returns

    def _drop_column(start, end):
        return real_gsbr(start, end).drop(columns=["US Large Quality"])

    monkeypatch.setattr(attr_mod, "get_sleeve_benchmark_returns", _drop_column)

    with pytest.raises(AssertionError, match="_SLEEVE_BENCHMARKS"):
        attr_mod.brinson_fachler_period(*_W)


def test_unknown_sleeve_retained_with_na_marker(monkeypatch):
    """A portfolio sleeve with NO benchmark (w_b == 0, the 'Unknown' bucket for
    a ticker missing from securities) is RETAINED — its real return belongs in
    the decomposition and its r_b placeholder cancels algebraically — and is
    listed on attrs['no_benchmark_sleeves'] so render surfaces show its
    benchmark return as N/A instead of a fabricated 0.00%.

    PRE-FIX: the sleeve was retained with the same math, but nothing marked
    the fabricated display value — the attrs assertion fails against that code.
    """
    import src.attribution as attr_mod
    from src.db import get_connection as real_get_connection

    _bf_or_skip(*_W)  # probe before injecting

    # Pick a real (non-SPAXX) holding at window start to orphan from securities.
    with real_get_connection() as conn:
        row = conn.execute(
            """SELECT ticker,
                      SUM(CASE WHEN LOWER(action)='buy' THEN shares ELSE -shares END) AS net
               FROM trades
               WHERE trade_date <= ?
                 AND ticker != 'SPAXX'
                 AND (lot_source IS NULL OR lot_source != 'drip')
               GROUP BY ticker HAVING net > 0
               ORDER BY ticker LIMIT 1""",
            (_W[0],),
        ).fetchone()
    if row is None:
        pytest.skip("no non-cash holdings at window start")
    orphan = row["ticker"]

    class _Rows:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class _OrphanConn:
        """Delegates to the real connection but drops the orphaned ticker from
        the securities join, so it lands in the 'Unknown' sleeve."""

        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *params):
            cur = self._conn.execute(sql, *params)
            if "FROM securities" in sql:
                return _Rows([r for r in cur.fetchall() if r["ticker"] != orphan])
            return cur

        def __enter__(self):
            self._conn.__enter__()
            return self

        def __exit__(self, *args):
            return self._conn.__exit__(*args)

    monkeypatch.setattr(
        attr_mod, "get_connection", lambda: _OrphanConn(real_get_connection())
    )

    bf_df = attr_mod.brinson_fachler_period(*_W)   # bare: a failure here IS the bug

    assert "Unknown" in bf_df["sleeve"].tolist(), (
        "a no-benchmark sleeve must be retained, not excluded"
    )
    row_u = bf_df[bf_df["sleeve"] == "Unknown"].iloc[0]
    assert row_u["w_b"] == 0.0
    assert bf_df.attrs.get("no_benchmark_sleeves") == ["Unknown"], (
        "expected the no-benchmark sleeve marked for N/A rendering, got "
        f"attrs['no_benchmark_sleeves'] = {bf_df.attrs.get('no_benchmark_sleeves')!r}"
    )
    # Retention must not disturb the identity: both weight vectors still sum
    # to 1.0 and the algebra check closes over the full row set.
    assert abs(bf_df["w_p"].sum() - 1.0) < 1e-6
    assert abs(bf_df["w_b"].sum() - 1.0) < 1e-6
    active = float(
        (bf_df["w_p"] * bf_df["r_p"]).sum() - (bf_df["w_b"] * bf_df["r_b"]).sum()
    )
    assert abs(float(bf_df["total_effect"].sum()) - active) < 1e-4


def test_no_benchmark_gaps_on_committed_demo_windows():
    """Committed demo data has no benchmark gap and no no-benchmark sleeve on
    the shared window: the new attrs are present-but-empty and all 9 strategic
    sleeves attribute normally — the fix is a no-op on the demo's numbers.

    PRE-FIX: the attrs keys did not exist at all, so the equality assertions
    fail against that code (None != []).
    """
    bf_df = _bf_or_skip(*_W)

    assert bf_df.attrs.get("benchmark_gaps") == [], (
        f"unexpected benchmark gaps on committed demo data: "
        f"{bf_df.attrs.get('benchmark_gaps')!r}"
    )
    assert bf_df.attrs.get("no_benchmark_sleeves") == [], (
        f"unexpected no-benchmark sleeves on committed demo data: "
        f"{bf_df.attrs.get('no_benchmark_sleeves')!r}"
    )
    assert len(bf_df) == 9


def test_real_benchmark_sleeves_never_silently_zero():
    """TRIPWIRE: no strategic sleeve's benchmark return is silently zero over the
    portfolio's full since-inception window, using the committed demo.db price cache
    (no live fetch).

    brinson_fachler()'s internal algebra check cannot distinguish a correct r_b from a
    silently-zeroed one -- the sum-of-effects identity holds either way (see module
    docstring). This test instead inspects r_b directly, per sleeve, for the real
    pipeline: a genuine multi-month benchmark return netting to EXACTLY 0.0 is not a
    real market outcome for any of these tickers (SPY/QUAL/IWD/IWM/EFA/EEM/IEF/TIP/
    VNQ+DBC/BIL), so an exact-zero r_b for a sleeve carrying real benchmark weight
    (w_b > 0) is the fingerprint of the degenerate-benchmark bug, not a coincidence.
    Belt-and-suspenders alongside the mechanism tests above: those prove the handling
    of an injected gap; this proves the committed data needs none of it.
    """
    from src.attribution import brinson_fachler_period

    try:
        bf_df = brinson_fachler_period("2025-05-01", "2026-06-10")
    except Exception as exc:
        pytest.skip(f"BF data unavailable: {exc}")

    if bf_df.empty:
        pytest.skip("BF result empty — skipped in local/empty-DB mode")

    zeroed = bf_df[(bf_df["w_b"] > 0) & (bf_df["r_b"] == 0.0)]
    assert zeroed.empty, (
        "Sleeve(s) with real benchmark weight but r_b exactly 0.0 -- this is the "
        "degenerate-benchmark fingerprint (a benchmark price fetch silently failed and "
        f"zero-filled instead of erroring):\n{zeroed[['sleeve', 'w_b', 'r_b']]}"
    )


# ── PR A: reference-benchmark (naive 60/40, custom blended, S&P 500) gap
# mechanism ─────────────────────────────────────────────────────────────────
# get_sp500_series / get_custom_blended_series / get_naive_60_40_series shared
# an UNGUARDED price path (the old _get_price_series): a stale-cache tail or a
# total fetch failure silently ffilled/zero-filled a fabricated return with no
# flag, inflating the default Stage-1/Total tiles and swinging blended alpha.
# _get_price_series is now itself coverage-gated (mirrors _component_series
# above), and each consumer flags a resulting gap on .attrs["benchmark_gaps"].
#
# Each test's docstring states what it would find against the PRE-FIX code —
# the proof it actually catches the fabrication, not just describes the fix.

def test_get_price_series_full_coverage_matches_old_implementation():
    """On full-coverage demo data, the coverage-gated _get_price_series must
    match the OLD (pre-fix) direct reindex/ffill/bfill implementation it
    replaced byte-for-byte — the coverage gate is a pure safety net, invisible
    whenever the data is actually complete."""
    ticker, start, end = "SPY", _W[0], _W[1]
    gated = bm._get_price_series(ticker, start, end)
    assert not gated.attrs.get("benchmark_gap_bounds"), (
        "expected full coverage on the committed demo window, got a gap: "
        f"{gated.attrs.get('benchmark_gap_bounds')}"
    )

    date_range = pd.date_range(start=start, end=end, freq="D")
    p = bm.get_prices(ticker, start, end)
    p.index = pd.to_datetime(p.index)
    expected = p["adj_close"].fillna(p["close"]).reindex(date_range).ffill().bfill()

    assert gated.equals(expected), (
        "coverage-gated _get_price_series diverges from the pre-fix "
        "reindex/ffill/bfill formula on full-coverage data"
    )


def test_get_price_series_gap_yields_nan_and_gap_bounds(monkeypatch, caplog):
    """A component that cannot be fetched at all yields a NaN sentinel over
    the whole window, with the gap bound(s) recorded on
    .attrs['benchmark_gap_bounds'] and a logged warning naming the ticker.

    PRE-FIX: the except-Exception branch returned an EMPTY (not NaN-filled,
    not flagged) Series indistinguishable from other edge cases — this proves
    the new NaN-sentinel-plus-attrs contract.
    """
    _series_or_skip(bm._get_price_series, "SPY", _W[0], _W[1])   # probe
    _fail_fetch_for(monkeypatch, "SPY")

    with caplog.at_level(logging.WARNING):
        out = bm._get_price_series("SPY", _W[0], _W[1])

    assert out.isna().all(), f"expected an all-NaN sentinel, got:\n{out.tail()}"
    assert out.attrs.get("benchmark_gap_bounds"), (
        "expected the gap bound(s) recorded in attrs['benchmark_gap_bounds']"
    )
    assert any("SPY" in rec.message for rec in caplog.records)


def test_get_sp500_series_total_gap_yields_nan_sentinel_and_flag(monkeypatch):
    """SPY totally unpriceable: get_sp500_series must carry a NaN sentinel and
    flag it on .attrs['benchmark_gaps'], never a flat-1.0 fabricated line.

    PRE-FIX: the old _get_price_series's except branch returned an all-NaN
    but UNFLAGGED series; first_valid_index() found nothing, so
    get_sp500_series happened to return that same unflagged NaN series — with
    no gap signal anywhere for a caller to detect and warn on.
    """
    _series_or_skip(bm.get_sp500_series, *_W)   # probe
    _fail_fetch_for(monkeypatch, "SPY")

    out = bm.get_sp500_series(*_W)   # bare: a failure here IS the bug

    assert out.isna().all(), f"expected a NaN sentinel, got:\n{out.tail()}"
    gaps = out.attrs.get("benchmark_gaps", [])
    assert any(t == "SPY" for _, t, _ in gaps), (
        f"expected SPY's gap recorded in attrs['benchmark_gaps'], got: {gaps}"
    )


def test_naive_60_40_total_failure_yields_nan_sentinel_and_flag(monkeypatch):
    """Both SPY and AGG unpriceable: get_naive_60_40_series must carry an
    explicit NaN sentinel and flag both legs, never the fabricated flat 0%
    return pct_change().fillna(0.0) produces on an empty/stale series.

    PRE-FIX: an empty/all-NaN price series' .pct_change().fillna(0.0) is
    EXACTLY 0.0 every day — a fabricated "flat, zero-return" baseline with no
    flag anywhere — inflating or sign-flipping the displayed Stage 1/Total
    tiles depending on the portfolio's own true active return.
    """
    _series_or_skip(bm.get_naive_60_40_series, *_W)   # probe
    _fail_fetch_for(monkeypatch, "SPY", "AGG")

    out = bm.get_naive_60_40_series(*_W)   # bare: a failure here IS the bug

    assert out.isna().all(), f"expected a NaN sentinel, got:\n{out.tail()}"
    gaps = out.attrs.get("benchmark_gaps", [])
    assert any(t == "SPY" for _, t, _ in gaps) and any(t == "AGG" for _, t, _ in gaps), (
        f"expected both SPY and AGG gaps recorded, got: {gaps}"
    )


def test_naive_60_40_partial_failure_renormalizes_and_flags(monkeypatch):
    """AGG unpriceable, SPY fine: the naive series degrades to 100% SPY (the
    surviving leg's weight renormalizes to 1.0) and the AGG gap is flagged —
    never silently kept at a diluted 60% weight against a fabricated 0% AGG.

    PRE-FIX: agg_ret was a fabricated flat 0.0 return series blended in at its
    full 40% weight (0.6*spy_ret + 0.4*0.0), UNDERSTATING the naive baseline's
    true sensitivity to the surviving SPY leg by 40%, with no flag.
    """
    _series_or_skip(bm.get_naive_60_40_series, *_W)   # probe
    spy_only = _series_or_skip(bm.get_sp500_series, *_W)

    _fail_fetch_for(monkeypatch, "AGG")

    out = bm.get_naive_60_40_series(*_W)   # bare: a failure here IS the bug

    assert not out.isna().all(), "AGG-only failure must degrade, not blank the series"
    gaps = out.attrs.get("benchmark_gaps", [])
    assert any(t == "AGG" for _, t, _ in gaps), f"expected AGG's gap flagged, got: {gaps}"
    assert not any(t == "SPY" for _, t, _ in gaps), "SPY must not be flagged — it's fine"

    # Renormalized to 100% SPY: the naive series must now match pure SPY, not
    # a diluted 60%-weighted SPY-only return.
    common = out.dropna().index.intersection(spy_only.dropna().index)
    assert len(common) >= 10
    np.testing.assert_allclose(
        out.loc[common].values, spy_only.loc[common].values, rtol=1e-6,
        err_msg="AGG-gapped naive series should renormalize to 100% SPY",
    )


def test_custom_blended_component_gap_drops_and_flags(monkeypatch):
    """A real-weight component (EFA, International Developed ~20%) unpriceable:
    get_custom_blended_series must flag it on .attrs['benchmark_gaps'] — not
    silently renormalize the survivors with no signal anywhere.

    PRE-FIX: the survivor renormalization already happened (implicitly, via
    the day-0 share allocation), but with NO flag anywhere — a plausible,
    nonzero, WRONG blended benchmark value invisible to any downstream
    reconciliation check.
    """
    _series_or_skip(bm.get_custom_blended_series, *_W)   # probe
    _fail_fetch_for(monkeypatch, "EFA")

    out = bm.get_custom_blended_series(*_W)   # bare: a failure here IS the bug

    assert not out.isna().all(), "a single component gap must degrade, not blank the series"
    gaps = out.attrs.get("benchmark_gaps", [])
    assert any(t == "EFA" for _, t, _ in gaps), (
        f"expected EFA's gap recorded in attrs['benchmark_gaps'], got: {gaps}"
    )


def test_custom_blended_total_failure_yields_nan_sentinel(monkeypatch):
    """Every benchmark component unpriceable: get_custom_blended_series must
    carry an explicit NaN sentinel, never the fabricated flat $0.00 line the
    un-normalized fallthrough (daily_value stays all-zero; first_val <= 0 so
    it's never rescaled) used to produce.

    PRE-FIX: the returned series was a flat 0.0 for every day — read
    downstream as a "-100%"/"nan bps" figure once any consumer divided by
    it — with no flag anywhere.
    """
    _series_or_skip(bm.get_custom_blended_series, *_W)   # probe

    def _always_fails(ticker, *args, **kwargs):
        raise RuntimeError("simulated total fetch failure")

    monkeypatch.setattr(bm, "get_prices", _always_fails)

    out = bm.get_custom_blended_series(*_W)   # bare: a failure here IS the bug

    assert out.isna().all(), f"expected a NaN sentinel, got:\n{out.tail()}"
    gapped_tickers = {t for _, t, _ in out.attrs.get("benchmark_gaps", [])}
    all_tickers = {t for comps in bm._SLEEVE_BENCHMARKS.values() for t, _ in comps}
    assert gapped_tickers == all_tickers, (
        f"expected every component ticker flagged, got: {gapped_tickers} vs {all_tickers}"
    )
