"""Shared as-of date utilities — banner text for every Streamlit page."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Every rendered time is New York time, the market's clock. A named zone, never a
# fixed offset: it is EDT (UTC-4) from March to November and EST (UTC-5) otherwise,
# and the label is "ET" for both.
ET = ZoneInfo("America/New_York")

# A quarter-end is "priceable" if committed data reaches within this many calendar
# days of it. Deliberately the same window as src.attribution._last_adj_price's
# ``window_days`` default: that function decides whether a sleeve gets a real
# end-of-period price or is purged as a price gap, so tying the quarter cap to it
# means a quarter is offered exactly when attribution can actually price its close.
# Also absorbs a quarter-end landing on a weekend/holiday (Dec-31 on a Sunday), where
# the last real trading day legitimately precedes the calendar quarter-end.
QUARTER_END_COVERAGE_DAYS = 5

# ── Committed market-data staleness (factor / valuation inputs) ────────────────
# The tracked market-data files (Ken French factor caches, Shiller CAPE,
# trailing P/E) are refreshed ONLY by tools/refresh_market_data.py — loaders
# never fetch or write. These thresholds decide when a file's age is SURFACED
# to the reader; they are read against the DATA frontier (the series' last
# row), never file mtime, because a git checkout resets mtime to checkout time
# and says nothing about content age.
#
# They are deliberately NOT the old auto-refresh trigger (35 days): the repo's
# one historical refresh (2026-05-06) fetched Ken French data already 36 days
# behind — the source's own publication lag breaches 35 — so a 35-day banner
# would fire on day-one-fresh data and train the reader to ignore it. 35 was
# tuned as a REFETCH trigger, not a staleness alarm. These are sized so that
# firing means a refresh CYCLE was missed, not merely that the source lags:
#   factors:   ~4-6 week Ken French publication lag + a monthly cadence ≈ 70d
#   valuation: multpl carries current-month rows (fresh ≈ ≤31d) + a month ≈ 45d
MARKET_DATA_STALE_DAYS_FACTORS = 70
MARKET_DATA_STALE_DAYS_VALUATION = 45


def data_vintage(label: str, frontier: "date | None") -> str:
    """The one wording for a committed series' vintage (2026-09-25 audit, item 4):
    its end date, and that the end is where this app's last refresh of its committed
    copy stopped. It makes no claim about the source's publication schedule, which
    the app cannot see: the same sentence was worded three ways, and "a publication
    lag" was false while the source had published two months the copy lacked."""
    if frontier is None:
        return f"No {label} data is on file."
    return f"{label} data ends {format_long_date(frontier)}, as of this app's last refresh."


def staleness_note(label: str, frontier: "date | None", threshold_days: int) -> "str | None":
    """Rendered staleness sentence for a committed market-data series.

    Returns None while the data is within ``threshold_days`` of today — fresh
    data renders nothing. A missing frontier (unreadable/absent file) returns a
    loud sentence rather than None: absence must never present as freshness.

    The maintainer's command to fix it is personal-mode only: a visitor to the
    public demo can do nothing with "Run tools/refresh_market_data.py".
    """
    from src.config import IS_DEMO
    if frontier is None:
        note = f"{label} data is unavailable: the committed file is missing or unreadable."
        return note if IS_DEMO else note + " Restore it from git or run tools/refresh_market_data.py."
    days = (date.today() - frontier).days
    if days <= threshold_days:
        return None
    note = (f"{label} data ends {format_long_date(frontier)} ({days} days ago), as of this "
            f"app's last refresh.")
    return note if IS_DEMO else note + " Run tools/refresh_market_data.py and commit the result."


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


# Distinguishes "argument omitted — resolve it" from an explicit None, which means
# "known to be absent". Both are meaningful for a price frontier and they cannot
# share a default: collapsing them makes the nothing-committed state unreachable,
# because an omitted frontier would go and find a real one. Same pattern, and the
# same reasoning, as location_actions._UNSET.
_UNSET = object()


def as_of_live_line(
    today: date | None = None,
    *,
    frontier: "date | str | None" = _UNSET,
    coverage=None,
) -> str:
    """What the app can honestly say about how current its prices are.

    "Prices through August 10" makes no completeness claim; "Live data as of
    August 10" would. That distinction is the whole design: VINTAGE is knowable on
    any page from the committed cache, COVERAGE is knowable only where a
    PriceCoverage record exists, and the banner must never let the first imply the
    second.

    Four states:

      1 fully current   frontier == today and nothing unresolved -> the original
                        "Live data as of <date>." sentence, unchanged
      2 past            frontier < today                        -> "Prices through
                        <date>", plus "— N weekdays behind" when closes are missing
      3 incomplete      something unresolved (needs a record)   -> as state 2, plus
                        "N of M holdings have no committed price"
      4 nothing         no frontier at all                      -> "No committed
                        price data." — absence must never present as freshness,
                        the same rule staleness_note applies above.

    THE COUNT IS OF MISSING CLOSES, not of calendar days. The rule is settled closes
    strictly before today, so the latest close expected is the last session before
    today, and a frontier there is current: it gets no count. Counting calendar days
    called that "1 day behind", and a Monday "3 days behind". N is the sessions after
    the frontier and before today (_sessions_missing), by demo_refresh's calendar:
    weekdays, with no holiday table, so the day after a market holiday reads one
    behind. It says "weekdays" because that is what it counts.

    NO THRESHOLD, deliberately. staleness_note tolerates 70/45 days because it
    guards a refresh cycle and a committed factor file is expected to lag. Prices
    are expected to be current, so any missing close is worth stating — and a
    threshold here would recreate the very defect this fixes for every lag below
    its cutoff.

    ``frontier`` omitted means "resolve it" (via committed_price_frontier, ~2ms);
    ``frontier=None`` means "known to be absent" and gives state 4. ``coverage``
    absent means this caller has no record to consult: it reports vintage and says
    NOTHING about coverage. Nine of the twelve call sites are in that position, and
    their silence is the honest limit of what the frontier knows — it skips a
    holding with no committed price by construction (holdings.py:200-206), so it
    cannot see the gap it would need to report.
    """
    ref = today or date.today()

    if frontier is _UNSET:
        if coverage is not None:
            frontier = coverage.frontier_served
        else:
            # Lazy import: avoids a DB import at module load, as at line ~108.
            from src.holdings import committed_price_frontier
            frontier = committed_price_frontier(ref)

    if frontier is None:
        return "No committed price data."

    served = date.fromisoformat(frontier) if isinstance(frontier, str) else frontier
    lag = (ref - served).days
    gap = tuple(coverage.unresolved) if coverage is not None else ()

    # WHICH frontier, stated (#265). A coverage record's frontier_served is "how
    # current is what THIS PAGE served"; the committed frontier is "the latest
    # SETTLED close every holding has". Both include today's close once it is stored
    # (the 2026-09-25 audit made them one rule), so they differ only where a
    # personal-mode read served an open session's bar, which is never stored.
    basis = "as served to this page" if coverage is not None else "settled closes"

    # The demo's daily refresh (#368 item 3), when it has run in this process. After
    # a success that reached this date, the date is as current as settled closes
    # allow, so the line says when it was fetched, ahead of state 1: once the
    # refresh stores today's close the frontier IS today, and the fetch time is the
    # part the reader needs. After a failure, the line says so, what is served in
    # its place, and when it retries: a failed fetch must never read as a quiet old
    # date.
    from src.demo_refresh import state as _refresh_state
    refresh = _refresh_state()
    if (refresh is not None and refresh.status == "fetched" and not gap
            and refresh.served_through and served >= date.fromisoformat(refresh.served_through)):
        return (f"Prices through {format_long_date(served)} ({basis}, fetched "
                f"{_when(refresh.attempted_at)}).")

    if lag <= 0 and not gap:
        # A page whose coverage record saw the open session's bar lands here, so this
        # is the state that most needs the unsettled-price sentence (#160, item 4i).
        return f"Live data as of {format_long_date(ref)}." + _live_mark_sentence()

    missing = _sessions_missing(served, ref)
    line = f"Prices through {format_long_date(served)} ({basis})"
    if missing:
        line += f" — {missing} weekday{'' if missing == 1 else 's'} behind"
    if gap:
        line += (", and " if missing else " — ")
        line += (f"{len(gap)} of {len(coverage.requested)} holdings have "
                 "no committed price")
    if refresh is not None and refresh.status == "failed":
        instead = (f"prices last fetched {_when(refresh.fetched_before)}"
                   if refresh.fetched_before else "the committed snapshot")
        line += (f". The daily price fetch failed {_when(refresh.attempted_at)}; "
                 f"serving {instead} until it retries after "
                 f"{_when(refresh.next_attempt_at)}")
    return line + "." + _live_mark_sentence()


def _live_mark_sentence() -> str:
    """The banner's sentence for an open session's bar ("Current values include
    today's unsettled price, quoted at 11:02 AM ET."), when this process has served
    one that no stored close covers; else "".

    #160 keeps an open session's bar out of the cache and hands it to the caller, so
    in personal mode during market hours a current value (Performance, Risk) includes
    a price that is not a close, while the lines above describe settled closes. The
    time is the quote's, in ET, and it is the earliest one served: a process keeps
    serving the bar it fetched, so the price can be hours old. Returns use settled
    closes only (holdings.last_settled_price_date) and do not include it."""
    from src.prices import live_marks
    marks = live_marks()
    if not marks:
        return ""
    from src.holdings import committed_price_frontier
    stored = committed_price_frontier()
    newer = [m for m in marks.values()
             if stored is None or date.fromisoformat(m["date"]) > (
                 date.fromisoformat(stored) if isinstance(stored, str) else stored)]
    if not newer:
        return ""
    quoted = sorted(m["quoted_at"] for m in newer if m.get("quoted_at"))
    when = (f", quoted at {datetime.fromisoformat(quoted[0]).astimezone(ET):%I:%M %p} ET"
            .replace(" at 0", " at ") if quoted else "")
    return f" Current values include today\u2019s unsettled price{when}."


def _sessions_missing(served: date, today: date) -> int:
    """Closes the settled-closes rule expects by ``today`` that ``served`` lacks: the
    sessions after ``served`` and before ``today``, by demo_refresh.is_session. Zero
    when ``served`` is the last session before today, or later."""
    from src.demo_refresh import is_session
    day, n = served + timedelta(days=1), 0
    while day < today:
        n += is_session(day)
        day += timedelta(days=1)
    return n


def _when(t: "datetime | str") -> str:
    """'September 24, 2026 at 5:30 PM ET' for a datetime or an ISO string: the
    moment in New York time, whatever zone it was recorded in. A naive value is read
    as the machine's local time, which is how datetime.now() recorded it."""
    if isinstance(t, str):
        t = datetime.fromisoformat(t)
    t = t.astimezone(ET)
    return f"{format_long_date(t.date())} at {t:%I:%M %p}".replace(" at 0", " at ") + " ET"


def today_et(now: "datetime | None" = None) -> date:
    """Today's date in New York, for a date stamped on something a reader keeps (the
    PDF). The server's own date is UTC on the public demo, a day ahead after 8 PM ET."""
    return (now or datetime.now(timezone.utc)).astimezone(ET).date()


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


def as_of_banner(*, frontier: "date | str | None" = _UNSET, coverage=None) -> str:
    """Return muted one-line as-of banner for display under each page title.

    Format: "Live data as of May 4, 2026. Latest locked quarterly report: Q1 2026 (March 31, 2026)."
    Both dates are computed dynamically — no hardcoded strings.
    """
    return f"{as_of_live_line(frontier=frontier, coverage=coverage)} {as_of_report_line()}"


def as_of_banner_with_inception(
    inception: "date | str", days: int, today: date | None = None,
    *, frontier: "date | str | None" = _UNSET, coverage=None,
) -> str:
    """as_of_banner() extended with the portfolio's inception date and age.

    Format: "Live data as of July 14, 2026. Portfolio inception June 9, 2026
    (35 days). Latest locked quarterly report: Q2 2026 (June 30, 2026)."

    A separate entry point rather than a widened as_of_banner(): the plain banner
    is rendered by every page, most of which have no inception context to pass and
    no reason to name it. Every component is computed — no literal dates.
    """
    return (
        f"{as_of_live_line(today, frontier=frontier, coverage=coverage)} "
        f"{inception_line(inception, days)} "
        f"{as_of_report_line(today, inception)}"
    )
