"""
Positioning helpers: effective duration. (The equity style box that lived here was
withdrawn in #468: its fact-sheet figures had no recorded source.)

All functions are pure computation over portfolio state.
No hand-written quarterly text — every number re-derives from live data.
"""
from __future__ import annotations

from src.etf_metadata import load_metadata
from src.holdings import get_sleeve_weights_on_date

# ── Static reference dicts ─────────────────────────────────────────────────

# The duration the FI sleeve's is compared with. Since #462 it lives in
# data/etf_metadata.json with its measure, source and as-of date, locked with the
# fact-sheet data like the funds' (#455). Bloomberg's own index page could not be read
# (HTTP 403), so it is AGG's, the iShares ETF that tracks the Bloomberg US Agg, and
# named as AGG's wherever it is shown. Read through benchmark_duration().
AGG_TICKER = "AGG"
AGG_NAME = "AGG, the iShares ETF tracking the Bloomberg US Agg"
#
# The figure before #462, labelled the Agg's. UNDATED like _UNDATED_DURATIONS below:
# nothing recorded when or where it was read. Kept only for a quarter locked before
# the move, which reports the benchmark it was reported with. Never a live figure.
_UNDATED_AGG_DURATION: float = 6.0
UNDATED_AGG_NAME = "the Bloomberg US Agg"

# A fund's duration lives in data/etf_metadata.json since #455, each with its
# measure, source and as-of date, so a quarter lock snapshots it with the rest of the
# fact-sheet data. Read through fund_durations() (the positioning section, locked) or
# live_fund_durations() (live figures).
#
# The table they came from before #455. UNDATED: nothing recorded when or where its
# values were read, and by 2026-09 it was out of date (VGIT 5.5 against Vanguard's
# 4.9, IEF 7.5 against iShares' 6.86). Kept only so a quarter locked before the
# move, whose locked metadata holds no durations, reports the durations it was
# reported with. Never read for a live figure.
_UNDATED_DURATIONS: dict[str, float] = {
    "VGIT": 5.5, "SCHP": 6.8, "IEF": 7.5, "TIP": 7.0, "BIL": 0.1,
}


def _durations_in(meta: dict) -> "dict[str, float]":
    return {t: float(v["duration_years"]) for t, v in meta.items()
            if isinstance(v, dict) and v.get("duration_years") is not None}


def live_fund_durations() -> "dict[str, float]":
    """{ticker: duration in years} from data/etf_metadata.json as it stands, for a
    live figure (the Risk page's rate shock, a thesis's {{dur:}}). Never a lock's."""
    import json
    from src.etf_metadata import META_PATH
    with open(META_PATH) as f:
        return _durations_in(json.load(f))


def fund_durations() -> "dict[str, float]":
    """{ticker: duration in years} for the positioning section: the ETF metadata's,
    locked with the quarter's fact-sheet data like the rest of it (#455). Inside a
    lock taken before the move, whose locked metadata holds no durations or which
    holds no inputs at all, the undated table that quarter was reported with. Raises
    InputPending inside a lock still waiting on its metadata."""
    from src import prices
    from src.input_lock import ETF_METADATA, locked
    held = locked(ETF_METADATA)
    if held is not None:
        return _durations_in(held) or dict(_UNDATED_DURATIONS)
    if prices._PRICE_LOCK.get() is not None:        # a lock from before locked inputs
        return dict(_UNDATED_DURATIONS)
    return live_fund_durations()


def benchmark_duration() -> "dict":
    """The duration the FI sleeve's is compared with, as {years, name, short}: AGG's
    from the ETF metadata, locked with it like the funds' (#462); inside a lock taken
    before the move, whose locked metadata holds no AGG duration or which holds no
    inputs at all, the undated 6.0 it was reported with, under the Agg's name. Raises
    InputPending inside a lock still waiting on its metadata."""
    from src import prices
    from src.input_lock import ETF_METADATA, locked
    held = locked(ETF_METADATA)
    if held is not None:
        meta = held
    elif prices._PRICE_LOCK.get() is not None:        # a lock from before locked inputs
        meta = {}
    else:
        meta = load_metadata()
    years = _durations_in(meta).get(AGG_TICKER)
    if years is None:
        return {"years": _UNDATED_AGG_DURATION, "name": UNDATED_AGG_NAME,
                "short": "Bloomberg US Agg"}
    return {"years": years, "name": AGG_NAME, "short": AGG_TICKER}


def live_duration_sources() -> str:
    """Where the live duration metric's figures come from: one clause per fund it
    weights, the ETF metadata's duration with its measure and as-of date (#455)."""
    import json
    from src.asof import format_long_date
    from src.etf_metadata import META_PATH
    with open(META_PATH) as f:
        meta = json.load(f)
    funds = [t for s, t in _FI_SLEEVE_HOLDING.items() if s != "Cash / SPAXX"]
    if AGG_TICKER in meta:                         # the benchmark's, named as AGG's (#462)
        funds.append(AGG_TICKER)
    return "; ".join(
        f"{t} {meta[t]['duration_years']:g} yrs, {meta[t]['duration_measure']} as of "
        f"{format_long_date(meta[t]['as_of'])}" for t in funds)


# Sleeve → actual holding ticker (for duration lookup)
_FI_SLEEVE_HOLDING: dict[str, str] = {
    "Core Fixed Income": "VGIT",
    "TIPS":              "SCHP",
    "Cash / SPAXX":      "SPAXX",
}

# ── Public API ─────────────────────────────────────────────────────────────

def get_effective_duration(end_date: str) -> dict:
    """
    Return effective duration metrics for the FI sleeves.

    Returns:
        duration               — portfolio-level duration contribution (Core FI + TIPS only)
        fi_sleeve_duration     — duration of Core FI + TIPS only (cash excluded)
        fi_weight_pct          — Core FI + TIPS weight as % of invested (ex-cash) portfolio
        cash_weight_pct        — operational SPAXX float as % of TOTAL portfolio
        fi_weight_incl_cash_pct — Core FI + TIPS + operational cash (for informational use)
        agg_benchmark          — the benchmark duration for comparison (benchmark_duration)
        agg_name / agg_short   — whose it is: AGG's since #462, the Agg's in older locks

    Phase 38a — sleeve weights are ex-cash (sum to 1.0 over the 9 strategic
    sleeves); operational cash is read from the sleeve frame's .attrs, not as a
    sleeve row.
    """
    bench = benchmark_duration()
    _empty = {
        "duration": 0.0, "fi_sleeve_duration": 0.0,
        "fi_weight_pct": 0.0, "cash_weight_pct": 0.0,
        "fi_weight_incl_cash_pct": 0.0, "agg_benchmark": bench["years"],
        "agg_name": bench["name"], "agg_short": bench["short"],
    }
    sw = get_sleeve_weights_on_date(end_date)
    if sw.empty:
        return _empty

    total_portfolio_wt = float(sw["Actual Weight"].sum())
    if total_portfolio_wt == 0:
        return _empty

    weighted_dur    = 0.0
    fi_wt_excl_cash = 0.0
    fi_wt_incl_cash = 0.0
    cash_wt         = 0.0
    durations       = fund_durations()

    for sleeve, ticker in _FI_SLEEVE_HOLDING.items():
        if sleeve not in sw.index:
            continue
        actual_wt = float(sw.loc[sleeve, "Actual Weight"])
        fi_wt_incl_cash += actual_wt
        if sleeve == "Cash / SPAXX":
            cash_wt += actual_wt          # operational cash: no duration is read for it
            continue
        # A held FI-sleeve holding with no duration would silently contribute 0 to
        # the weighted sleeve duration — understating "FI Sleeve Duration" with no
        # signal: raise rather than default to 0.
        if ticker not in durations:
            raise ValueError(
                f"get_effective_duration: FI-sleeve holding {ticker!r} (sleeve "
                f"{sleeve!r}) has no duration in data/etf_metadata.json. A held FI "
                f"fund with no duration silently understates the FI sleeve duration "
                f"metric — add {ticker}'s duration, measure, source and as_of from "
                f"its issuer. Known: {sorted(durations)}."
            )
        weighted_dur    += actual_wt * durations[ticker]
        fi_wt_excl_cash += actual_wt

    eff_duration  = weighted_dur / total_portfolio_wt
    fi_sleeve_dur = weighted_dur / fi_wt_excl_cash if fi_wt_excl_cash > 0 else 0.0

    # Operational cash as a share of TOTAL portfolio, from the ex-cash frame's attrs.
    cash_of_total_pct = round(float(sw.attrs.get("cash_weight_of_total", 0.0)) * 100, 1)
    fi_excl_cash_pct  = round(fi_wt_excl_cash / total_portfolio_wt * 100, 1)

    return {
        "duration":                round(eff_duration, 1),
        "fi_sleeve_duration":      round(fi_sleeve_dur, 1),
        "fi_weight_pct":           fi_excl_cash_pct,
        "cash_weight_pct":         cash_of_total_pct,
        "fi_weight_incl_cash_pct": round(fi_excl_cash_pct + cash_of_total_pct, 1),
        "agg_benchmark":           bench["years"],
        "agg_name":                bench["name"],
        "agg_short":               bench["short"],
    }


