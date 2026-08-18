"""Capital deployment engine — contribution allocation and band-breach rebalancing.

Pure functions only — no DB access. DB-backed helpers live in the page layer.
"""
from __future__ import annotations

from typing import NamedTuple

import pandas as pd

from src.coverage import PriceCoverage

_CASH_SLEEVE = "Cash / SPAXX"
_CASH_TICKER = "SPAXX"

SUM_INVARIANT_TOLERANCE = 0.10


class UnpricedAllocationError(ValueError):
    """A sleeve was allocated cash while holding something with no resolved price.

    RAISED, never returned as an empty frame. pages/11_Capital_Deployment.py maps an
    empty result to "All sleeves within tolerance bands. No rebalancing required."
    (:618-619), so a refusal expressed as emptiness would render as REASSURANCE —
    which is the #261 defect reintroduced by the fix for it. A refusal has to be
    impossible to mistake for nothing-to-do.
    """


class CoverageMismatchError(ValueError):
    """``prices`` and ``coverage`` disagree about the same ticker.

    The page assembles them from two separate fetch passes — `sleeve_weights_with_
    coverage` produces the record, and pages/11:89-99 builds the price dict in its
    own loop — so they can disagree if taken at different as-of dates or across a
    cache change. A ticker the record calls resolved but the dict cannot price is
    not a data gap to disclose; it means the two inputs do not describe the same
    moment, and every figure derived from the pair is then unfounded.
    """


class BuySuggestions(NamedTuple):
    """suggest_buys' rows, plus the report a caller needs to EXPLAIN them.

    A NamedTuple rather than ``DataFrame.attrs``, matching holdings.SleeveWeights
    and holdings.ValueSeries. src/coverage.py:10-22 sets out why: pandas documents
    ``attrs`` as experimental, requirements.txt admits an open pandas range, and
    ``attrs`` is dropped by merge/concat. PR #261 put this report on ``attrs``
    anyway; this moves it onto the channel the rest of the codebase already uses.

      ``total_shortfall``           dollars needed to close every breach
      ``shortfalls_fully_filled``   True in the fill-completely branch, where any
                                    leftover is a genuine surplus; False in the
                                    proportional branch, where the cash was exhausted
                                    and shortfalls REMAIN, so leftover is per-row
                                    rounding residue; None when nothing was assessed.
    """

    frame: pd.DataFrame
    total_shortfall: float
    shortfalls_fully_filled: "bool | None"


def _placeable_by_sleeve(
    ticker_to_sleeve: dict[str, str],
    prices: dict[str, float],
    coverage: PriceCoverage,
    allocated_sleeves: "set[str]",
    *,
    caller: str,
) -> dict[str, list[str]]:
    """sleeve -> [tickers that can actually be bought], separating three states.

    Replaces the single test ``prices.get(ticker, 0.0) > 0``, which decided two
    different questions at once — *is this security held* and *did its price
    resolve* — and answered both with "absent from the dict". That conflation is
    why neither function's own invariant could see the defect in #192.

    ``coverage.requested`` is exactly the HELD set (sleeve_weights_with_coverage
    asks per held ticker), so the record already separates them:

      not in ``requested``      -> not held. Exclude silently; correct, and the
                                   majority case, since ticker_to_sleeve spans every
                                   security while the book holds ten.
      in ``unresolved``         -> HELD but unpriced. Refuse if its sleeve is being
                                   allocated; ignore otherwise, because an unpriced
                                   holding in a sleeve receiving nothing cannot
                                   affect the trade.
      resolved, but unpriceable
      from ``prices``           -> the two inputs disagree. Raise.

    The refusal is scoped to ``allocated_sleeves`` deliberately. A global refusal
    would block a trade over a holding that has no bearing on it.
    """
    unresolved_reason = {u.ticker: u.reason for u in coverage.unresolved}
    requested = set(coverage.requested)

    out: dict[str, list[str]] = {}
    blocked: dict[str, list[str]] = {}

    for ticker, sleeve in ticker_to_sleeve.items():
        # Cash is the funding source, never a buy target. Both functions already
        # exclude it — suggest_buys via the drift filter, suggest_contributions via
        # `investable` — so testing both spellings here changes no output.
        if ticker == _CASH_TICKER or sleeve == _CASH_SLEEVE:
            continue

        if ticker in unresolved_reason:
            if sleeve in allocated_sleeves:
                blocked.setdefault(sleeve, []).append(
                    f"{ticker} ({unresolved_reason[ticker]})"
                )
            continue

        if ticker not in requested:
            continue

        price = prices.get(ticker, 0.0)
        if price <= 0:
            raise CoverageMismatchError(
                f"{caller}: coverage reports {ticker!r} resolved as of "
                f"{coverage.as_of_requested}, but `prices` cannot price it "
                f"({price!r}). The two arguments are built by separate fetch passes "
                "and must describe the same moment; when they do not, every sleeve "
                "weight and dollar figure derived from the pair is unfounded. This "
                "is not a data gap to disclose — a gap would appear in "
                "coverage.unresolved, and it does not."
            )
        out.setdefault(sleeve, []).append(ticker)

    if blocked:
        detail = "; ".join(
            f"{s}: {', '.join(sorted(ts))}" for s, ts in sorted(blocked.items())
        )
        raise UnpricedAllocationError(
            f"{caller}: {len(blocked)} sleeve(s) are being allocated cash while "
            f"holding a security with no resolved price — {detail}. Sizing a buy "
            "against a sleeve whose market value is understated produces a wrong "
            "trade instruction, not a wrong display: the unpriced holding counted "
            "as $0.00, so the sleeve looks more underweight than it is and draws a "
            "larger share, and the surviving tickers in it absorb the whole "
            "allocation (measured at 3.76x for VNQ when PDBC went unpriced, #192).\n\n"
            "Refuse and name the holding; do not fall back to sizing over the "
            "survivors. That looks reasonable and is the least safe option — the "
            "sleeve's shortfall is already inflated upstream, so an 'honest' split "
            "over what remains is a confident number on a corrupted base.\n\n"
            "Callers must not turn this into an empty result. pages/11 renders an "
            "empty frame as 'All sleeves within tolerance bands. No rebalancing "
            "required.', so a refusal expressed as emptiness reads as reassurance."
        )

    return out


def compute_drift(
    sleeve_weights: dict[str, float],
    saa_targets: dict[str, float],
    saa_bands: dict[str, float],
) -> pd.DataFrame:
    """
    Compute per-sleeve drift vs SAA targets.

    Args:
        sleeve_weights: actual weight per sleeve (fractions, should sum ~1)
        saa_targets:    target weight per sleeve (fractions)
        saa_bands:      symmetric ±tolerance per sleeve (fractions)

    Returns DataFrame indexed by sleeve name with columns:
        Actual Weight, Target Weight, Band, Drift, In Band.
    Drift = actual − target (negative = underweight).
    """
    rows = []
    for sleeve, actual in sleeve_weights.items():
        # target: a held sleeve with no SAA target reads as fully overweight vs 0%.
        # That is a defensible, DISPLAY-ONLY reading of an untargeted holding (no
        # consumer sizes a trade from it — suggest_buys is underweight-only,
        # suggest_contributions/closest_to_breach are target-driven), so it keeps
        # the 0.0 fallback rather than raising and blanking the whole drift table.
        target = saa_targets.get(sleeve, 0.0)
        # band: a missing band is NOT display-only — it decides the In-Band verdict.
        # A silent fallback here fabricated that verdict (and split 0.02 here vs 0.03
        # in the Performance chart for the SAME sleeve). Every held sleeve maps to a
        # strategic asset_classes row whose band is NOT NULL, so a sleeve reaching
        # here without one is an unmapped/mis-mapped holding — raise, don't guess.
        if sleeve not in saa_bands:
            raise ValueError(
                f"compute_drift: sleeve {sleeve!r} has no tolerance band. Every held "
                f"sleeve must resolve to a strategic asset_classes row with a band; a "
                f"sleeve here without one is an unmapped/mis-mapped holding, and a "
                f"fallback band would fabricate its in/out-of-band verdict. "
                f"Bands present for: {sorted(saa_bands)}."
            )
        band = saa_bands[sleeve]
        drift = round(actual - target, 6)
        rows.append(
            {
                "Sleeve":        sleeve,
                "Actual Weight": actual,
                "Target Weight": target,
                "Band":          band,
                "Drift":         drift,
                "In Band":       abs(drift) <= band,
            }
        )
    return pd.DataFrame(rows).set_index("Sleeve")


def suggest_buys(
    drift_df: pd.DataFrame,
    portfolio_value: float,
    cash_to_deploy: float,
    ticker_to_sleeve: dict[str, str],
    prices: dict[str, float],
    *,
    coverage: PriceCoverage,
) -> "BuySuggestions":
    """
    Suggest buy orders to bring underweight sleeves toward target.

    Only considers sleeves with Drift < 0 (underweight). The Cash / SPAXX
    sleeve is never a buy target — it is the funding source. SPAXX ticker
    is excluded from suggestions regardless of sleeve mapping.

    Allocation:
        shortfall_i = |Drift_i| × portfolio_value
        if sum(shortfall) <= cash_to_deploy:  fill each sleeve completely
        else:                                  allocate proportionally to shortfall

    Multi-holding sleeves (e.g. Real Assets → VNQ + PDBC) receive an equal
    split across their tickers.

    Args:
        drift_df:        result of compute_drift()
        portfolio_value: current total portfolio value in dollars
        cash_to_deploy:  dollars to invest
        ticker_to_sleeve: ticker → sleeve name
        prices:          ticker → current price

    ``coverage`` is REQUIRED and keyword-only, and must stay that way. Giving it a
    default of ``None`` recreates the defect exactly: a caller that omits it gets
    the old conflated behaviour with nothing to indicate the guard is inert, which
    is the silent path this parameter exists to close. If a call site is awkward to
    supply, build the record — do not weaken the signature. Tests use a helper that
    constructs an all-resolved record; that is the right place to pay the cost.

    Returns a ``BuySuggestions`` NamedTuple: the rows, plus the branch report a
    caller needs to explain undeployed cash. See that class for why it is not
    ``DataFrame.attrs``.

    Raises ``UnpricedAllocationError`` when an allocated sleeve holds a security
    with no resolved price — never an empty frame, which pages/11 renders as
    "All sleeves within tolerance bands".
    """
    _EMPTY = pd.DataFrame(
        columns=["Ticker", "Sleeve", "Price", "Suggested $", "Suggested Shares"]
    )

    def _stamp(df: pd.DataFrame, shortfall: float, filled: "bool | None") -> "BuySuggestions":
        """Report on EVERY return path.

        An unreported frame reads as "not assessed" to a caller, which is the same
        silence this record exists to remove — so the guard returns declare their
        state explicitly rather than defaulting into it.
        """
        return BuySuggestions(df, float(shortfall), filled)

    # Nothing assessed: no cash to place, or no book to place it against. None, not
    # True — "no breach remains unfilled" is a claim this branch has not tested, and
    # a caller must be able to say nothing rather than say the wrong thing.
    if cash_to_deploy <= 0 or portfolio_value <= 0:
        return _stamp(_EMPTY, 0.0, None)

    underweight = drift_df[
        (~drift_df["In Band"]) & (drift_df["Drift"] < 0) & (~drift_df.index.isin([_CASH_SLEEVE]))
    ].copy()
    # No breaches at all, so nothing is unfilled — True is the honest report here.
    if underweight.empty:
        return _stamp(_EMPTY, 0.0, True)

    underweight["Shortfall $"] = underweight["Drift"].abs() * portfolio_value

    total_shortfall = underweight["Shortfall $"].sum()
    if total_shortfall <= cash_to_deploy:
        allocations: dict[str, float] = underweight["Shortfall $"].to_dict()
    else:
        allocations = {
            sleeve: (shortfall / total_shortfall) * cash_to_deploy
            for sleeve, shortfall in underweight["Shortfall $"].items()
        }

    sleeve_to_tickers = _placeable_by_sleeve(
        ticker_to_sleeve, prices, coverage, set(allocations), caller="suggest_buys",
    )

    rows = []
    dropped: dict[str, float] = {}
    for sleeve in sorted(allocations, key=lambda s: -allocations[s]):
        dollars = allocations[sleeve]
        tickers_in_sleeve = sorted(sleeve_to_tickers.get(sleeve, []))
        if not tickers_in_sleeve:
            dropped[sleeve] = dollars
            continue
        per_ticker = dollars / len(tickers_in_sleeve)
        for ticker in tickers_in_sleeve:
            price = prices[ticker]   # guaranteed > 0 by filter above
            rows.append(
                {
                    "Ticker":           ticker,
                    "Sleeve":           sleeve,
                    "Price":            round(price, 2),
                    "Suggested $":      round(per_ticker, 2),
                    "Suggested Shares": round(per_ticker / price, 6),
                }
            )

    # COMPLETENESS BACKSTOP: no allocated sleeve may be dropped silently.
    #
    # Since the coverage record arrived (#192 item 1) this is no longer the primary
    # guard — _placeable_by_sleeve refuses a held-but-unpriced holding before the
    # loop runs, so that cause can no longer reach here. What remains is the cause
    # coverage CANNOT see: a held, resolved ticker that is absent from
    # ticker_to_sleeve altogether (an unmapped holding, cf. #224). Coverage reports
    # it resolved; only this structural check notices the sleeve produced no row.
    #
    # It still counts SLEEVES, not dollars. suggest_contributions asserts its
    # Suggested-$ total equals the cash offered; transplanting that here is UNSOUND
    # rather than merely weak — mutation-tested, it flags this case AND the
    # fill-completely branch, where deploying less than the cash offered is correct.
    # A guard that cannot be left armed is not a guard.
    assert not dropped, (
        f"suggest_buys: {len(dropped)} allocated sleeve(s) produced no buy rows, so "
        f"${sum(dropped.values()):,.2f} of the ${sum(allocations.values()):,.2f} "
        f"allocated is missing from the result — "
        + "; ".join(f"{s} (${d:,.2f})" for s, d in sorted(dropped.items()))
        + ". A holding with no resolved price cannot cause this: "
        "_placeable_by_sleeve raises UnpricedAllocationError on that before this "
        "loop. The remaining cause is a held, resolved ticker missing from "
        "ticker_to_sleeve, so its sleeve is allocated cash with nothing mapped to "
        "buy — an unmapped holding (see #224's startup warning), not a price gap. "
        "Check the securities-to-asset_classes join for these sleeves.\n\n"
        "Do not replace this with a Suggested-$ total check like the one in "
        "suggest_contributions. Such a check would catch this case — but it also "
        "fires when total_shortfall <= cash_to_deploy, where deploying only the "
        "shortfall and leaving the rest undeployed is the CORRECT answer. It is "
        "unsound here, not insufficient, and a guard that cannot be left armed is "
        "not a guard. That is why this counts sleeves rather than dollars.\n\n"
        "The result would otherwise render as a complete rebalance that quietly "
        "under-deploys — a wrong trade instruction, not a wrong display. See #192."
    )

    return _stamp(
        pd.DataFrame(rows) if rows else _EMPTY,
        total_shortfall,
        bool(total_shortfall <= cash_to_deploy),
    )


def suggest_contributions(
    portfolio_value: float,
    cash_to_deploy: float,
    sleeve_weights: dict[str, float],
    saa_targets: dict[str, float],
    ticker_to_sleeve: dict[str, str],
    prices: dict[str, float],
    *,
    coverage: PriceCoverage,
) -> pd.DataFrame:
    """
    Suggest how to allocate new cash contributions across sleeves.

    Hybrid algorithm — close drift first, then maintain SAA policy on residual.

    Step 1: For each investable (non-cash) sleeve, compute current dollar
            shortfall vs SAA target:
                shortfall_i = portfolio_value × max(0, target_i − actual_i)

    Step 2: If sum(shortfall) >= cash: allocate proportionally to shortfall.
            Only underweight sleeves (shortfall > 0) receive cash.

    Step 3: If sum(shortfall) < cash:
            - Fully close all shortfalls.
            - Distribute residual = cash − sum(shortfall) across ALL
              investable sleeves proportionally to their target weights
              (normalized to exclude Cash / SPAXX).

    Cash / SPAXX is excluded; new cash is deployed into investable sleeves.

    Rationale column:
        "close drift"     — sleeve is below target, Step 2 allocation
        "mixed"           — sleeve is below target AND receives residual (Step 3)
        "maintain target" — sleeve is at/above target, residual only (Step 3)

    Args:
        portfolio_value:  current total portfolio value in dollars
        cash_to_deploy:   new cash to invest
        sleeve_weights:   current actual weights per sleeve (fractions)
        saa_targets:      SAA target weights per sleeve (fractions)
        ticker_to_sleeve: ticker → sleeve name
        prices:           ticker → current price
        coverage:         what the price layer actually resolved. REQUIRED and
                          keyword-only, and must stay that way — a default of
                          ``None`` recreates the defect exactly, letting a caller
                          that omits it fall back to the old conflated filter with
                          nothing to show the guard is inert. Build the record at
                          the call site rather than weakening this signature.

    Returns DataFrame with columns:
        Ticker, Sleeve, Rationale, Price, Suggested $, Suggested Shares.
    Empty DataFrame if cash <= 0 or no investable sleeves with ticker mappings.

    Raises ``UnpricedAllocationError`` when an allocated sleeve holds a security
    with no resolved price, and ``CoverageMismatchError`` when ``prices`` and
    ``coverage`` disagree about a ticker.
    """
    _EMPTY = pd.DataFrame(
        columns=["Ticker", "Sleeve", "Rationale", "Price", "Suggested $", "Suggested Shares"]
    )

    if cash_to_deploy <= 0 or portfolio_value < 0:
        return _EMPTY

    # Investable = all sleeves except cash
    investable = {s: t for s, t in saa_targets.items() if s != _CASH_SLEEVE}
    investable_total = sum(investable.values())
    if not investable or investable_total <= 0:
        return _EMPTY

    V = portfolio_value
    X = cash_to_deploy

    # Dollar shortfall per sleeve (current deficit only — does not include
    # the proportional share of new cash each sleeve would need to stay flat)
    shortfalls = {
        sleeve: V * max(0.0, target - sleeve_weights.get(sleeve, 0.0))
        for sleeve, target in investable.items()
    }
    total_shortfall = sum(shortfalls.values())

    if total_shortfall >= X:
        # Step 2: proportional to shortfall; only underweight sleeves participate
        sleeve_alloc: dict[str, float] = {
            s: (sf / total_shortfall) * X
            for s, sf in shortfalls.items()
            if sf > 0
        }
        step = 2
    else:
        # Step 3: full close + residual proportional to normalized target weight
        residual = X - total_shortfall
        sleeve_alloc = {
            sleeve: shortfalls.get(sleeve, 0.0) + residual * (target / investable_total)
            for sleeve, target in investable.items()
        }
        step = 3

    def _rationale(sleeve: str) -> str:
        if step == 2:
            return "close drift"
        if shortfalls.get(sleeve, 0.0) > 0:
            return "mixed"
        if V * (sleeve_weights.get(sleeve, 0.0) - investable.get(sleeve, 0.0)) > 0.01:
            return "above target"
        return "maintain target"

    # Scoped to sleeves that actually receive an allocation — the loop below skips
    # anything under half a cent, so a sleeve at 0.0 cannot produce a trade and an
    # unpriced holding in it has no bearing on one.
    sleeve_to_tickers = _placeable_by_sleeve(
        ticker_to_sleeve, prices, coverage,
        {s for s, d in sleeve_alloc.items() if d >= 0.005},
        caller="suggest_contributions",
    )

    rows = []
    raw_total = 0.0
    for sleeve in sorted(sleeve_alloc, key=lambda s: -sleeve_alloc[s]):
        dollars = sleeve_alloc[sleeve]
        if dollars < 0.005:
            continue
        tickers_in_sleeve = sorted(sleeve_to_tickers.get(sleeve, []))
        if not tickers_in_sleeve:
            continue
        per_ticker = dollars / len(tickers_in_sleeve)
        raw_total += dollars
        rationale = _rationale(sleeve)
        for ticker in tickers_in_sleeve:
            price = prices[ticker]   # guaranteed > 0 by filter above
            rows.append(
                {
                    "Ticker":           ticker,
                    "Sleeve":           sleeve,
                    "Rationale":        rationale,
                    "Price":            round(price, 2),
                    "Suggested $":      round(per_ticker, 2),
                    "Suggested Shares": round(per_ticker / price, 6),
                }
            )

    if rows:
        final_sum = sum(r["Suggested $"] for r in rows)
        assert abs(final_sum - X) <= SUM_INVARIANT_TOLERANCE, (
            f"suggest_contributions: Suggested $ sum {final_sum:.4f} ≠ cash {X:.4f}; "
            "check that all investable sleeves have at least one priced ticker"
        )
    return pd.DataFrame(rows) if rows else _EMPTY


def unfunded_target_sleeves(
    saa_targets: dict[str, float],
    ticker_to_sleeve: dict[str, str],
    prices: dict[str, float],
) -> list[str]:
    """SAA-targeted sleeves (target > 0) with no priced-held ticker.

    These are exactly the sleeves ``suggest_contributions`` would allocate cash to
    and then be unable to place — the condition that trips its Suggested-$ invariant.
    A caller guards on this BEFORE calling ``suggest_contributions`` (naming what to
    buy) rather than relaxing the invariant, which is deliberately left to fire.
    Returns a sorted list of sleeve names, empty when every targeted sleeve is
    covered. ``prices`` holds only tickers the account actually HOLDS (the page
    fetches prices per held ticker), so a sleeve is "unfunded" precisely when it
    holds nothing priceable.
    """
    priced = {
        ticker_to_sleeve[t] for t, p in prices.items()
        if p and p > 0 and t in ticker_to_sleeve
    }
    return sorted(s for s, tgt in saa_targets.items() if tgt > 0 and s not in priced)


# ── Band-status surfacing (Phase 33) — tax-aware, buy-only philosophy ──────────
#
# These read compute_drift() output and produce status prose. They deliberately
# do NOT suggest corrective sells: in a taxable account, selling an overweight
# sleeve to fix tolerance-band drift realizes capital gains that typically cost
# more than the tracking error they fix. Overweight drift is closed over time by
# redirecting new contributions, not by selling.

def closest_to_breach(drift_df: pd.DataFrame) -> dict | None:
    """
    The in-band SAA sleeve with the least headroom to its band edge.

    Headroom = band − |drift|, so the closest-to-breach sleeve is the one with
    the SMALLEST headroom — which is not necessarily the largest drift, because a
    wider band absorbs more drift. Returns a dict with sleeve, drift, band, and
    headroom (all fractions), or None if no SAA sleeve is in band.

    Only band-managed SAA sleeves (Target Weight > 0) are considered: a 0%-target
    residual bucket (e.g. "Other / Non-SAA" holdings) is not a tolerance-managed
    sleeve, so it is never reported as the closest to breach.
    """
    in_band = drift_df[drift_df["In Band"] & (drift_df["Target Weight"] > 0)]
    if in_band.empty:
        return None
    headroom = in_band["Band"] - in_band["Drift"].abs()
    sleeve = headroom.idxmin()
    return {
        "sleeve":   str(sleeve),
        "drift":    float(in_band.loc[sleeve, "Drift"]),
        "band":     float(in_band.loc[sleeve, "Band"]),
        "headroom": float(headroom.loc[sleeve]),
    }


def rebalance_action_text(row: pd.Series) -> str:
    """
    Tax-aware corrective-action text for a single drift row.

    Overweight breaches are NOT sold (realizing gains is tax-inefficient);
    they are closed by redirecting new contributions. Underweight breaches
    receive priority contribution allocation.
    """
    if bool(row["In Band"]):
        return "—"
    if float(row["Drift"]) > 0:
        return (
            "Over band — direct new contributions away from this sleeve until the "
            "drift closes; trim only within tax-advantaged accounts. Not sold here: "
            "realizing capital gains to rebalance is tax-inefficient."
        )
    return (
        "Under band — receives priority allocation from new contributions "
        "(see the buy suggestions below)."
    )


def interpret_rebalance_status(drift_df: pd.DataFrame) -> str:
    """
    One-line band-status verdict from compute_drift() output, in the project's
    tax-aware voice.

    All in band → confirms tolerance and names the closest-to-breach sleeve with
    its remaining headroom. Out of band → counts over/under, names them, and
    states the buy-only, contributions-not-sells correction policy.
    """
    n = len(drift_df)
    out = drift_df[~drift_df["In Band"]]

    if out.empty:
        c = closest_to_breach(drift_df)
        base = f"All {n} sleeves are within their tolerance bands."
        if c is None:
            return base
        return (
            f"{base} {c['sleeve']} is closest to breach at {c['drift'] * 100:+.1f}% "
            f"drift within its ±{c['band'] * 100:.1f}% band "
            f"({c['headroom'] * 100:.1f}% of headroom remaining)."
        )

    over  = out[out["Drift"] > 0]
    under = out[out["Drift"] < 0]
    k, j, m = len(out), len(over), len(under)

    parts = [f"{k} sleeve{'s' if k != 1 else ''} out of band: {j} over, {m} under."]
    if m:
        verb = "is" if m == 1 else "are"
        obj  = "it" if m == 1 else "them"
        parts.append(
            f"Underweight ({', '.join(under.index)}) {verb} addressed by directing "
            f"new contributions to {obj}."
        )
    if j:
        verb = "is" if j == 1 else "are"
        parts.append(
            f"Overweight ({', '.join(over.index)}) {verb} left to close via future "
            "contributions rather than sold, to avoid realizing capital gains in a "
            "taxable account."
        )
    return " ".join(parts)
