"""
Factor Valuation — the value spread (value vs growth), Phase 30.

Distinct from factor *performance* (trailing return, see ``factor_regime``):
this measures how *cheap* value is relative to growth on book-to-market — a
mean-reversion-relevant signal. A factor can perform well while still being
cheap, or perform poorly while expensive, so valuation is independent
information.

Definition
----------
From the Ken French NYSE BE/ME breakpoints (annual, formed each June), the
value spread is the log ratio of the value-portfolio boundary to the
growth-portfolio boundary::

    value_spread = log( B/M at the 70th percentile  /  B/M at the 30th percentile )

The 30th and 70th NYSE book-to-market percentiles are the exact breakpoints HML
uses to form its growth and value portfolios, so this spread is the valuation
gap between the same boundaries that define the HML factor shown in the
performance charts. A *wide* spread means value is unusually cheap relative to
growth; a *narrow* spread means the value-growth valuation gap is compressed.

Size valuation is deliberately omitted: no clean academic series isolates size
valuation, and deriving one from size-and-value-sorted portfolios conflates the
two factors (small-caps structurally trade at different multiples for reasons
unrelated to mean reversion).
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from src.factors import load_beme_breakpoints

# HML's NYSE book-to-market breakpoints: growth portfolio below the 30th
# percentile, value portfolio above the 70th.
_GROWTH_PCT = 30
_VALUE_PCT  = 70

# Percentile bands for the interpretation prose (fractions 0.0–1.0), matching the
# 10/30/70/90 structure of src.macro.interpret_excess_cape.
_PCT_EXTREME_LOW  = 0.10
_PCT_LOW          = 0.30
_PCT_HIGH         = 0.70
_PCT_EXTREME_HIGH = 0.90


def _ordinal(n: float) -> str:
    """Format a percentile as an ordinal, e.g. 92 -> '92nd'."""
    n = int(round(n))
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th', 'st', 'nd', 'rd', 'th'][min(n % 10, 4)]}"


def value_spread(high_bm: float, low_bm: float) -> float:
    """Log value spread from a high-B/M (value) and low-B/M (growth) ratio."""
    return float(np.log(high_bm / low_bm))


def build_value_spread_series(
    bp_df: Optional[pd.DataFrame] = None,
    value_pct: int = _VALUE_PCT,
    growth_pct: int = _GROWTH_PCT,
) -> pd.Series:
    """
    Annual value-spread series: ``log(p{value_pct} / p{growth_pct})`` of the
    Ken French NYSE BE/ME breakpoints, indexed by June-30 formation date.

    Fetching is injectable via ``bp_df`` (a breakpoints DataFrame with p-columns)
    for tests; otherwise it loads from ``factors.load_beme_breakpoints``.
    """
    if bp_df is None:
        bp_df = load_beme_breakpoints()
    hi = bp_df[f"p{value_pct}"].astype(float)
    lo = bp_df[f"p{growth_pct}"].astype(float)
    s = np.log(hi / lo)
    s.name = "value_spread"
    return s.dropna().sort_index()


def valuation_percentile(series: pd.Series, current_value: float) -> float:
    """
    Historical percentile rank (0–100) of ``current_value`` within ``series``.

    Delegates to ``src.macro.percentile`` so the valuation percentile uses the
    exact same method as CAPE and the Phase 29 performance percentile.
    """
    from src.macro import percentile as _macro_percentile
    return _macro_percentile(series, current_value)


def interpret_value_spread(current_value: float, percentile_fraction: float) -> str:
    """
    Banded interpretation of the value spread, conditioned on its historical
    percentile (a fraction 0.0–1.0, matching ``macro.interpret_excess_cape``).

    A high percentile = wide spread = value historically cheap relative to growth
    (mean-reversion tailwind for the SAA's value tilt). A low percentile = narrow
    spread = value richly priced relative to growth. The prose explicitly pairs
    with the performance read above, since valuation is independent of trailing
    return: a factor can perform well and still be cheap, or perform well while
    expensive.
    """
    pct_label = _ordinal(percentile_fraction * 100)

    if percentile_fraction >= _PCT_EXTREME_HIGH:
        return (
            f"The value spread is in the {pct_label} percentile of its history — value "
            "is historically cheap relative to growth, near the widest end of the "
            "record. Wide spreads of this kind have tended to precede value "
            "outperformance and are independent of recent trailing returns: even where "
            "value has performed well lately, the cheapness tailwind behind the SAA's "
            "value tilt remains intact."
        )
    if percentile_fraction >= _PCT_HIGH:
        return (
            f"The value spread is in the {pct_label} percentile of its history — value "
            "is cheap relative to growth by historical standards. This is a valuation "
            "tailwind for the SAA's value tilt, distinct from the trailing-return read "
            "above: value can be winning on performance and still carry an unusually "
            "wide cheapness gap."
        )
    if percentile_fraction > _PCT_LOW:
        return (
            f"The value spread is near its historical median (the {pct_label} "
            "percentile) — value is neither notably cheap nor rich relative to growth. "
            "The valuation signal is neutral; the SAA's value tilt rests on its "
            "strategic rationale rather than a valuation tailwind, independent of "
            "whatever the trailing-performance read shows."
        )
    if percentile_fraction > _PCT_EXTREME_LOW:
        return (
            f"The value spread is in the {pct_label} percentile of its history — value "
            "is somewhat richly priced relative to growth, with a below-average "
            "cheapness gap. The valuation tailwind behind the SAA's value tilt is "
            "muted, even if trailing performance has been favorable."
        )
    return (
        f"The value spread is in the {pct_label} percentile of its history — value is "
        "richly priced relative to growth by historical standards; the cheapness "
        "tailwind behind the SAA's value tilt is largely absent. A factor can keep "
        "performing while expensive, but a narrow spread offers little mean-reversion "
        "support, independent of the trailing-return read above."
    )
