"""Benchmark series construction for performance attribution."""
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date

import pandas as pd

from src.db import get_connection
from src.prices import get_prices, total_return_series
from src.sleeve_config import sleeve_benchmarks as _derive_benchmarks

# Coverage tolerance for benchmark components, mirroring the portfolio side's
# _first_adj_price/_last_adj_price 5-day tolerance in src/attribution.py: a
# component with no real price within this many days of each window bound is
# an explicit data gap, never folded in as a flat/fabricated series. The end
# bound matches the portfolio convention exactly (last price in [end-5, end]);
# the start bound is forward-tolerant (first price in [start, start+5]) by
# construction, since get_prices is bounded below by the window start and
# cannot return the prior close the portfolio side would back-fill from.
_COVERAGE_WINDOW_DAYS = 5

# Sleeve → benchmark mapping is DERIVED from asset_classes.benchmark_ticker at
# call time (src.sleeve_config), so it describes whatever book this process is
# pointed at — 9 sleeves in personal mode, 12 in demo — instead of freezing one
# taxonomy into a module constant. Real Assets' "VNQ (60%) + DBC (40%)" blend is
# parsed from that column (DJP, the original ETN leg, was delisted May 2020; the
# seed carries DBC). The parser fails loud on any sleeve whose benchmark_ticker
# is empty or unparseable — the coherence check that replaced the old hardcoded-
# map-vs-DB drift assertion.
def _sleeve_benchmarks() -> dict[str, list[tuple[str, float]]]:
    """{sleeve -> [(benchmark ticker, weight), …]} for the current book.

    Every key is a live DB sleeve and every value a parsed benchmark spec, so a
    stale key can no longer resolve to weight 0.0 and silently drop its leg from
    the blend (the +120bps drift the old drift-guard existed to catch). Cash /
    SPAXX is included (BIL) exactly as the hardcoded map had it; it carries
    target 0.0 so it contributes nothing to the weighted blend.
    """
    return _derive_benchmarks(include_cash=True)


def _get_price_series(ticker: str, start_date: str, end_date: str,
                      col: str = "adj_close") -> pd.Series:
    """Return a daily price series, coverage-gated exactly like
    _component_series below — never a flat/fabricated fill on a missing or
    stale-tail component (the reference-benchmark mirror of that sleeve-side
    fix). The window bound(s) at which coverage failed, if any, are attached
    to the returned series' .attrs["benchmark_gap_bounds"] (an empty list
    when fully covered) so callers can flag the gap without this function
    changing its plain-Series return contract — a NaN sentinel over the
    period is returned in that case, never a fabricated flat line.
    """
    date_range = pd.date_range(start=start_date, end=end_date, freq="D")
    series, missing_bounds = _component_series(ticker, start_date, end_date)
    out = series if series is not None else pd.Series(float("nan"), index=date_range)
    out.attrs["benchmark_gap_bounds"] = missing_bounds
    return out


def _component_series(
    ticker: str, start_date: str, end_date: str
) -> tuple[pd.Series | None, list[str]]:
    """Daily ffilled adj_close series for one benchmark component, plus the
    window bound(s) — ISO dates — at which the component has no real price
    within _COVERAGE_WINDOW_DAYS. An empty gap list means full coverage.

    Returns (None, [bounds]) — never a flat or fabricated series — when the
    component cannot be priced near a bound, whether because the fetch raised,
    came back empty, or a stale cache silently ends inside the window
    (get_prices swallows gap-fetch failures, so coverage is judged on the
    returned prices, not on whether the fetch raised). Mirrors the portfolio
    side's _first_adj_price/_last_adj_price None contract in src/attribution.py.
    """
    date_range = pd.date_range(start=start_date, end=end_date, freq="D")
    if ticker == "SPAXX":
        return pd.Series(1.0, index=date_range), []
    try:
        p = get_prices(ticker, start_date, end_date)
    except Exception:
        logging.exception(
            "Benchmark price lookup failed for %s in [%s, %s]",
            ticker, start_date, end_date,
        )
        return None, [start_date, end_date]
    if p.empty:
        logging.warning(
            "No benchmark prices for %s in [%s, %s]", ticker, start_date, end_date
        )
        return None, [start_date, end_date]
    p.index = pd.to_datetime(p.index)
    # Uncached direct fetches can carry duplicate date labels (get_prices only
    # dedups on the cache-concat path) — dedup here so reindex can't raise.
    p = p[~p.index.duplicated(keep="last")]
    series = total_return_series(p)
    first_real = series.first_valid_index()
    last_real = series.last_valid_index()
    gaps: list[str] = []
    if first_real is None or (
        first_real - pd.Timestamp(start_date)
    ).days > _COVERAGE_WINDOW_DAYS:
        gaps.append(start_date)
    if last_real is None or (
        pd.Timestamp(end_date) - last_real
    ).days > _COVERAGE_WINDOW_DAYS:
        gaps.append(end_date)
    if gaps:
        logging.warning(
            "Benchmark component %s lacks price coverage near %s (window [%s, %s])",
            ticker, ", ".join(gaps), start_date, end_date,
        )
        return None, gaps
    return series.reindex(date_range).ffill().bfill(), gaps


def get_sp500_series(start_date: str, end_date: str | None = None) -> pd.Series:
    """
    Daily total-return series for SPY (adj_close), normalized so the first
    available value equals the portfolio's starting value on start_date.
    Returns a Series indexed by pd.Timestamp with dollar values starting at 1.0.
    Call site multiplies by starting portfolio value to align scales.

    A SPY price gap near a window bound (see _component_series) yields an
    explicit NaN-sentineled series flagged on .attrs["benchmark_gaps"] as
    (label, ticker, bound) 3-tuples — never a flat/fabricated line.
    """
    end = end_date or date.today().isoformat()
    series = _get_price_series("SPY", start_date, end, col="adj_close")
    gap_bounds = series.attrs.get("benchmark_gap_bounds", [])

    first_valid = series.first_valid_index()
    if first_valid is None or series[first_valid] == 0:
        out = series
    else:
        out = series / series[first_valid]   # normalized index starting at 1.0
    out.attrs["benchmark_gaps"] = [("S&P 500", "SPY", b) for b in gap_bounds]
    return out


def _benchmarks_for(sleeve_weights: dict[str, float]) -> dict[str, list[tuple[str, float]]]:
    """Return the current book's sleeve→benchmark map, asserting coherence.

    The map is DERIVED from asset_classes.benchmark_ticker, so it can no longer
    drift from the DB the way a hardcoded constant did: the +120bps failure
    (a renamed sleeve leaving a stale key that resolved to weight 0.0 and
    silently dropped its 20.41% leg) is structurally impossible when every key
    is a live DB sleeve. _sleeve_benchmarks() already raises on any sleeve whose
    benchmark_ticker is empty or unparseable; this adds the reverse coherence
    check — every WEIGHTED sleeve must appear in the map — so a strategic sleeve
    can never be priced against nothing. Works on either book: 9 sleeves in
    personal mode, 12 in demo. Cash / SPAXX carries target 0.0 and is exempt.
    """
    bench = _sleeve_benchmarks()
    missing = sorted(s for s, w in sleeve_weights.items() if w and s not in bench)
    assert not missing, (
        f"Strategic sleeve(s) {missing} carry weight but have no benchmark in "
        f"asset_classes — benchmark coherence failure: their legs would silently "
        f"drop from the blend and inflate the benchmark the portfolio is judged against."
    )
    return bench


# ── The blended benchmarks' rebalancing rule (#383) ────────────────────────────
# ONE series per blended benchmark: the basket is reset to its target weights at the
# start of every calendar quarter (the prior quarter's last day, the base a quarter
# report measures from), held through the quarter, and the quarters are chain-linked.
# Every window reads that series, so a quarter's slice is exactly the basket bought
# at the quarter's start: how every locked quarterly report measured its quarter.
# Before the rule each surface bought its own basket at the start of whatever window
# it measured, so a year never equalled its four quarters, and the Performance page
# (one basket held from inception) disagreed with the report (#383).
BLENDED_RULE = "quarterly_reset"
BLENDED_RULE_SINCE = date(2026, 9, 26)


def blended_rule_note() -> str:
    """The rule as the site states it wherever it describes a blended benchmark."""
    d = BLENDED_RULE_SINCE
    return (f"rebalanced to target weights at the start of each calendar quarter and "
            f"chain-linked (the rule since {d.strftime('%B')} {d.day}, {d.year})")


# Inside a quarter lock taken before the rule, each figure reads the basket held from
# its own window's start, as that report was built: a locked figure does not move for
# a change of method (#383). Entered by cache.snapshot_price_context.
_WINDOW_START_BASKETS: ContextVar[bool] = ContextVar("window_start_baskets", default=False)


@contextmanager
def window_start_baskets():
    token = _WINDOW_START_BASKETS.set(True)
    try:
        yield
    finally:
        _WINDOW_START_BASKETS.reset(token)


def _quarter_anchor(d: date) -> date:
    """The latest calendar quarter-end on or before ``d``."""
    for y in (d.year, d.year - 1):
        for m, day in ((12, 31), (9, 30), (6, 30), (3, 31)):
            if date(y, m, day) <= d:
                return date(y, m, day)
    raise AssertionError(d)


def _next_quarter_end(q: date) -> date:
    y, m = (q.year + 1, 3) if q.month == 12 else (q.year, q.month + 3)
    return date(y, m, 31 if m in (3, 12) else 30)


def _quarterly_reset(build, start_date: str, end_date: str | None) -> pd.Series:
    """Chain-link ``build(segment_start, segment_end)`` baskets over calendar quarters,
    and return the window [start_date, end_date] normalized to 1.0 at its start.

    The segments start at the quarter-end on or before start_date, so a window's
    reading does not depend on where the window starts: every caller reads one series.
    Gaps from every segment ride on .attrs["benchmark_gaps"]; a segment that can price
    nothing makes the whole window an explicit NaN sentinel, never a flat line."""
    end = end_date or date.today().isoformat()
    s_d, e_d = date.fromisoformat(start_date), date.fromisoformat(end)
    date_range = pd.date_range(start=start_date, end=end, freq="D")
    bounds = [_quarter_anchor(s_d)]
    while _next_quarter_end(bounds[-1]) < e_d:
        bounds.append(_next_quarter_end(bounds[-1]))
    bounds.append(e_d)

    gaps: list = []
    parts: list[pd.Series] = []
    level = 1.0
    for a, b in zip(bounds, bounds[1:]):
        seg = build(a.isoformat(), b.isoformat())
        gaps.extend(g for g in seg.attrs.get("benchmark_gaps", []) if g not in gaps)
        first = seg.first_valid_index()
        if first is None or not seg[first] > 0:
            out = pd.Series(float("nan"), index=date_range)
            out.attrs["benchmark_gaps"] = gaps
            return out
        rel = seg / seg[first] * level
        rel[rel.index < first] = level     # a non-trading quarter start holds the level
        parts.append(rel if not parts else rel.iloc[1:])
        level = float(rel.iloc[-1])
    chain = pd.concat(parts)
    window = chain[chain.index >= pd.Timestamp(s_d)]
    out = window / window.iloc[0]
    out.attrs["benchmark_gaps"] = gaps
    return out


def get_custom_blended_series(start_date: str, end_date: str | None = None) -> pd.Series:
    """
    Daily value series for the $1-normalized custom blended SAA benchmark: the ONE
    series every figure reads, rebalanced to target weights at each calendar quarter
    start and chain-linked (#383, see BLENDED_RULE). Returns the window
    [start_date, end_date] starting at 1.0, gaps on .attrs["benchmark_gaps"].

    Inside a quarter lock taken before the rule (window_start_baskets), the basket
    bought at start_date and held, as those reports were built.
    """
    if _WINDOW_START_BASKETS.get():
        return _fresh_basket_series(start_date, end_date)
    return _quarterly_reset(_fresh_basket_series, start_date, end_date)


def _fresh_basket_series(start_date: str, end_date: str | None = None) -> pd.Series:
    """
    One quarter's segment of the blended benchmark, and the whole of it before #383.

    Allocates $1 across benchmark tickers at target weights on start_date,
    then marks to market daily using adj_close prices.  Returns a Series
    indexed by pd.Timestamp starting at 1.0.

    A component unpriceable near a window bound (see _component_series) is
    dropped from the basket — the survivors' shares implicitly renormalize
    to $1 below (unchanged mechanics) — flagged on .attrs["benchmark_gaps"]
    as (sleeve, ticker, bound) 3-tuples. Total failure (no component
    priceable) yields an explicit NaN sentinel, never a fabricated flat line.
    """
    end = end_date or date.today().isoformat()
    date_range = pd.date_range(start=start_date, end=end, freq="D")

    with get_connection() as conn:
        rows = conn.execute(
            """SELECT name, target_weight
               FROM asset_classes
               WHERE parent_id IS NOT NULL""",
        ).fetchall()
    sleeve_weights = {r["name"]: r["target_weight"] for r in rows}
    _bench_map = _benchmarks_for(sleeve_weights)

    # Build price matrix for each component ticker
    price_cols: dict[str, pd.Series] = {}
    weight_map: dict[str, float] = {}   # component ticker → effective weight
    gaps: list[tuple[str, str, str]] = []

    for sleeve, components in _bench_map.items():
        # _bench_map is derived from the DB, so every key is a live sleeve: this
        # cannot silently resolve a stale key to 0.0 and drop its leg from the blend.
        sleeve_wt = sleeve_weights.get(sleeve, 0.0)
        for ticker, frac in components:
            effective_wt = sleeve_wt * frac
            if ticker in price_cols:
                # Sum weight when same ticker appears in multiple sleeves
                weight_map[ticker] = weight_map.get(ticker, 0.0) + effective_wt
            else:
                p = _get_price_series(ticker, start_date, end, col="adj_close")
                missing_bounds = p.attrs.get("benchmark_gap_bounds", [])
                price_cols[ticker] = p
                weight_map[ticker] = effective_wt
                if missing_bounds:
                    gaps.extend((sleeve, ticker, b) for b in missing_bounds)

    if not price_cols:
        return pd.Series(1.0, index=date_range)

    # Determine the number of "shares" for each ticker so that on day 0 the
    # portfolio value equals $1.00 total. A gapped component's all-NaN column
    # has no real day-0 price and is dropped here — its dollars are simply
    # never allocated, so the survivors implicitly renormalize to $1 when
    # daily_value is scaled below.
    prices_df = pd.DataFrame(price_cols, index=date_range)
    first_row = prices_df.ffill().bfill().iloc[0]

    shares: dict[str, float] = {}
    for ticker, wt in weight_map.items():
        p0 = float(first_row.get(ticker, 0))
        if p0 > 0:
            shares[ticker] = wt / p0   # units bought with `wt` dollars at price p0

    if not shares:
        # Every component gapped: an explicit missing-data sentinel, never
        # the fabricated flat $0.00 line the un-normalized fallthrough used
        # to produce (which read as a "nan bps"/"-100%" figure downstream).
        out = pd.Series(float("nan"), index=date_range)
        out.attrs["benchmark_gaps"] = gaps
        return out

    daily_value = pd.Series(0.0, index=date_range)
    for ticker, n_shares in shares.items():
        daily_value += prices_df[ticker].ffill() * n_shares

    # Normalize to start at 1.0
    first_val = daily_value.iloc[0]
    out = daily_value / first_val if first_val > 0 else daily_value
    out.attrs["benchmark_gaps"] = gaps
    return out


def get_sleeve_benchmark_returns(
    start_date: str,
    end_date: str | None = None,
) -> pd.DataFrame:
    """
    Return a DataFrame of per-sleeve benchmark cumulative returns over the period.
    Columns = sleeve names, index = dates, values = (price_t / price_0) - 1.

    Benchmark gaps: a component with no real price within _COVERAGE_WINDOW_DAYS
    of each window bound (see _component_series) is dropped from its sleeve's
    blend and recorded as a (sleeve, ticker, date) tuple on the returned frame's
    ``.attrs["benchmark_gaps"]``. A sleeve whose components ALL fail carries an
    explicit NaN sentinel column — never a flat series that reads as a
    fabricated 0.0 return; a partially-priceable blend renormalizes to its
    surviving components (still flagged, so the substitution is visible).
    Callers must treat NaN as missing data, never as a return of zero.
    """
    end = end_date or date.today().isoformat()
    date_range = pd.date_range(start=start_date, end=end, freq="D")

    result: dict[str, pd.Series] = {}
    benchmark_gaps: list[tuple[str, str, str]] = []

    for sleeve, components in _sleeve_benchmarks().items():
        # Build blended price series for multi-component sleeves
        sleeve_series = pd.Series(0.0, index=date_range)
        total_frac = 0.0

        for ticker, frac in components:
            p, missing_bounds = _component_series(ticker, start_date, end)
            if missing_bounds:
                # Explicit data gap: drop the component from the blend and flag
                # it, never fold in a flat/fabricated series.
                benchmark_gaps.extend(
                    (sleeve, ticker, bound) for bound in missing_bounds
                )
                continue
            if p.empty:   # degenerate window (start > end) — nothing to price
                continue
            p0 = float(p.iloc[0])
            if p0 > 0:
                # Normalized so each component starts at frac (proportional weight)
                sleeve_series += (p / p0) * frac
                total_frac += frac
            else:
                # A non-positive anchor price is unusable data, not a flat market
                benchmark_gaps.append((sleeve, ticker, start_date))

        if total_frac > 0:
            sleeve_series = sleeve_series / total_frac   # renormalize if any component failed
            result[sleeve] = sleeve_series - 1.0         # convert to return
        else:
            # Every component failed: carry an explicit missing-data sentinel,
            # never a flat series that reads as a fabricated 0.0 return.
            result[sleeve] = pd.Series(float("nan"), index=date_range)

    df = pd.DataFrame(result, index=date_range)
    df.attrs["benchmark_gaps"] = benchmark_gaps
    return df


def get_naive_60_40_series(start_date: str, end_date: str | None = None) -> pd.Series:
    """
    $1-normalized daily value series for the 60/40 naive benchmark (60% SPY, 40% AGG),
    under the blended benchmarks' one rule: rebalanced to 60/40 at the start of each
    calendar quarter and chain-linked (#383). Returns a Series indexed by
    pd.Timestamp starting at 1.0 on start_date.

    It weighted daily returns before #383, which is a DAILY rebalance, not the absence
    of a rebalancing assumption its docstring claimed.

    A leg unpriceable near a segment bound (see _component_series) is dropped
    and the surviving leg's weight renormalizes to 1.0, flagged on
    .attrs["benchmark_gaps"] as (label, ticker, bound) 3-tuples; both legs
    missing yields an explicit NaN sentinel, never a fabricated flat 0% return.
    """
    return _quarterly_reset(_naive_basket_series, start_date, end_date)


def _naive_basket_series(start_date: str, end_date: str) -> pd.Series:
    """One quarter's 60/40 segment: $0.60 of SPY and $0.40 of AGG bought at start_date
    and held, the same mechanics as the blended SAA basket (_fresh_basket_series)."""
    end = end_date
    date_range = pd.date_range(start=start_date, end=end, freq="D")

    spy = _get_price_series("SPY", start_date, end, col="adj_close")
    agg = _get_price_series("AGG", start_date, end, col="adj_close")
    spy_gaps = spy.attrs.get("benchmark_gap_bounds", [])
    agg_gaps = agg.attrs.get("benchmark_gap_bounds", [])

    gaps: list[tuple[str, str, str]] = []
    gaps.extend(("60/40 Naive", "SPY", b) for b in spy_gaps)
    gaps.extend(("60/40 Naive", "AGG", b) for b in agg_gaps)

    legs: list[tuple[pd.Series, float]] = []
    if not spy_gaps:
        legs.append((spy, 0.6))
    if not agg_gaps:
        legs.append((agg, 0.4))

    if not legs:
        # Both legs gapped: an explicit missing-data sentinel, never the
        # fabricated flat 0% return pct_change().fillna(0.0) used to produce
        # on a stale/absent price series.
        out = pd.Series(float("nan"), index=date_range)
        out.attrs["benchmark_gaps"] = gaps
        return out

    total_wt = sum(wt for _, wt in legs)
    value = pd.Series(0.0, index=date_range)
    for leg_series, wt in legs:
        p = leg_series.reindex(date_range)
        p0 = float(p.ffill().bfill().iloc[0])
        value = value + p.ffill() * ((wt / total_wt) / p0)

    first = value.first_valid_index()
    out = value / value[first] if first is not None and value[first] > 0 else value
    out.attrs["benchmark_gaps"] = gaps
    return out


def get_naive_series(kind: str, start_date: str, end_date: str | None = None) -> pd.Series:
    """
    $1-normalized naive benchmark series.

    kind='60_40': 60% SPY + 40% AGG (calls get_naive_60_40_series).
    kind='spy':   pure SPY total return (calls get_sp500_series).
    """
    if kind == "spy":
        return get_sp500_series(start_date, end_date)
    return get_naive_60_40_series(start_date, end_date)
