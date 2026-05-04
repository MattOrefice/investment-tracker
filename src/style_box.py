"""
Continuous-coordinate equity style box: size and value-growth scores for US equity ETFs.

Methodology
-----------
Size (y-axis)
    log10(weighted-avg market cap in $B), additive-shifted so SPY anchors at y=2.0
    (centre of the Large band on the 3x3 grid).  Derived thresholds at SPY_MC=$250B:
      Large/Mid boundary: y=1.5 ≈ $79B
      Mid/Small boundary: y=0.5 ≈ $7.9B

Value-growth (x-axis)
    Four value metrics (all higher = more value-tilted):
      B/P = 1/p_b,  E/P = 1/p_e,  dividend yield,  CF/P = 1/p_cf
    Each is normalised as a fractional deviation from SPY:
      z_i = (ETF_ratio_i / SPY_ratio_i) - 1
    Composite: avg_z = mean(z_i for four metrics)
    Mapped to x: x = 1.0 - avg_z * VALUE_SCALE
      SPY/VOO → z=0 → x=1.0 (Blend centre)
      Value tilt → z>0 → x<1.0 (leftward)
      Growth tilt → z<0 → x>1.0 (rightward)
    VALUE_SCALE=0.85 calibrated so VTV (avg_z≈0.71) lands at x≈0.40,
    firmly in the Value column (x<0.5 = Value/Blend boundary).

Simplification disclosure
    This is a 4-factor trailing approximation of Morningstar's proprietary
    10-factor score (which adds forward P/E, P/S, earnings-growth metrics,
    and undisclosed weights).  Treat positions as directional, not precise.

Universe
    US equity ETFs only: VOO, VTV, SPHQ, AVUV.
    Non-US (VEA, IEMG) are listed in a separate callout — not placed on this grid.

Data
    Static cache in data/etf_metadata.json.  Refresh each quarter from ETF fact sheets.
    The 'as_of' field stamps the source date.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

_META_PATH = Path(__file__).resolve().parent.parent / "data" / "etf_metadata.json"

# ── Calibration constants ──────────────────────────────────────────────────────
_SIZE_ANCHOR_Y   = 2.0   # SPY maps to y=2.0 (Large band centre)
_VALUE_SCALE     = 0.85  # z=0.71 (VTV) → x≈0.40 (Value column)
COLLISION_DIST   = 0.15  # cell-units; dots closer than this would need leader lines

# ── ETF universe ───────────────────────────────────────────────────────────────
US_EQUITY_TICKERS = frozenset({"VOO", "VTV", "SPHQ", "AVUV"})
NON_US_EQUITY_MAP = {"VEA": "Developed", "IEMG": "Emerging"}


def _load_metadata() -> dict:
    with open(_META_PATH) as f:
        return json.load(f)


def compute_size_score(ticker: str, meta: dict | None = None) -> float:
    """
    y-coordinate on the 3x3 grid.
    y = log10(MC_B) - log10(SPY_MC_B) + SIZE_ANCHOR_Y
    """
    if meta is None:
        meta = _load_metadata()
    mc_b     = float(meta[ticker]["weighted_avg_mcap_b"])
    spy_mc_b = float(meta["SPY"]["weighted_avg_mcap_b"])
    return math.log10(mc_b) - math.log10(spy_mc_b) + _SIZE_ANCHOR_Y


def compute_value_growth_score(ticker: str, meta: dict | None = None) -> float:
    """
    x-coordinate on the 3x3 grid.
    x=1.0 → SPY-neutral (Blend centre).  x<1.0 → value.  x>1.0 → growth.
    """
    if meta is None:
        meta = _load_metadata()
    etf = meta[ticker]
    spy = meta["SPY"]

    bp_z  = (1.0 / etf["p_b"])  / (1.0 / spy["p_b"])  - 1.0
    ep_z  = (1.0 / etf["p_e"])  / (1.0 / spy["p_e"])  - 1.0
    dy_z  = etf["div_yield"]    /       spy["div_yield"]  - 1.0
    cfp_z = (1.0 / etf["p_cf"]) / (1.0 / spy["p_cf"]) - 1.0

    avg_z = (bp_z + ep_z + dy_z + cfp_z) / 4.0
    return 1.0 - avg_z * _VALUE_SCALE


def assign_textposition(x: float, y: float) -> str:
    """
    Return Plotly textposition pointing the label outward from the dot's cell centre.

    Cell centre is the nearest integer grid point (0/1/2 on each axis).
    For dots near the horizontal cell centre (|x - cx| < 0.05), use "center"
    alignment to avoid ambiguity.
    """
    cx = max(0, min(2, round(x)))
    cy = max(0, min(2, round(y)))
    vert  = "top" if y >= cy else "bottom"
    if abs(x - cx) < 0.05:
        horiz = "center"
    elif x < cx:
        horiz = "left"
    else:
        horiz = "right"
    return f"{vert} {horiz}"


def xy_to_category(x: float, y: float) -> tuple[str, str]:
    """Derive approximate Morningstar size/style labels from continuous coordinates."""
    size  = "Large" if y >= 1.5 else ("Mid" if y >= 0.5 else "Small")
    style = "Value" if x <= 0.5 else ("Growth" if x >= 1.5 else "Blend")
    return size, style
