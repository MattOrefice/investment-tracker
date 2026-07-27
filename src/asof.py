"""Shared as-of date utilities — banner text for every Streamlit page."""
from datetime import date, timedelta

# A quarter-end is "priceable" if committed data reaches within this many calendar
# days of it. Deliberately the same window as src.attribution._last_adj_price's
# ``window_days`` default: that function decides whether a sleeve gets a real
# end-of-period price or is purged as a price gap, so tying the quarter cap to it
# means a quarter is offered exactly when attribution can actually price its close.
# Also absorbs a quarter-end landing on a weekend/holiday (Dec-31 on a Sunday), where
# the last real trading day legitimately precedes the calendar quarter-end.
QUARTER_END_COVERAGE_DAYS = 5


def _quarters_for_year(year: int) -> list[tuple[date, date, str]]:
    """The four (quarter_start, quarter_end, label) triples for ``year``.

    Quarter start = prior quarter-end date (conventional: Q1 return uses Dec-31 base price).
    """
    return [
        (date(year - 1, 12, 31), date(year, 3, 31),  f"Q1 {year}"),
        (date(year, 3, 31),      date(year, 6, 30),  f"Q2 {year}"),
        (date(year, 6, 30),      date(year, 9, 30),  f"Q3 {year}"),
        (date(year, 9, 30),      date(year, 12, 31), f"Q4 {year}"),
    ]


def _completed_quarters_descending(today: date, limit: int = 40):
    """Yield completed quarters newest-first, starting from ``today``.

    ``limit`` (10 years) is a runaway guard only — every caller stops on its own
    inception condition long before it, and an unbounded generator behind a
    stepping loop is how a bad frontier turns into a hang.
    """
    year = today.year
    emitted = 0
    while emitted < limit:
        for q_start, q_end, label in reversed(_quarters_for_year(year)):
            if q_end < today:
                yield q_start, q_end, label
                emitted += 1
                if emitted >= limit:
                    return
        year -= 1


def _most_recent_completed_quarter(today: date | None = None) -> tuple[date, date, str]:
    """Return (quarter_start, quarter_end, label) for the most recently completed quarter.

    The CALENDAR answer — it ignores whether any data exists to price that quarter.
    Use ``most_recent_reportable_quarter`` for the reportable one; this stays the
    pure calendar reference the staleness disclosure is measured against.
    """
    d = today or date.today()
    for q in _completed_quarters_descending(d):
        return q
    return date(d.year - 1, 9, 30), date(d.year - 1, 12, 31), f"Q4 {d.year - 1}"


def _resolve_frontier(frontier: "date | str | None") -> date | None:
    """Normalise a caller-supplied frontier; ``None`` means "resolve from the DB".

    Mirrors ``as_of_report_line``'s treatment of ``inception``: None is a request
    to look it up, not a request to skip it. Pass an explicit far-future date to
    opt out of the cap (what the pure-logic tests do).
    """
    if frontier is None:
        from src.holdings import committed_price_frontier  # lazy — avoids a DB import at module load
        frontier = committed_price_frontier()
    if frontier is None:
        return None
    if isinstance(frontier, str):
        return date.fromisoformat(frontier)
    return frontier


def _quarter_end_is_priceable(q_end: date, frontier: date | None) -> bool:
    """True when committed data reaches within QUARTER_END_COVERAGE_DAYS of ``q_end``.

    ``frontier is None`` (no committed prices / no resolvable account) returns True:
    the cap expresses "the data cannot support this quarter", and absent data it has
    no opinion. A DB in that state renders an empty/structural report anyway, so
    failing open here cannot publish a wrong figure — it only avoids suppressing
    every quarter on a bare or freshly-seeded database.
    """
    if frontier is None:
        return True
    return frontier >= q_end - timedelta(days=QUARTER_END_COVERAGE_DAYS)


# Empty-state copy when no completed quarter has elapsed since inception.
# Pinned literal — referenced by the Performance page (Section 1a snapshot and
# the PDF-export expander) and the every-page as-of banner. Update all callers
# and tests together if this string changes.
NO_COMPLETED_QUARTER = "No completed quarter yet."


def most_recent_reportable_quarter(
    inception: "date | str",
    today: date | None = None,
    frontier: "date | str | None" = None,
) -> "tuple[date, date, str] | None":
    """Most recent completed quarter the portfolio existed for AND the data can price.

    Returns (quarter_start, quarter_end, label), or ``None`` when no quarter
    qualifies. Two independent gates, both of which step the answer BACKWARD in
    time rather than producing a quarter that cannot be honoured:

    1. **Inception.** A quarter is reportable only if ``quarter_end >= inception``
       — the portfolio existed for at least part of it (a partial first quarter,
       ``quarter_start < inception <= quarter_end``, still qualifies). Walking
       back past inception returns ``None`` so callers render the empty state
       rather than an all-zero "locked" report for a span preceding the portfolio.

    2. **Data frontier.** A quarter is reportable only if committed prices reach
       its close (see ``_quarter_end_is_priceable``). Without this gate the
       calendar rolls into Q3 while the price history still ends in July, and the
       report renders that quarter anyway: attribution, benchmarks and holdings
       visibly break on the gap, but the value series forward-fills, so the
       executive summary and the trailing-period table print a three-week move
       under a quarter's label. Silently wrong where it is quoted, loudly broken
       everywhere else — hence a cap rather than a warning. The reported quarter
       steps back to the newest one the data actually supports; pair this with
       ``quarter_staleness_note`` so the artifact says which quarter it stepped
       back to and why.

    ``inception`` accepts a date or an ISO date string. ``frontier`` defaults to
    ``None`` = resolve from the committed price cache (``committed_price_frontier``,
    no network); pass an explicit date to pin it, including a far-future date to
    exercise the uncapped inception logic on its own.
    """
    if isinstance(inception, str):
        inception = date.fromisoformat(inception)
    d = today or date.today()
    resolved = _resolve_frontier(frontier)

    for q_start, q_end, label in _completed_quarters_descending(d):
        if q_end < inception:
            return None          # stepped back past the portfolio's existence
        if _quarter_end_is_priceable(q_end, resolved):
            return q_start, q_end, label
    return None


def quarter_staleness_note(
    inception: "date | str",
    today: date | None = None,
    frontier: "date | str | None" = None,
) -> str | None:
    """Disclosure for a report whose quarter was stepped back by the data frontier.

    Returns ``None`` in the normal case — the reported quarter IS the calendar's
    most recent completed one, so there is nothing to explain. Returns a single
    sentence naming both quarters and the frontier date when they differ, for the
    PDF cover and the Performance page.

    Deliberately silent when the step-back is caused by *inception* rather than by
    the frontier: that case already renders ``NO_COMPLETED_QUARTER`` empty-state
    copy, and a second explanation of the same fact reads as a defect.
    """
    if isinstance(inception, str):
        inception = date.fromisoformat(inception)
    d = today or date.today()
    resolved = _resolve_frontier(frontier)
    if resolved is None:
        return None

    _, calendar_end, calendar_label = _most_recent_completed_quarter(d)
    reported = most_recent_reportable_quarter(inception, d, frontier=resolved)
    if reported is None or reported[2] == calendar_label:
        return None
    if calendar_end < inception:
        return None              # pre-inception, not a data-frontier step-back

    # The closing clause is not padding: this note appears on a PUBLIC artifact
    # whose reader cannot ask whether "stale" also means the printed figures are
    # unreliable. It answers that — the reported quarter closed before the
    # frontier, so it is complete; only the newer quarter is missing.
    return (
        f"Reporting {reported[2]}, not {calendar_label}: price data ends "
        f"{format_long_date(resolved)}, which cannot support a quarter that "
        f"closed {format_long_date(calendar_end)}. {reported[2]} closed before "
        f"that date, so the figures in this report are complete."
    )


def latest_report_link(
    existing_reports,
    inception: "date | str",
    today: date | None = None,
    frontier: "date | str | None" = None,
):
    """The report to surface as 'Latest report', or None to suppress the link.

    ``existing_reports`` is a newest-first sequence of report paths/names. Returns
    the newest one only when a completed quarter is currently reportable
    (``most_recent_reportable_quarter`` is not None); when none is reportable —
    the pre-inception case — returns None so a stale pre-inception report is not
    surfaced above the "No completed quarter yet." empty state. Inception-gated
    through the same helper the report and tooltip use (single source of truth);
    report *generation* is already inception-gated, so a reportable-quarter state
    only ever has reportable or custom reports on disk to link.
    """
    if most_recent_reportable_quarter(inception, today, frontier=frontier) is None:
        return None
    return existing_reports[0] if existing_reports else None


def reportable_quarter_phrase(
    inception: "date | str",
    today: date | None = None,
    frontier: "date | str | None" = None,
) -> str:
    """Parenthetical quarter label for help/tooltip copy.

    '(Q1 2026)' when a completed quarter is reportable, else
    '(no completed quarter yet)'. Sourced from most_recent_reportable_quarter so
    help text never names a stale, pre-inception, or unpriceable quarter — it
    tracks the frontier cap automatically, which is the point of routing every
    quarter-naming surface through that one helper.
    """
    q = most_recent_reportable_quarter(inception, today, frontier=frontier)
    return f"({q[2]})" if q else "(no completed quarter yet)"


def format_long_date(d: "date | str") -> str:
    """Long-form date for banner and label copy. e.g. 'June 9, 2026'.

    Composed from strftime('%B') plus the integer day rather than a %-d/%#d
    directive, which is platform-specific. Accepts a date or an ISO string.
    """
    if isinstance(d, str):
        d = date.fromisoformat(d)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def as_of_live_line(today: date | None = None) -> str:
    """Return the live-data line only. e.g. 'Live data as of May 10, 2026.'

    ``today`` defaults to the current date; it is injectable so a composed banner
    can be pinned in a test without patching the clock.
    """
    return f"Live data as of {format_long_date(today or date.today())}."


def as_of_report_line(
    today: date | None = None,
    inception: "date | str | None" = None,
    frontier: "date | str | None" = None,
) -> str:
    """Return the locked-report line. e.g. 'Latest locked quarterly report: Q1 2026 (March 31, 2026).'

    When the most-recent completed quarter entirely predates inception (no
    completed quarter has elapsed since the portfolio began), returns the
    empty-state copy instead of naming a quarter the portfolio never existed
    during. ``inception`` defaults to the canonical MIN(trade_date).

    Names the REPORTABLE quarter, so once the frontier cap steps the report back
    this banner steps back with it — the every-page banner and the PDF cannot
    disagree about which quarter is current.
    """
    today = today or date.today()
    if inception is None:
        from src.holdings import get_inception_date, get_portfolio_account_id
        inception = get_inception_date(account_id=get_portfolio_account_id())
    q = most_recent_reportable_quarter(inception, today, frontier=frontier)
    if q is None:
        return NO_COMPLETED_QUARTER
    _, q_end, q_label = q
    return f"Latest locked quarterly report: {q_label} ({format_long_date(q_end)})."


def inception_line(inception: "date | str", days: int) -> str:
    """Return the inception clause. e.g. 'Portfolio inception June 9, 2026 (35 days).'

    ``days`` is supplied by the caller rather than derived from ``date.today()``
    here: the pages that show a portfolio age measure it to the settled display
    anchor (the last complete trading day), not to today, so a locally recomputed
    count would disagree with the figure alongside it by a day or more.
    """
    return f"Portfolio inception {format_long_date(inception)} ({days} day{'s' if days != 1 else ''})."


def as_of_banner() -> str:
    """Return muted one-line as-of banner for display under each page title.

    Format: "Live data as of May 4, 2026. Latest locked quarterly report: Q1 2026 (March 31, 2026)."
    Both dates are computed dynamically — no hardcoded strings.
    """
    return f"{as_of_live_line()} {as_of_report_line()}"


def as_of_banner_with_inception(
    inception: "date | str", days: int, today: date | None = None
) -> str:
    """as_of_banner() extended with the portfolio's inception date and age.

    Format: "Live data as of July 14, 2026. Portfolio inception June 9, 2026
    (35 days). Latest locked quarterly report: Q2 2026 (June 30, 2026)."

    A separate entry point rather than a widened as_of_banner(): the plain banner
    is rendered by every page, most of which have no inception context to pass and
    no reason to name it. Every component is computed — no literal dates.
    """
    return (
        f"{as_of_live_line(today)} "
        f"{inception_line(inception, days)} "
        f"{as_of_report_line(today, inception)}"
    )
