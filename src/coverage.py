"""What a price-derived figure is actually made of.

Every dollar figure in this app is a sum over holdings, and until now a holding
whose price could not be fetched contributed ``0.0`` to that sum with nothing
anywhere recording that it had done so (see GitHub #188). A zeroed holding does
not look missing: it looks like a sleeve that fell to zero weight, which renders
as a real drift, a real band breach, and — on the Capital Deployment page — a real
buy instruction.

This module holds the record that makes the gap representable. It is deliberately
a plain value object with no pandas dependency: it travels beside a frame as an
explicit return value, never on ``DataFrame.attrs``. Two reasons, one upstream and
one local:

* pandas documents ``attrs`` as *"experimental and may change without warning"*,
  and requirements.txt admits an open range (``pandas>=2.2.0``), so no measurement
  on any one version licenses the next. A disclosure substrate resting on that is
  the silent-failure class relocated one layer up.
* ``attrs`` is dropped by ``merge`` and by ``concat`` when the inputs' attrs differ
  (pandas' own documented behaviour). A substrate that survives the three pages it
  was built for and vanishes the moment coverage reaches ``household.py`` or the
  PDF's merged frames is not a substrate.

WHY THE PRODUCERS ARE TWO-LAYER, which is the load-bearing design decision here:
``get_prices`` raises on every failure path, so from a caller's ``except`` clause a
blocked network and an empty cache arrive as the same ``ValueError``. Only the price
layer can consult the cache before the fetch and therefore tell ``no_cached_rows``
from ``fetch_failed``. A one-layer implementation that classified misses from
``holdings.py``'s ``except`` would report every miss as ``fetch_failed``; the reason
code is not decoration, it decides downstream behaviour (a delisted holding is a
legitimate degradation, a failed fetch is a defect), so it must be produced where
it is knowable. See ``prices.classify_miss`` and
``holdings._price_and_status``, which assembles its results.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Why a ticker produced no usable price. The code SELECTS THE DOWNSTREAM
# BEHAVIOUR, so the vocabulary is closed and validated: an unrecognised reason is
# a silently unhandled category, which is the defect this module exists to stop.
#
# Not every reason is produced by every layer, by design. The price layer can
# determine no_cached_rows / fetch_failed / empty_window mechanically; delisted
# needs a delisting check and not_in_snapshot belongs to the quarter-snapshot
# reader (see #193). They are accepted here so those layers need no vocabulary
# change when they start reporting.
UNRESOLVED_REASONS: frozenset[str] = frozenset({
    "no_cached_rows",   # nothing cached for the window, and the fetch could not run
    "fetch_failed",     # rows existed or the ticker was known, but the fetch errored
    "empty_window",     # the call succeeded and returned nothing for this window
    "not_in_snapshot",  # a locked quarter snapshot does not carry this ticker
    "delisted",         # the provider says the symbol no longer trades
})


class CoverageInvariantError(ValueError):
    """A coverage record that could hide a gap. Raised at construction, never
    logged and continued: a record that cannot be trusted is worse than none,
    because every consumer downstream reads it as a disclosure."""


@dataclass(frozen=True)
class Unresolved:
    """A requested ticker that produced no usable price, and why."""

    ticker: str
    reason: str


@dataclass(frozen=True)
class DroppedBar:
    """A bar the price layer deleted rather than served — a null or non-positive
    close. Dropping is correct (nothing traded is not knowable), so what is
    missing is the record of the drop. Populated by a later PR; empty here."""

    ticker: str
    date: str
    reason: str


@dataclass(frozen=True)
class Substitution:
    """A price that resolved, but not on the basis the caller believes.

    Two live examples, both invisible today: SPAXX is priced off BIL at five sites
    and "BIL" appears nowhere a reader can reach, and the locked quarter snapshot
    serves adj_close in the ``close`` column against two written raw-close
    guarantees (#193). Both are RESOLVED prices, so a requested/resolved/unresolved
    triple would report full coverage over a PDF whose Market Value column is on
    the wrong basis. Populated by a later PR; empty here."""

    ticker: str
    kind: str
    detail: str


@dataclass(frozen=True)
class TickerStatus:
    """One ticker's outcome, as the price layer saw it. Aggregated into a
    PriceCoverage by whichever producer asked."""

    ticker: str
    resolved: bool
    reason: str | None = None
    served_through: str | None = None
    error: BaseException | None = None


@dataclass(frozen=True)
class PriceCoverage:
    """What a price-derived frame is made of: who was asked for, who answered,
    who did not and why, and the vintage of what was served.

    Coverage and vintage are separate questions. A record can be complete and
    still stale — every ticker resolved, but from three days ago because the
    trailing-gap fetch could not reach the network. That is the ordinary state of
    an offline render, and it is why the freshness banner (#189) needs a staleness
    disclosure rather than a coverage marker.
    """

    requested: tuple[str, ...]
    resolved: tuple[str, ...]
    unresolved: tuple[Unresolved, ...]
    as_of_requested: str

    # frontier_served answers "HOW CURRENT IS WHAT I SERVED" — the MIN over the
    # dates actually served, INCLUDING as_of_requested itself.
    #
    # Do not copy holdings.committed_price_frontier's `price_date < today` rule in
    # here. That function answers a different question — "what date can I CLAIM AS
    # SETTLED" — and excludes today so a partial mid-session bar cannot pose as a
    # settled frontier. Applying that exclusion to this field reports None
    # precisely when every price is fully current, i.e. blanks the disclosure
    # exactly when coverage is best. It was written that way once and the bug
    # surfaced only when a suite run brought the cache up to date mid-run.
    #
    # Whether a same-day bar is settled enough to anchor a report stays the
    # caller's judgement, made on the evidence this field gives it.
    frontier_served: str | None = None

    dropped_bars: tuple[DroppedBar, ...] = ()
    substitutions: tuple[Substitution, ...] = ()

    def __post_init__(self) -> None:
        accounted = set(self.resolved) | {u.ticker for u in self.unresolved}
        if set(self.requested) != accounted:
            raise CoverageInvariantError(
                "a requested ticker must be either resolved or unresolved — "
                f"requested {sorted(self.requested)}, accounted for "
                f"{sorted(accounted)}. A ticker that is neither is exactly the "
                "silent gap this record exists to make impossible."
            )
        unknown = {u.reason for u in self.unresolved} - UNRESOLVED_REASONS
        if unknown:
            raise CoverageInvariantError(
                f"unknown unresolved reason(s) {sorted(unknown)}; the vocabulary "
                f"is {sorted(UNRESOLVED_REASONS)}. The reason selects downstream "
                "behaviour, so an unrecognised one would be silently unhandled."
            )

    @property
    def is_complete(self) -> bool:
        """True only when nothing is missing, nothing was dropped, and nothing was
        substituted — a strict conjunction over all three.

        DO NOT USE THIS AS A RENDER GATE. It is a summary for tests and logs. Once
        ``substitutions`` is populated by a later PR, any book holding SPAXX reads
        incomplete on *every* render, because SPAXX is priced off BIL — so a marker
        keyed on this boolean would be permanently on and would say nothing about
        what is actually wrong. Consumers must key their disclosure on the specific
        field they can act on: ``unresolved`` (a holding contributed nothing),
        ``dropped_bars`` (a bar was deleted), ``substitutions`` (the basis differs),
        or ``stale_days`` (the vintage lags the request). Each of those has a
        different correct rendering; the boolean has none.
        """
        return not (self.unresolved or self.dropped_bars or self.substitutions)

    @property
    def stale_days(self) -> int | None:
        """Days between the date asked for and the newest date actually served.
        None when nothing was served, which is not the same as zero."""
        if self.frontier_served is None:
            return None
        return (date.fromisoformat(self.as_of_requested)
                - date.fromisoformat(self.frontier_served)).days

    def unresolved_tickers(self) -> frozenset[str]:
        """The names only, for callers that report which holdings are missing."""
        return frozenset(u.ticker for u in self.unresolved)


def unresolved_marker(cov: PriceCoverage) -> "str | None":
    """The shared body of a coverage marker, or None when there is nothing to say.

    Returns None — not an empty string — when no holding is unresolved. A caller
    must branch on it rather than render it unconditionally: an empty marker is
    invisible in a rendered page and invisible to a digest, and it is the specific
    leak tests/render/test_coverage_markers_render.py's control half exists to
    catch.

    Keyed on ``unresolved`` ALONE, deliberately. Not on ``is_complete`` (see that
    property's docstring — it will read False on every render once substitutions
    populate, and a marker wired to it would be permanently on), and not on
    ``dropped_bars`` or ``substitutions``, which describe different failures and
    need different sentences. Those get their own builders when their fields are
    populated.

    The phrase "no committed price" is the marker SIGNATURE: every coverage marker
    on every page carries it, which is what makes "no marker rendered" checkable
    without enumerating three pages of prose.

    The vintage rides along because a reader told WHICH holdings failed asks AS OF
    WHEN in the same breath. That is ``frontier_served`` — this marker's own
    statement about its own figures — and is distinct from the every-page freshness
    banner (``asof.as_of_live_line``, GitHub #189), which is a separate surface.
    """
    if not cov.unresolved:
        return None
    names = ", ".join(sorted(cov.unresolved_tickers()))
    served = (f" Prices served through {cov.frontier_served}."
              if cov.frontier_served else
              " No committed price date is available for any holding.")
    return (f"{len(cov.unresolved)} of {len(cov.requested)} holdings have "
            f"no committed price ({names}), so their market value counted as "
            f"zero.{served}")


def coverage_from_statuses(
    statuses: "list[TickerStatus] | tuple[TickerStatus, ...]",
    as_of: str,
) -> PriceCoverage:
    """Aggregate per-ticker statuses into one record.

    The frontier is the MIN over what was actually served: MIN because a report
    consumes every holding, so one ticker reaching a later date does not make the
    portfolio priceable there.

    Two differences from ``holdings.committed_price_frontier``, both deliberate:

    * It does NOT exclude the requested date. That function asks "what date can I
      claim as SETTLED", so it requires ``price_date < today`` to keep a partial
      mid-session bar from posing as a settled frontier. This field answers a
      different question — "how current is what I served" — and excluding the
      requested date would report ``None`` precisely when every price is fully
      current, i.e. blank out the disclosure exactly when coverage is best.
      Whether a same-day bar is settled enough to anchor a report stays the
      caller's judgement, on the evidence this field gives it.
    * That function SKIPS a holding with no committed price, which is why it reads
      identically whether every holding is priced or two are missing entirely.
      Here those holdings are the ``unresolved`` tuple.
    """
    requested = tuple(dict.fromkeys(s.ticker for s in statuses))
    resolved = tuple(s.ticker for s in statuses if s.resolved)
    unresolved = tuple(
        Unresolved(s.ticker, s.reason or "fetch_failed")
        for s in statuses if not s.resolved
    )
    served = [s.served_through for s in statuses
              if s.resolved and s.served_through]
    return PriceCoverage(
        requested=requested,
        resolved=resolved,
        unresolved=unresolved,
        as_of_requested=as_of,
        frontier_served=min(served) if served else None,
    )
