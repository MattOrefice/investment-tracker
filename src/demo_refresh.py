"""The public demo's daily price refresh (#368 item 3).

The demo serves a committed snapshot (demo.db) whose prices end on a fixed date. In
demo mode app.py calls refresh_if_due() at startup, before the landing page reads its
date. Every ticker the demo holds prices for is fetched from its last stored date to
the latest settled close, into the runtime cache (src.db, #368 item 2), and served
over the snapshot. demo.db is never written.

WHEN. After a success, the next attempt waits for the next New York close plus a
margin for the provider to publish it: no newer settled close can exist before then,
and a container that stays up still moves daily. After a failure, the next attempt
waits RETRY_AFTER. Between attempts nothing fetches, because the refresh switches off
the price layer's gap fills (prices.set_gap_fetch): a failed fetch is retried on a
timer, never on every render, which would slow every page and invite throttling.

SETTLED CLOSES ONLY. fetch_prices never caches a bar whose session is still open
(prices.unsettled_bar_date), so a mid-session attempt stores through the previous
close and the next attempt picks up the rest.

AND THE CLOSES IT LACKS. A response that leaves one session out and holds later ones
puts a gap inside a history, behind the date the next fetch starts from. After the
fetch, each attempt runs the gap check on the cache and asks again for every close it
names (src.price_gaps, #501).

What the banner reports (asof.as_of_live_line) comes from state(): the date served
and its basis, and, when the fetch failed, that it failed, what is served instead,
and when it retries. In tests the refresh never runs: conftest sets
DEMO_DAILY_FETCH=0, and tests keep reading fixed data.
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple, Optional
from zoneinfo import ZoneInfo

_NY = ZoneInfo("America/New_York")
# A failed attempt is retried after this long, never sooner.
RETRY_AFTER = timedelta(minutes=30)
# The provider publishes a session's close a little after 16:00 New York.
_PUBLISH_MARGIN = timedelta(minutes=30)
# Four requests at a time: the whole set in a few seconds rather than half a minute,
# without the burst that draws a 429. Their stores take turns (prices._STORE_LOCK, #580).
_WORKERS = 4


class RefreshState(NamedTuple):
    """One attempt's outcome, as the banner reports it."""

    status: str                            # "fetched" | "failed"
    attempted_at: datetime                 # UTC
    next_attempt_at: datetime              # UTC: nothing fetches before this
    served_through: Optional[str]          # committed_price_frontier after the attempt
    fetched_before: Optional[str]          # a previous success on this seed, ISO UTC, or None
    failed: tuple = ()                     # (ticker, reason) for each fetch that raised
    gaps: tuple = ()                       # (ticker, ISO date): closes still lacking inside a
                                           # history after they were asked for again (#501)


_STATE: Optional[RefreshState] = None
_LOCK = threading.Lock()


def _forget_last_attempt() -> None:
    """A re-seed dropped every fetched row with the seed it was fetched into (src.db,
    #480), so the next refresh_if_due fetches again, as a restarted process would,
    instead of waiting out the last attempt's timer on the committed snapshot."""
    global _STATE
    _STATE = None


def enabled() -> bool:
    """False in tests (DEMO_DAILY_FETCH=0), or before the runtime cache is in use:
    fetching into demo.db itself is the one thing this must never do."""
    import src.db as db
    return os.environ.get("DEMO_DAILY_FETCH", "1") != "0" and db._RUNTIME_CACHE is not None


def state() -> Optional[RefreshState]:
    """The latest attempt in this process, or None if none has run."""
    return _STATE


# NYSE full-day closures, 1990 to 2028. Early closes are sessions. Static, so no
# calendar dependency. Outside these years every weekday counts as a session, the rule
# before this table, so the close-out refuses to run (holiday_table_refusal), and
# tests/test_market_holidays.py fails, once the table does not reach the end of next
# year: extend it from nyse.com/markets/hours-calendars before then (#466).
#
# IT REACHES BACK TO THE EARLIEST START ANY get_prices CALLER PASSES (decided by the
# owner on October 18, 2026, #644): 1990-01-01, the Correlations page's pair history.
# Asset Evaluation reads from 2018-01-01 and a quarter lock from 2020-01-01, both NYSE
# holidays, and prices._gap_fetch never fetches a window with no session: the table is
# what says a window has none. Each year is entered from NYSE's own published calendars
# (NYSE_CALENDAR_SOURCES, citing NYSE_DOCUMENTS), and every year from 1993, SPY's first
# close, was checked day by day against SPY's stored closes in data/demo.db and the
# personal book before it went in. tests/test_market_holidays.py holds the committed
# books to it.
NYSE_HOLIDAY_YEARS = (1990, 2028)

# NYSE's own documents: (what and when, the URL, as the Internet Archive captured it
# where the page has gone). A document that lists several years counts for each.
NYSE_DOCUMENTS = {
    'holidays_1885_1995': (
        'NYSE, "List of Holidays, 1885-1995" (page dated 1996), captured 1996-11-12',
        'https://web.archive.org/web/19961112180744/http://www.nyse.com/public/about/hist/holday85.html'),
    'history_1885_1998': (
        'NYSE, "History of New York Stock Exchange Holidays, 1885-date" (hday1997.pdf, made 1998-07-10), captured 1998-12-05',
        'https://web.archive.org/web/19981205121746/http://www.nyse.com/public/market/2c/2c5/hday1997.pdf'),
    'holidays_1997': (
        'NYSE, "1997 Holidays", captured 1997-02-22',
        'https://web.archive.org/web/19970222214416/http://www.nyse.com/public/about/hol97.html'),
    'special_1999': (
        'NYSE, "NYSE Holidays and Special Closings", captured 1999-03-02',
        'https://web.archive.org/web/19990302075821/http://www.nyse.com/public/market/2c/2c5/2c5fm.htm'),
    'schedule_1999': (
        'NYSE, "NYSE Hours and Holiday Schedule" (1999-2001), captured 1999-11-28',
        'https://web.archive.org/web/19991128072223/http://www.nyse.com/about/holidayclosings.html'),
    'schedule_2000': (
        'NYSE, "NYSE Hours and Holiday Schedule" (2000-2004), captured 2000-06-17',
        'https://web.archive.org/web/20000617131814/http://www.nyse.com/about/holidayclosings.html'),
    'schedule_2001': (
        'NYSE, "NYSE Hours and Holiday Schedule" (2001-2004), captured 2001-08-15',
        'https://web.archive.org/web/20010815160228/http://www.nyse.com/about/holidayclosings.html'),
    'schedule_2002': (
        'NYSE, "NYSE Hours and Holiday Schedule" (2002-2004), captured 2002-12-14',
        'https://web.archive.org/web/20021214020233/http://www.nyse.com/about/holidayclosings.html'),
    'holidays_2003': (
        'NYSE, "Holiday Schedule" (2003-2004), captured 2003-04-25',
        'https://web.archive.org/web/20030425015823/http://www.nyse.com/about/1022963613686.html'),
    'holidays_2004': (
        'NYSE, "Holidays & Hours" (2004-2006), captured 2004-04-07',
        'https://web.archive.org/web/20040407093818/http://www.nyse.com/about/1022963613686.html'),
    'holidays_2005': (
        'NYSE, "Holidays & Hours" (2005-2006), captured 2005-10-23',
        'https://web.archive.org/web/20051023004214/http://www.nyse.com/about/1022963613686.html'),
    'tradeday_2004': (
        'NYSE, "2004 Holiday Schedule" (tradeday.pdf, modified 2004-12-22), captured 2005-01-28',
        'https://web.archive.org/web/20050128203904/http://www.nyse.com/pdfs/tradeday.pdf'),
    'tradeday_2006': (
        'NYSE, "2006 Holiday Schedule" (tradeday_06.pdf, made 2006-01-06), captured 2006-01-12',
        'https://web.archive.org/web/20060112043538/http://www.nyse.com/pdfs/tradeday_06.pdf'),
    'special_closings_2006': (
        'NYSE, "NYSE Special Closings, 1885-date" (closings.pdf, revised through June 2006), captured 2006-07-12',
        'https://web.archive.org/web/20060712172136/http://www.nyse.com/pdfs/closings.pdf'),
    'hours_2007a': (
        'NYSE, "Holidays and Hours" (2007), captured 2007-04-30',
        'https://web.archive.org/web/20070430231733/http://www.nyse.com/about/newsevents/1176373643795.html'),
    'hours_2007b': (
        'NYSE, "Holidays and Hours" (2007-2009), captured 2007-12-22',
        'https://web.archive.org/web/20071222074629/http://www.nyse.com/about/newsevents/1176373643795.html'),
    'hours_2010': (
        'NYSE, "Holidays and Hours" (2009-2011), captured 2010-02-08',
        'https://web.archive.org/web/20100208104805/http://www.nyse.com/about/newsevents/1176373643795.html'),
    'hours_2011': (
        'NYSE, "Holidays and Hours" (2011-2013), captured 2011-06-28',
        'https://web.archive.org/web/20110628080544/http://www.nyse.com/about/newsevents/1176373643795.html'),
    'nyx_2012': (
        'NYSE Euronext, "Holidays, Hours and Early Closures" (2012-2014), captured 2012-11-15',
        'https://web.archive.org/web/20121115214448/http://usequities.nyx.com/markets/holidays-and-hours/nyse'),
    'nyx_2014': (
        'NYSE Euronext, "Holidays, Hours and Early Closures" (2013-2014), captured 2014-01-05',
        'https://web.archive.org/web/20140105140301/http://usequities.nyx.com/markets/holidays-and-hours/nyse'),
    'calendars_2014': (
        'NYSE, "Holidays and Trading Hours" (2014-2015), captured 2014-11-24',
        'https://web.archive.org/web/20141124052340/https://www.nyse.com/markets/hours-calendars'),
    'calendars_2015': (
        'NYSE, "Holidays and Trading Hours" (2015-2016), captured 2015-03-30',
        'https://web.archive.org/web/20150330183404/https://www.nyse.com/markets/hours-calendars'),
    'calendars_2017': (
        'NYSE, "Holidays and Trading Hours" (2016-2017), captured 2017-01-02',
        'https://web.archive.org/web/20170102054744/https://www.nyse.com/markets/hours-calendars'),
    'calendars_2018': (
        'NYSE, "Holidays and Trading Hours" (2017-2019), captured 2018-01-01',
        'https://web.archive.org/web/20180101202300/https://www.nyse.com/markets/hours-calendars'),
    'calendars_2019': (
        'NYSE, "Holidays and Trading Hours" (2019-2021), captured 2019-01-07',
        'https://web.archive.org/web/20190107231452/https://www.nyse.com/markets/hours-calendars'),
    'calendars_2020': (
        'NYSE, "Holidays and Trading Hours" (2020-2022), captured 2020-01-22',
        'https://web.archive.org/web/20200122050320/https://www.nyse.com/markets/hours-calendars'),
    'calendars_2021': (
        'NYSE, "Holidays and Trading Hours" (2021-2023), captured 2021-01-11',
        'https://web.archive.org/web/20210111130129/https://www.nyse.com/markets/hours-calendars'),
    'calendars_2022': (
        'NYSE, "Holidays and Trading Hours" (2022-2024, Juneteenth included), captured 2022-01-05',
        'https://web.archive.org/web/20220105021504/https://www.nyse.com/markets/hours-calendars'),
    'calendars_2023': (
        'NYSE, "Holidays and Trading Hours" (2023-2025), captured 2023-01-14',
        'https://web.archive.org/web/20230114155111/https://www.nyse.com/markets/hours-calendars'),
    'calendars_2024': (
        'NYSE, "Holidays and Trading Hours" (2024-2026), captured 2024-01-11',
        'https://web.archive.org/web/20240111002222/https://www.nyse.com/markets/hours-calendars'),
    'calendar_2025_2027': (
        "NYSE Group's 2025-2027 holiday calendar, announced 2024-11-11",
        None),
    'calendars_2026': (
        'nyse.com/markets/hours-calendars (2026-2028), as it read on 2026-09-26',
        None),
}

# Each year's documents (#644). 2022 cites only the 2022 capture: the two before it were
# made before Juneteenth was added and lack 2022-06-20.
NYSE_CALENDAR_SOURCES = {
    **{y: ("holidays_1885_1995", "history_1885_1998") for y in range(1990, 1996)},
    1996: ("history_1885_1998",),
    1997: ("holidays_1997", "history_1885_1998"),
    1998: ("history_1885_1998",),
    1999: ("special_1999", "schedule_1999"),
    2000: ("schedule_1999", "schedule_2000"),
    2001: ("schedule_2001",),
    2002: ("schedule_2002",),
    2003: ("schedule_2002", "holidays_2003"),
    2004: ("holidays_2004", "tradeday_2004"),
    2005: ("holidays_2004", "holidays_2005"),
    2006: ("holidays_2005", "tradeday_2006"),
    2007: ("hours_2007a", "hours_2007b"),
    2008: ("hours_2007b",),
    2009: ("hours_2007b", "hours_2010"),
    2010: ("hours_2010",),
    2011: ("hours_2010", "hours_2011"),
    2012: ("hours_2011", "nyx_2012"),
    2013: ("hours_2011", "nyx_2012", "nyx_2014"),
    2014: ("nyx_2012", "nyx_2014", "calendars_2014"),
    2015: ("calendars_2014", "calendars_2015"),
    2016: ("calendars_2015", "calendars_2017"),
    2017: ("calendars_2017", "calendars_2018"),
    2018: ("calendars_2018",),
    2019: ("calendars_2018", "calendars_2019"),
    2020: ("calendars_2019", "calendars_2020"),
    2021: ("calendars_2019", "calendars_2020", "calendars_2021"),
    2022: ("calendars_2022",),
    2023: ("calendars_2022", "calendars_2023"),
    2024: ("calendars_2022", "calendars_2023", "calendars_2024"),
    2025: ("calendars_2023", "calendars_2024", "calendar_2025_2027"),
    2026: ("calendars_2024", "calendar_2025_2027", "calendars_2026"),
    2027: ("calendar_2025_2027", "calendars_2026"),
    2028: ("calendars_2026",),
}

# The scheduled closures by year, month-day (observed dates where a holiday falls on a
# weekend). No New Year's closure in 1994, 2000, 2005, 2011, 2022 or 2028, when January 1
# is a Saturday and the Friday before was a session; Martin Luther King Jr. Day from
# 1998; Juneteenth from 2022.
_SCHEDULED_BY_YEAR = {
    1990: "01-01 02-19 04-13 05-28 07-04 09-03 11-22 12-25",
    1991: "01-01 02-18 03-29 05-27 07-04 09-02 11-28 12-25",
    1992: "01-01 02-17 04-17 05-25 07-03 09-07 11-26 12-25",
    1993: "01-01 02-15 04-09 05-31 07-05 09-06 11-25 12-24",
    1994: "02-21 04-01 05-30 07-04 09-05 11-24 12-26",
    1995: "01-02 02-20 04-14 05-29 07-04 09-04 11-23 12-25",
    1996: "01-01 02-19 04-05 05-27 07-04 09-02 11-28 12-25",
    1997: "01-01 02-17 03-28 05-26 07-04 09-01 11-27 12-25",
    1998: "01-01 01-19 02-16 04-10 05-25 07-03 09-07 11-26 12-25",
    1999: "01-01 01-18 02-15 04-02 05-31 07-05 09-06 11-25 12-24",
    2000: "01-17 02-21 04-21 05-29 07-04 09-04 11-23 12-25",
    2001: "01-01 01-15 02-19 04-13 05-28 07-04 09-03 11-22 12-25",
    2002: "01-01 01-21 02-18 03-29 05-27 07-04 09-02 11-28 12-25",
    2003: "01-01 01-20 02-17 04-18 05-26 07-04 09-01 11-27 12-25",
    2004: "01-01 01-19 02-16 04-09 05-31 07-05 09-06 11-25 12-24",
    2005: "01-17 02-21 03-25 05-30 07-04 09-05 11-24 12-26",
    2006: "01-02 01-16 02-20 04-14 05-29 07-04 09-04 11-23 12-25",
    2007: "01-01 01-15 02-19 04-06 05-28 07-04 09-03 11-22 12-25",
    2008: "01-01 01-21 02-18 03-21 05-26 07-04 09-01 11-27 12-25",
    2009: "01-01 01-19 02-16 04-10 05-25 07-03 09-07 11-26 12-25",
    2010: "01-01 01-18 02-15 04-02 05-31 07-05 09-06 11-25 12-24",
    2011: "01-17 02-21 04-22 05-30 07-04 09-05 11-24 12-26",
    2012: "01-02 01-16 02-20 04-06 05-28 07-04 09-03 11-22 12-25",
    2013: "01-01 01-21 02-18 03-29 05-27 07-04 09-02 11-28 12-25",
    2014: "01-01 01-20 02-17 04-18 05-26 07-04 09-01 11-27 12-25",
    2015: "01-01 01-19 02-16 04-03 05-25 07-03 09-07 11-26 12-25",
    2016: "01-01 01-18 02-15 03-25 05-30 07-04 09-05 11-24 12-26",
    2017: "01-02 01-16 02-20 04-14 05-29 07-04 09-04 11-23 12-25",
    2018: "01-01 01-15 02-19 03-30 05-28 07-04 09-03 11-22 12-25",
    2019: "01-01 01-21 02-18 04-19 05-27 07-04 09-02 11-28 12-25",
    2020: "01-01 01-20 02-17 04-10 05-25 07-03 09-07 11-26 12-25",
    2021: "01-01 01-18 02-15 04-02 05-31 07-05 09-06 11-25 12-24",
    2022: "01-17 02-21 04-15 05-30 06-20 07-04 09-05 11-24 12-26",
    2023: "01-02 01-16 02-20 04-07 05-29 06-19 07-04 09-04 11-23 12-25",
    2024: "01-01 01-15 02-19 03-29 05-27 06-19 07-04 09-02 11-28 12-25",
    2025: "01-01 01-20 02-17 04-18 05-26 06-19 07-04 09-01 11-27 12-25",
    2026: "01-01 01-19 02-16 04-03 05-25 06-19 07-03 09-07 11-26 12-25",
    2027: "01-01 01-18 02-15 03-26 05-31 06-18 07-05 09-06 11-25 12-24",
    2028: "01-17 02-21 04-14 05-29 06-19 07-04 09-04 11-23 12-25",
}
_SCHEDULED_CLOSURES = frozenset(date.fromisoformat(f"{y}-{md}")
                                for y, mds in _SCHEDULED_BY_YEAR.items() for md in mds.split())

# Closures no calendar schedules, each entered by hand from NYSE's notice or record:
# (why, the document, its URL). Nothing falls back when an expected session never
# trades: a lock waits on the close of its quarter's last session until the closure is
# entered here (#466).
NYSE_UNSCHEDULED_NOTICES = {
    date(1994, 4, 27): ("President Nixon's funeral",
        'NYSE, "NYSE Special Closings - Funerals of U.S. Presidents" (presidents_closings.pdf, 2004-06-07); and special_closings_2006',
        'https://web.archive.org/web/20040609214215/http://www.nyse.com/pdfs/presidents_closings.pdf'),
    date(2001, 9, 11): ('closed following the September 11 attack',
        "NYSE's home-page notices of 2001-09-12 and 2001-09-14; and special_closings_2006",
        'https://web.archive.org/web/20010913000417/http://www.nyse.com/'),
    date(2001, 9, 12): ('closed following the September 11 attack',
        'as 2001-09-11',
        None),
    date(2001, 9, 13): ('closed following the September 11 attack',
        'as 2001-09-11',
        None),
    date(2001, 9, 14): ('closed following the September 11 attack',
        'as 2001-09-11, and the notice that trading resumes on 2001-09-17',
        'https://web.archive.org/web/20010914220538/http://www.nyse.com/'),
    date(2004, 6, 11): ("President Reagan's national day of mourning",
        'NYSE, "NYSE to Close on Friday, June 11 to Observe National Day of Mourning for President Ronald Reagan", 2004-06-07',
        'https://web.archive.org/web/20040609090925/http://www.nyse.com/press/1086606031441.html'),
    date(2007, 1, 2): ("President Ford's national day of mourning",
        'NYSE Group, "NYSE Group, Inc. Markets to Close on Tuesday, Jan. 2, 2007", 2006-12-29',
        'https://web.archive.org/web/20080122222428/http://www.nyse.com/press/1167219778099.html'),
    date(2012, 10, 29): ('Hurricane Sandy',
        'NYSE Euronext, "NYSE Euronext Statement on Closure of U.S. Markets on Tuesday, Oct. 30, 2012", 2012-10-29, which follows Monday\'s closure',
        'https://web.archive.org/web/20121030024626/http://www.nyx.com/sandy'),
    date(2012, 10, 30): ('Hurricane Sandy',
        'as 2012-10-29',
        None),
    date(2018, 12, 5): ("President George H. W. Bush's national day of mourning",
        'ICE, "New York Stock Exchange to Honor President George H. W. Bush", 2018-12-01',
        'https://web.archive.org/web/20250108195503/https://ir.theice.com/press/news-details/2018/New-York-Stock-Exchange-to-Honor-President-George-H-W-Bush/default.aspx'),
    date(2025, 1, 9): ("President Carter's national day of mourning",
        "NYSE Group's notice of December 30, 2024",
        None),
}
NYSE_UNSCHEDULED_CLOSURES = frozenset(NYSE_UNSCHEDULED_NOTICES)
NYSE_HOLIDAYS = _SCHEDULED_CLOSURES | NYSE_UNSCHEDULED_CLOSURES


def holiday_table_refusal(today: "date | None" = None) -> "str | None":
    """Why the quarterly close-out may not run, or None: the NYSE holiday table must
    reach the end of next year (#466, decided by the owner on September 28, 2026).

    A lock waits on the close of its quarter's last NYSE session
    (cache.last_session_on_or_before), and past the table every weekday counts as a
    session. So a closure the table does not hold is a session that never trades, and
    a quarter ending there would wait indefinitely: March 31, 2029 is a Saturday and the
    Friday before it is Good Friday. ``today`` is New York's date by default."""
    from src.asof import today_et
    needed = (today or today_et()).year + 1
    last = NYSE_HOLIDAY_YEARS[1]
    if last >= needed:
        return None
    return (f"the NYSE holiday table in src/demo_refresh.py ends with {last}, and the "
            f"close-out needs it through December 31, {needed}. Extend it from "
            f"nyse.com/markets/hours-calendars, and enter any unscheduled closure by "
            f"hand from NYSE's notice.")


def is_session(d: date) -> bool:
    """Whether New York holds a session on ``d``: a weekday that is not an NYSE
    holiday (NYSE_HOLIDAYS). The banner's freshness count (asof.as_of_live_line) uses
    the same calendar. Before the table, a market holiday counted as a session, so the
    day after one read a current frontier as a close behind (2026-09-25 audit, item 12).
    """
    return d.weekday() < 5 and d not in NYSE_HOLIDAYS


def next_close_after(t: datetime) -> datetime:
    """The next New York session's close (plus the publish margin) after ``t``, in
    UTC. Holidays in NYSE_HOLIDAYS are skipped."""
    ny = t.astimezone(_NY)
    c = ny.replace(hour=16, minute=0, second=0, microsecond=0) + _PUBLISH_MARGIN
    if c <= ny:
        c += timedelta(days=1)
    while not is_session(c.date()):
        c += timedelta(days=1)
    return c.astimezone(timezone.utc)


def _frontier_tickers() -> "set[str]":
    """The tickers the banner's frontier is taken over: the portfolio's holdings, with
    SPAXX priced off BIL, as committed_price_frontier reads them."""
    from src.holdings import get_holdings_on_date, get_portfolio_account_id
    try:
        held = get_holdings_on_date(date.today().isoformat(),
                                    account_id=get_portfolio_account_id())
    except Exception:                                            # noqa: BLE001
        return set()
    return {"BIL" if t == "SPAXX" else t for t in held.index}


def _fetch_one(ticker: str, last: str, today: str) -> "Optional[tuple[str, str]]":
    """Fetch from the last stored date (inclusive, so the window always holds a
    bar) through today. None on success, else (ticker, reason)."""
    from src import prices
    try:
        prices.fetch_prices(ticker, last, today)
        return None
    except Exception as exc:                                     # noqa: BLE001
        return ticker, f"{type(exc).__name__}: {exc}"[:160]


def _attempt(now: datetime) -> RefreshState:
    import src.db as db
    from src import prices
    from src.holdings import committed_price_frontier

    today = now.astimezone(_NY).date().isoformat()
    with db.get_connection() as conn:
        tickers = conn.execute(
            "SELECT ticker, MAX(price_date) FROM prices GROUP BY ticker").fetchall()
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        results = list(pool.map(lambda r: _fetch_one(r[0], r[1], today), tickers))
    failed = tuple(sorted(r for r in results if r is not None))

    # Then ask again for every close the cache lacks inside a ticker's history (#501).
    # Each fetch above began at its ticker's last stored date, so a session the
    # provider left out of an earlier response was never asked for again. What it
    # still lacks stays named (state().gaps, and cache.quarter_lock_gaps for a ticker
    # a lock covers) and is asked for at the next attempt. It does not make the fetch
    # a failure: the later closes arrived, and the banner's date is theirs.
    from src import price_gaps
    gaps = price_gaps.reask().left if price_gaps.enabled() else ()

    # A failure the banner must report: every ticker failed (the provider is out of
    # reach), or one the frontier is taken over did. A lone non-holding ticker that
    # failed is recorded and retried at the next close, without calling the whole
    # fetch failed.
    frontier_set = _frontier_tickers()
    failed_names = {t for t, _ in failed}
    ok = not (failed and (len(failed) == len(tickers) or failed_names & frontier_set))

    before = db.runtime_meta("last_fetch_success")
    served = committed_price_frontier(today)
    if ok:
        db.set_runtime_meta("last_fetch_success", now.isoformat(timespec="minutes"))
    return RefreshState(
        status="fetched" if ok else "failed",
        attempted_at=now,
        next_attempt_at=next_close_after(now) if ok else now + RETRY_AFTER,
        served_through=served,
        fetched_before=before,
        failed=failed,
        gaps=gaps,
    )


def refresh_if_due(now: Optional[datetime] = None) -> Optional[RefreshState]:
    """Fetch if the last attempt's wait has passed; return the current state.

    Once running, the price layer's own gap fills are off for the rest of the process:
    this is the only thing that fetches on a schedule."""
    global _STATE
    if not enabled():
        return _STATE
    import src.db as db
    from src import prices
    db.after_reseed(_forget_last_attempt)
    now = now or datetime.now(timezone.utc)
    with _LOCK:
        prices.set_gap_fetch(False)
        if _STATE is not None and now < _STATE.next_attempt_at:
            return _STATE
        _STATE = _attempt(now)
        return _STATE
