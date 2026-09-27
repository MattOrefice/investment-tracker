"""FRED macro data fetcher with 24-hour SQLite cache."""
import json
import math
import os
import sys
import threading
import time
from datetime import date, datetime, timezone
from typing import NamedTuple, Optional

import pandas as pd

from src.config import FRED_API_KEY as _FRED_KEY
from src.db import get_connection

_FRED_RETRY_DELAYS = (1, 3, 9)  # seconds between retry attempts (exponential backoff)


class FREDFetchError(Exception):
    """Raised when all FRED API retry attempts are exhausted."""
    def __init__(self, series_id: str, cause: Exception):
        self.series_id = series_id
        self.cause = cause
        super().__init__(
            f"FRED series '{series_id}' fetch failed after {len(_FRED_RETRY_DELAYS) + 1} attempts: {cause}"
        )


class FREDRetryWait(Exception):
    """A series whose last fetch failed, asked for again before its retry time.

    Raised by get_series on the demo's fetch timer instead of fetching: nothing was
    requested from FRED. The first failure raises it too, so every panel reports the
    failure the same way, with the time it retries."""
    def __init__(self, series_id: str, failed_at: datetime, retry_at: datetime, reason: str):
        from src.asof import _when
        self.series_id = series_id
        self.failed_at = failed_at
        self.retry_at = retry_at
        self.reason = reason
        super().__init__(
            f"FRED fetch for '{series_id}' failed {_when(failed_at)} ({reason}); "
            f"it retries after {_when(retry_at)}."
        )


# The demo's retry timer, applied to FRED (#368 item 3's rule for prices): after a
# series fails, get_series does not fetch it again until demo_refresh.RETRY_AFTER has
# passed, so a failure is retried on a timer, never on every page render. Per series,
# so one failing series never holds back the others. series_id -> (failed_at, reason).
_FAILED: "dict[str, tuple[datetime, str]]" = {}
_FAILED_LOCK = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)

_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS macro_cache (
    series_id  TEXT NOT NULL,
    fetch_date TEXT NOT NULL,
    data       TEXT NOT NULL,
    PRIMARY KEY (series_id, fetch_date)
)
"""


def _ensure_cache_table() -> None:
    with get_connection() as conn:
        conn.execute(_CACHE_DDL)


def _get_fred():
    from fredapi import Fred
    if not _FRED_KEY:
        raise RuntimeError("FRED_API_KEY not set. Add it to .env (local) or Streamlit secrets (cloud).")
    return Fred(api_key=_FRED_KEY)


def fetch_fred_series(
    series_id: str,
    start_date: str,
    end_date: Optional[str] = None,
) -> pd.Series:
    """Fetch directly from FRED API with exponential backoff retry. Does not touch cache."""
    end  = end_date or date.today().isoformat()
    fred = _get_fred()
    last_exc: Exception = RuntimeError("no attempts made")
    delays = list(_FRED_RETRY_DELAYS)
    for attempt in range(len(delays) + 1):
        try:
            return fred.get_series(series_id, observation_start=start_date, observation_end=end)
        except Exception as exc:
            last_exc = exc
            print(
                f"[INFO] FRED fetch attempt {attempt + 1}/{len(delays) + 1} "
                f"for '{series_id}' failed: {exc}",
                file=sys.stderr,
            )
            if attempt < len(delays):
                time.sleep(delays[attempt])
    raise FREDFetchError(series_id, last_exc)


def get_series(series_id: str, start_date: str = "1990-01-01") -> pd.Series:
    """
    Return series from 24h SQLite cache; fetches fresh from FRED if stale or
    if the cached window doesn't cover the requested start_date.

    On the demo's fetch timer (demo_refresh.enabled()), a failed fetch raises
    FREDRetryWait, and so does every call for that series until
    demo_refresh.RETRY_AFTER has passed, without fetching. Off it (personal mode, the
    suite), a failure raises as it always did and the next call fetches again.
    """
    _ensure_cache_table()
    today = date.today().isoformat()
    cached = _read_cached(series_id, start_date, today)
    if cached is not None:
        return cached
    # One fetch per series at a time (audit item 15f): a page that asks for a series the
    # startup warm is fetching waits for that fetch and reads its row, rather than
    # fetching it a second time.
    with _series_lock(series_id):
        cached = _read_cached(series_id, start_date, today)
        if cached is not None:
            return cached
        return _fetch_and_store(series_id, start_date, today)


def _read_cached(series_id: str, start_date: str, today: str) -> "pd.Series | None":
    """Today's cached row for the series, if it covers ``start_date``; else None."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT data FROM macro_cache WHERE series_id = ? AND fetch_date = ?",
            (series_id, today),
        ).fetchone()

    if row:
        payload = json.loads(row["data"])
        s = pd.Series(
            [float(v) if v is not None else float("nan") for v in payload["values"]],
            index=pd.to_datetime(payload["dates"]),
            name=series_id,
        )
        # Only use the cache if it covers the requested start date. A row records the
        # start it was fetched from, and covers any request from that date on: its first
        # observation is the first FRED has on or after it. Judged by the first
        # observation alone, a series that starts after the requested date (DGS10 from
        # 1990-01-02 for 1990-01-01, the ICE BofA spreads from 2023 for 1996) never
        # matched, and 17 of the Macro page's 22 series were fetched again on every
        # uncached call (#377). Rows written before the start was recorded fall back to
        # the first-observation check.
        fetched_from = payload.get("requested_start")
        if fetched_from is not None:
            covers = fetched_from <= start_date
        else:
            cached_min = s.dropna().index.min()
            covers = not s.dropna().empty and str(cached_min.date()) <= start_date
        if covers:
            return s[s.index >= start_date]
        # Cache hit but coverage is insufficient — re-fetch and overwrite
    return None


_SERIES_LOCKS: "dict[str, threading.Lock]" = {}
_SERIES_LOCKS_GUARD = threading.Lock()


def _series_lock(series_id: str) -> threading.Lock:
    with _SERIES_LOCKS_GUARD:
        return _SERIES_LOCKS.setdefault(series_id, threading.Lock())


def _retry_wait(series_id: str) -> "FREDRetryWait | None":
    """The wait a failed series is in while its retry time has not come; else None."""
    from src import demo_refresh
    with _FAILED_LOCK:
        failed = _FAILED.get(series_id)
    if failed is None:
        return None
    retry_at = failed[0] + demo_refresh.RETRY_AFTER
    if _now() < retry_at:
        return FREDRetryWait(series_id, failed[0], retry_at, failed[1])
    return None


def _fetch_and_store(series_id: str, start_date: str, today: str) -> pd.Series:
    """Fetch from FRED and write today's row; the demo's retry timer applies."""
    from src import demo_refresh
    timed = demo_refresh.enabled()
    if timed:
        wait = _retry_wait(series_id)
        if wait is not None:
            raise wait
    try:
        raw = fetch_fred_series(series_id, start_date)
    except Exception as exc:
        if not timed:
            raise
        failed_at = _now()
        reason = f"{type(exc).__name__}: {exc}"[:160]
        with _FAILED_LOCK:
            _FAILED[series_id] = (failed_at, reason)
        raise FREDRetryWait(series_id, failed_at,
                            failed_at + demo_refresh.RETRY_AFTER, reason) from exc
    payload = {
        "dates":  [str(d.date()) for d in raw.index],
        "values": [float(v) if pd.notna(v) else None for v in raw.values],
        "requested_start": start_date,
    }
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO macro_cache (series_id, fetch_date, data) VALUES (?, ?, ?)",
            (series_id, today, json.dumps(payload)),
        )
    return raw


# Every FRED series the Macro page reads, with the start it requests
# (pages/3_Macro.py's _try_fred calls; tests/test_fred_warm.py holds the two equal).
# The startup warm fetches exactly these, so it fills the rows the page will read.
# DGS3MO is also the risk-free rate's series, requested from the same start.
MACRO_PAGE_SERIES: "tuple[tuple[str, str], ...]" = (
    ("USREC", "1945-01-01"),
    ("T10Y2Y", "1976-06-01"),
    ("DFF", "1954-07-01"),
    ("BAMLH0A0HYM2", "1996-12-31"),
    ("BAMLC0A0CM", "1996-01-01"),
    ("BAMLH0A3HYC", "1996-01-01"),
    ("DGS10", "1990-01-01"),
    ("DGS3MO", "1990-01-01"),
    ("DGS2", "1990-01-01"),
    ("DGS1", "1990-01-01"),
    ("DGS5", "1990-01-01"),
    ("DGS7", "1990-01-01"),
    ("DGS20", "1990-01-01"),
    ("DGS30", "1990-01-01"),
    ("T10YIE", "2003-01-01"),
    ("DFII10", "2003-01-01"),
    ("UNRATE", "1948-01-01"),
    ("A191RL1Q225SBEA", "1947-01-01"),
    ("CPILFESL", "1957-01-01"),
    ("CFNAIDIFF", "1967-01-01"),
    ("DTWEXBGS", "2006-01-01"),
    ("NFCI", "1971-01-01"),
)

# How long the Macro page waits for today's FRED rows before it renders the stored
# copies with the date each was fetched. One visit in #429's measurements took 184 s,
# cause unrecorded; past this wait the page renders and the warm carries on.
PAGE_WAIT_SECONDS = 15.0

_WARM_GUARD = threading.Lock()
_WARM_THREAD: "threading.Thread | None" = None
_WARM_DAY: "str | None" = None
# Notified each time the warm finishes a series, fetched or failed, so a page waiting
# for one wakes as soon as it resolves.
_WARM_PROGRESS = threading.Condition()


def warm_enabled() -> bool:
    """Whether the warm runs: on the demo's fetch timer (demo_refresh.enabled()),
    unless DEMO_FRED_WARM=0. tests/conftest.py sets that, so no test starts the
    thread unless it means to; one that did left it running into later tests, where
    it raced their migrations."""
    from src import demo_refresh
    return demo_refresh.enabled() and os.environ.get("DEMO_FRED_WARM", "1") != "0"


def warm_cache_in_background(*, again: bool = False) -> "threading.Thread | None":
    """Fetch the Macro page's series into today's cache on a background thread (audit
    item 15f). A fresh container's first Macro visit waited about 15 seconds for 22
    fetches; warmed at startup, it reads cached rows, and a visit that arrives
    mid-warm waits only for the series still in flight (get_series takes one fetch per
    series at a time).

    The router calls this on every run and it starts one warm a day. The Macro page
    calls it with ``again=True`` when it lacks a row, which starts one unless one is
    running. Returns the thread, or None when none was started (one is running, today's
    has run, or warm_enabled() is False: personal mode, the suite)."""
    global _WARM_THREAD, _WARM_DAY
    if not warm_enabled():
        return None
    today = date.today().isoformat()
    with _WARM_GUARD:
        if _WARM_THREAD is not None and _WARM_THREAD.is_alive():
            return None
        if not again and _WARM_DAY == today:
            return None
        _WARM_DAY = today
        _WARM_THREAD = threading.Thread(target=_warm, name="fred-warm", daemon=True)
        _WARM_THREAD.start()
        return _WARM_THREAD


def _warm_running() -> bool:
    thread = _WARM_THREAD
    return thread is not None and thread.is_alive()


def _warm() -> None:
    for series_id, start in MACRO_PAGE_SERIES:
        try:
            get_series(series_id, start)
        except Exception as exc:                          # noqa: BLE001
            # The page reports its own failures when it reads the series.
            print(f"[INFO] FRED warm: {series_id} not cached ({type(exc).__name__})",
                  file=sys.stderr)
        finally:
            with _WARM_PROGRESS:
                _WARM_PROGRESS.notify_all()


class FREDStillFetching(Exception):
    """Today's row for a series did not arrive within the page's wait
    (PAGE_WAIT_SECONDS). ``stored`` is the newest stored copy that reaches back to the
    requested start and ``stored_on`` the date it was fetched, both None when nothing
    is stored. The warm keeps fetching, so a later visit reads today's row."""
    def __init__(self, series_id: str, stored: "pd.Series | None", stored_on: "str | None"):
        self.series_id = series_id
        self.stored = stored
        self.stored_on = stored_on
        if stored_on:
            detail = f"showing the copy fetched on {stored_on}"
        else:
            detail = "no earlier copy is stored"
        super().__init__(f"FRED has not returned '{series_id}' within "
                         f"{PAGE_WAIT_SECONDS:.0f} seconds; {detail}. The fetch "
                         "continues in the background.")


def get_series_for_page(series_id: str, start_date: str, deadline: float) -> pd.Series:
    """get_series for a page that must not wait on FRED past ``deadline``, a
    time.monotonic() value.

    While the warm runs (warm_enabled()) the page never fetches. It reads today's row,
    raises the series' retry wait as get_series would, or waits for the warm, starting
    one if none is running. At the deadline it raises FREDStillFetching with the stored
    copy, and the warm carries on. With the warm off (personal mode, the suite) this is
    get_series."""
    if not warm_enabled():
        return get_series(series_id, start_date)
    _ensure_cache_table()
    started = False
    while True:
        # Read BEFORE the row: a warm seen finished here has written all it will.
        running = _warm_running()
        cached = _read_cached(series_id, start_date, date.today().isoformat())
        if cached is not None:
            return cached
        wait = _retry_wait(series_id)
        if wait is not None:
            raise wait
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if not running:
            if started:
                break                  # it ran without resolving this series
            warm_cache_in_background(again=True)
            started = True
            continue
        with _WARM_PROGRESS:
            _WARM_PROGRESS.wait(min(remaining, 0.5))
    stored, stored_on = _read_stored(series_id, start_date)
    raise FREDStillFetching(series_id, stored, stored_on)


def _read_stored(series_id: str, start_date: str) -> "tuple[pd.Series | None, str | None]":
    """The newest stored row for the series that reaches back to ``start_date``, sliced
    from it, with the date it was fetched; (None, None) when there is none.

    A row that records its requested start reaches back when it was requested from
    ``start_date`` or earlier. An older row, without that record, is judged by its first
    observation: it reaches back when no other stored row of the series starts earlier.
    Other callers request some series from later starts (the PDF asks for T10Y2Y from
    1990, the page from 1976), and their rows must not shorten a panel's history."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT fetch_date, data FROM macro_cache WHERE series_id = ?",
            (series_id,)).fetchall()
    parsed = []
    for row in rows:
        payload = json.loads(row["data"])
        s = pd.Series(
            [float(v) if v is not None else float("nan") for v in payload["values"]],
            index=pd.to_datetime(payload["dates"]), name=series_id)
        valid = s.dropna()
        if valid.empty:
            continue
        parsed.append((row["fetch_date"], payload.get("requested_start"), valid.index[0], s))
    if not parsed:
        return None, None
    earliest = min(first for _, _, first, _ in parsed)
    reaching = [(fetched, s) for fetched, requested, first, s in parsed
                if (requested <= start_date if requested is not None else first == earliest)]
    if not reaching:
        return None, None
    fetched, s = max(reaching, key=lambda r: r[0])
    return s[s.index >= start_date], fetched


def clear_macro_cache() -> int:
    """Delete all macro_cache rows. Returns count of rows deleted."""
    _ensure_cache_table()
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM macro_cache")
        return cur.rowcount


def get_recession_periods(start_date: str, end_date: str) -> list:
    """
    Convert USREC monthly indicator into (start, end) date tuples for chart shading.
    USREC = 1 during NBER-dated recessions, 0 otherwise.
    """
    return recession_periods(get_series("USREC", start_date="1945-01-01"),
                             start_date, end_date)


def recession_periods(usrec: pd.Series, start_date: str, end_date: str) -> list:
    """get_recession_periods from a USREC series the caller already holds: the Macro
    page loads USREC within its wait and must not fetch it a second time without one."""
    window = usrec.loc[start_date:end_date].dropna()

    periods: list = []
    in_rec = False
    rec_start = None

    for dt, val in window.items():
        if val == 1 and not in_rec:
            in_rec = True
            rec_start = dt.date()
        elif val == 0 and in_rec:
            in_rec = False
            periods.append((rec_start, dt.date()))

    if in_rec and rec_start is not None:
        periods.append((rec_start, date.fromisoformat(end_date)))

    return periods


MAX_WINDOW_SENTINEL = "1800-01-01"


def percentile(series: pd.Series, current_value: float) -> float | None:
    """Percentile rank of current_value in the series (0–100), or None if there is none.

    RETURNS None ON AN EMPTY SERIES. It used to return 50.0 — the median rank, which is
    the single most plausible-looking wrong answer: it reads as a real measurement, sits
    in the middle of every band a consumer might test, and is indistinguishable from a
    genuine mid-range reading. Measured consequence in the PDF: a fabricated 50 maps to a
    "Moderate" allocation stance (#244). There is no percentile of no observations.

    Callers that cannot receive an empty series are unaffected; those that can must handle
    None. See window_pctile for the case where the number is real but its SCOPE was not
    what the label claimed — a different defect with the opposite disposition.
    """
    clean = series.dropna()
    if clean.empty:
        return None
    return float((clean <= current_value).mean() * 100)


class WindowedPctile(NamedTuple):
    """A windowed percentile with a record of what actually produced it.

    ``fell_back`` is the disclosure this type exists for: the requested window held no
    observations, so ``value`` is the FULL-series percentile rendered under a window label
    that overstates its scope. The number is real — it is a true percentile of real data —
    which is why the fix is to declare the window rather than to refuse the value.

    ``n`` is the count actually used (windowed when in-window, full when fallen back), so a
    caller can say "over N observations" truthfully in BOTH cases. Reporting the series
    length instead would be wrong on every normal load.

    The Max sentinel is NOT a fallback: '1800-01-01' means "use everything" by design, so
    "fell back" and "did what was asked" coincide and there is nothing to disclose.
    Flagging it would fire on every render of the two panels that use it and train the
    reader to ignore the marker.
    """

    value: float | None
    n: int
    fell_back: bool


def window_pctile(series: pd.Series, current_value: float,
                  w_start: str) -> WindowedPctile:
    """Percentile of current_value within series windowed from w_start onward.

    Falls back to the full series when the windowed slice is empty (the series ends before
    w_start), and SAYS SO via the returned ``fell_back``. Before that flag existed the
    caller received a bare float and rendered it under the reader's selected window label:
    pick 10Y, get the full-history number, no marker. Measured on the unemployment panel
    with a series ending 2015 — "33rd percentile of the 10Y window, near the historical
    median for this window" over a window containing zero observations.
    """
    if w_start == MAX_WINDOW_SENTINEL:
        windowed = series.dropna()
        fell_back = False
    else:
        windowed = series.loc[w_start:].dropna()
        fell_back = windowed.empty
        if fell_back:
            windowed = series.dropna()
    return WindowedPctile(value=percentile(windowed, current_value),
                          n=int(len(windowed)),
                          fell_back=fell_back)


_REGIME_LABELS = ("Recession", "Early-cycle", "Mid-cycle", "Late-cycle")

# The three signals, in the order coverage is reported.
_REGIME_SIGNALS = ("usrec", "t10y2y", "unrate")

# Heuristic branches need at least this many present signals. Recession is exempt: it
# reads USREC alone and one signal is COMPLETE there, not partial — NBER's indicator is
# definitionally the answer to "is it a recession", not evidence toward it.
_HEURISTIC_MIN_SIGNALS = 2


class RegimeVerdict(NamedTuple):
    """A regime label with a record of what it rests on.

    ``label`` is one of ``_REGIME_LABELS``, or **None** when the signals present cannot
    support a classification. None is a value to inspect, not an error: app.py imports
    pages unwrapped and fifteen other panels on the Macro page degrade rather than crash,
    so a raise here would take the page down for one badge.

    ``present``/``missing`` partition ``_REGIME_SIGNALS`` so a caller can say "from 2 of
    3 signals — T10Y2Y unavailable" without recomputing what it supplied. That is worth
    rendering even when nothing is missing: it is strictly more than a bare label.
    """

    label: str | None
    present: tuple[str, ...]
    missing: tuple[str, ...]


# The classifier's thresholds, named once so the verdict and its explanation read the
# same numbers (audit item 5). Rationale in docs/regime_classifier.md.
REGIME_UNRATE_EARLY: float = 5.5    # UNRATE above: Early-cycle (with the curve not inverted)
REGIME_UNRATE_TIGHT: float = 4.2    # UNRATE below: the tight-labor Late-cycle trigger
REGIME_CURVE_TRIGGER: float = -0.25  # T10Y2Y below, in percent: the inverted-curve trigger


def classify_regime(
    usrec: float | None,
    t10y2y: float | None,
    unrate: float | None,
) -> RegimeVerdict:
    """
    Classify the macro regime given three FRED indicator values.

    Rules applied in priority order (first match wins):
      1. Recession   — USREC = 1
      2. Early-cycle — USREC = 0, UNRATE > 5.5%, T10Y2Y > -0.25
      3. Late-cycle  — USREC = 0, T10Y2Y < -0.25 OR UNRATE < 4.2
      4. Mid-cycle   — default

    Missing signals (None) are treated as neutral WITHIN a rule, but NEUTRALITY HAS A
    FLOOR: below it, "neutral" stops being a modest assumption and becomes the entire
    basis of the answer. Because Mid-cycle is the default branch, without a floor an
    empty argument list returns a confident mid-cycle verdict — and pages/3_Macro.py
    rendered exactly that as a coloured badge with interpretive prose while every FRED
    series was unavailable.

    The floor is PER-BRANCH because the branches differ in kind. Recession reads USREC
    alone and that is complete. The other three are heuristic combinations needing
    ``_HEURISTIC_MIN_SIGNALS`` present; the deciding case is ``curve_ok``, which defaults
    True when t10y2y is None — so an absent curve actively supplies half the Early-cycle
    test, and a lone UNRATE reading would otherwise decide it. A missing signal there
    VOTES rather than abstains, which is why a flat "at least one present" floor does not
    catch it.

    Returns a RegimeVerdict; ``label`` is one of _REGIME_LABELS or None.
    See docs/regime_classifier.md for rationale and limitations.
    """
    supplied = {"usrec": usrec, "t10y2y": t10y2y, "unrate": unrate}
    present  = tuple(n for n in _REGIME_SIGNALS if supplied[n] is not None)
    missing  = tuple(n for n in _REGIME_SIGNALS if supplied[n] is None)

    def _verdict(label: str | None) -> RegimeVerdict:
        return RegimeVerdict(label=label, present=present, missing=missing)

    if usrec is not None and usrec >= 0.5:
        return _verdict("Recession")

    if len(present) < _HEURISTIC_MIN_SIGNALS:
        return _verdict(None)

    unrate_high  = unrate is not None and unrate > REGIME_UNRATE_EARLY
    curve_ok     = t10y2y is None or t10y2y > REGIME_CURVE_TRIGGER
    if unrate_high and curve_ok:
        return _verdict("Early-cycle")

    curve_inv    = t10y2y is not None and t10y2y < REGIME_CURVE_TRIGGER
    labor_tight  = unrate is not None and unrate < REGIME_UNRATE_TIGHT
    if curve_inv or labor_tight:
        return _verdict("Late-cycle")

    return _verdict("Mid-cycle")


def regime_explanation(label: str | None, t10y2y: float | None,
                       unrate: float | None) -> str:
    """The signals behind a verdict, with their values against classify_regime's own
    thresholds: what fired, then what did not (audit item 5). Written from the same
    comparisons the classifier makes, so the explanation cannot contradict the label.
    It used to be one fixed sentence per label ("the yield curve is inverted or labor
    markets are historically tight") beside a +0.31% curve."""
    def _curve() -> str:
        return (f"the 2/10 curve at {t10y2y:+.2f}%" if t10y2y is not None
                else "the 2/10 curve (unavailable)")

    def _ur() -> str:
        return f"unemployment at {unrate:.1f}%" if unrate is not None else "unemployment (unavailable)"

    trig = f"{REGIME_CURVE_TRIGGER:+.2f}%"
    if label == "Recession":
        return "Recession: the NBER recession indicator (USREC) reads 1."
    if label == "Early-cycle":
        curve = (f"{_curve()} is above the {trig} inversion trigger" if t10y2y is not None
                 else "the 2/10 curve is unavailable, which the classifier reads as not inverted")
        return (f"Early-cycle: {_ur()} is above {REGIME_UNRATE_EARLY:.1f}%, and {curve}.")
    if label == "Late-cycle":
        fired, quiet = [], []
        if t10y2y is not None and t10y2y < REGIME_CURVE_TRIGGER:
            fired.append(f"{_curve()} is below the {trig} inversion trigger")
        elif t10y2y is not None:
            quiet.append(f"{_curve()} is above the {trig} inversion trigger")
        if unrate is not None and unrate < REGIME_UNRATE_TIGHT:
            fired.append(f"{_ur()} is below the {REGIME_UNRATE_TIGHT:.1f}% tight-labor threshold")
        elif unrate is not None:
            quiet.append(f"{_ur()} is at or above the {REGIME_UNRATE_TIGHT:.1f}% "
                         "tight-labor threshold")
        text = "Late-cycle: " + " and ".join(fired) + "."
        if quiet:
            text += " Not triggered: " + "; ".join(quiet) + "."
        return text
    if label == "Mid-cycle":
        return (f"Mid-cycle: neither late-cycle trigger fired. {_curve()[0].upper()}"
                f"{_curve()[1:]} is above the {trig} inversion trigger, and {_ur()} is "
                f"between {REGIME_UNRATE_TIGHT:.1f}% and {REGIME_UNRATE_EARLY:.1f}%.")
    return ""


def get_regime_signals(as_of_date: str | None = None) -> dict:
    """
    Fetch the most-recent USREC, T10Y2Y, and UNRATE values as of as_of_date.
    Returns a dict with keys: usrec, t10y2y, unrate, label (from classify_regime;
    None when the signals present cannot support a verdict).
    """
    from datetime import date as _date
    end = as_of_date or _date.today().isoformat()

    def _latest(series_id: str, start: str) -> float | None:
        try:
            s = get_series(series_id, start_date=start)
            s = s.dropna()
            s = s.loc[:end]
            if s.empty:
                return None
            return float(s.iloc[-1])
        except Exception:
            return None

    usrec  = _latest("USREC",   "1945-01-01")
    t10y2y = _latest("T10Y2Y",  "1976-06-01")
    unrate = _latest("UNRATE",  "1948-01-01")

    return {
        "usrec":   usrec,
        "t10y2y":  t10y2y,
        "unrate":  unrate,
        # Dead code (no callers as of 2026-08-17) but kept consistent: the docstring
        # must not promise a label the classifier may decline to give.
        "label":   classify_regime(usrec, t10y2y, unrate).label,
    }


def compute_cape_implied_return(cape: float) -> float:
    """
    Implied 10-year annualized real return from CAPE via log-linear regression.
    Formula: r ≈ −0.070 × ln(CAPE/16) + 0.066
    Calibration: fitted to Shiller long-run data anchored at (CAPE=16, r=6.6%)
    and (CAPE=35, r=1.1%), consistent with Campbell & Shiller (1998) and
    subsequent replications of the log-linear CAPE-to-forward-return relationship.
    """
    return -0.070 * math.log(cape / 16.0) + 0.066


def compute_ecy_real(cape: float, real10y_pct: float) -> float:
    """Excess CAPE Yield from the 10-year real yield itself (DFII10, the TIPS yield):
    ECY = 100 / CAPE − real 10Y. The Macro page reads DFII10 for its real-yield panel
    and now for ECY too, so the page shows one real 10Y, not DFII10 (2.85%) on one
    panel and nominal minus breakeven (2.84%) on another (audit item 5)."""
    return (100.0 / cape) - real10y_pct


def compute_ecy(cape: float, t10y_pct: float, t10yie_pct: float) -> float:
    """
    Excess CAPE Yield: equity earnings yield minus 10-year real bond yield.
    ECY = (100 / CAPE) − (T10Y% − T10YIE%)
    Positive = equities yield more than real bonds (equities cheaper relative to bonds).
    All inputs and output are in percent (e.g., 2.5 means 2.5%).
    """
    return (100.0 / cape) - (t10y_pct - t10yie_pct)


# ── Interpretation thresholds ──────────────────────────────────────────────────

# ECY (all in percent, same units as compute_ecy output) — legacy absolute thresholds kept for tests
EXCESS_CAPE_RICH_THRESHOLD: float = -2.0   # deeply negative → bonds substantially outyield equities
EXCESS_CAPE_RICH: float             =  0.0   # near parity → limited equity premium
EXCESS_CAPE_FAIR: float             =  2.0   # low-to-moderate premium → fair range
EXCESS_CAPE_CHEAP: float            =  4.0   # high premium → equities cheap vs bonds

# ECY percentile thresholds (0.0–1.0) — used by interpret_excess_cape for regime branching
ECY_EXTREME_LOW_PCT:  float = 0.10   # bottom decile → extreme compression
ECY_LOW_PCT:          float = 0.30   # bottom tercile → below-average premium
ECY_HIGH_PCT:         float = 0.70   # top tercile → above-average premium
ECY_EXTREME_HIGH_PCT: float = 0.90   # top decile → historically wide premium

# Yield curve (basis points)
CURVE_INVERTED: float =   0.0
CURVE_FLAT:     float =  50.0   # 0–50 bps
CURVE_NORMAL:   float = 150.0   # 50–150 bps

# HY credit spreads (basis points)
HY_SPREAD_TIGHT:        float = 300.0
HY_SPREAD_NORMAL:       float = 450.0
HY_SPREAD_WIDE:         float = 600.0
HY_SPREAD_RECESSIONARY: float = 800.0

# GDP growth rate (QoQ annualized, percent)
# The FOMC's longer-run median projection for real GDP growth, Summary of Economic
# Projections, September 16, 2026 (federalreserve.gov/monetarypolicy/fomcprojtabl20260916.htm).
# It read 2.5 "(CBO estimate)"; no CBO figure near 2.5% could be found, and CBO's
# February 2026 outlook has real GDP growth averaging 1.8% a year over 2027-2036.
GDP_TREND:       float = 2.0
GDP_TREND_SOURCE: str = "the FOMC's longer-run median projection, September 2026"
GDP_ABOVE_TREND: float = 3.5   # solidly above trend


def interpret_excess_cape(value: float, percentile: float) -> str:
    """
    Dynamic 1–2 sentence interpretation of the Excess CAPE Yield.

    Args:
        value:      ECY in percent (e.g. 0.48 means 0.48%).
        percentile: ECY's rank in its historical distribution, 0.0–1.0.
                    The Jan 2003+ window is used (T10YIE inception).
    """
    assert 0.0 <= percentile <= 1.0, (
        f"percentile must be a fraction 0.0–1.0, got {percentile} "
        f"(units bug? if 0–100 scale, divide by 100 at call site)"
    )
    # An ordinal: "at the 0% percentile" read as a share, not a rank (audit item 9).
    from src.prose_helpers import ordinal
    pct_label = ordinal(percentile * 100)
    if percentile < ECY_EXTREME_LOW_PCT:
        return (
            f"ECY of {value:.2f}% is at extreme compression: at the {pct_label} percentile of "
            "the Jan 2003+ history, equities offer essentially no real-yield premium over bonds. "
            "This historically precedes muted forward equity returns; the Core Fixed Income and "
            "TIPS sleeves are competitively priced relative to equities on a real-yield basis."
        )
    elif percentile < ECY_LOW_PCT:
        return (
            f"ECY of {value:.2f}% is below average at the {pct_label} percentile. "
            "The equity risk premium over real bond yields is thin by historical standards, "
            "suggesting forward equity returns may be below long-run averages."
        )
    elif percentile < ECY_HIGH_PCT:
        return (
            f"ECY of {value:.2f}% is near the historical median (at the {pct_label} percentile). "
            "Equities offer a moderate real-yield premium over bonds, neither a strong valuation "
            "tailwind nor headwind for forward returns."
        )
    elif percentile < ECY_EXTREME_HIGH_PCT:
        return (
            f"ECY of {value:.2f}% is above average at the {pct_label} percentile, indicating "
            "equities offer a meaningful real-yield premium over bonds. "
            "The international developed and US Large Value sleeves, trading at a discount "
            "to US CAPE, benefit most from wide ECY readings."
        )
    else:
        return (
            f"ECY of {value:.2f}% is unusually wide: at the {pct_label} percentile of the "
            "Jan 2003+ history, equities offer a large real-yield premium over bonds. "
            "This has historically signalled materially undervalued equity markets."
        )


def interpret_curve_spread(value_bps: float) -> str:
    """Dynamic 1–2 sentence interpretation of the 10Y−2Y yield curve spread (in bps)."""
    if value_bps < CURVE_INVERTED:
        # The regime classifier counts an inversion only below its trigger (-25 bps), so
        # a shallower one says so here rather than contradicting the verdict (item 5).
        buffer = (
            f" It is above the regime classifier's {REGIME_CURVE_TRIGGER * 100:+.0f} bps "
            "trigger, so the regime does not count it as inverted."
            if value_bps > REGIME_CURVE_TRIGGER * 100 else ""
        )
        return (
            f"The yield curve is inverted at {value_bps:+.0f} bps: short-term rates exceed long-term "
            f"rates.{buffer} Persistent inversion has preceded each of the last seven US recessions with a "
            "12–18 month lead time. Allocators watch the un-inversion (curve steepening back above "
            "zero) as the signal that a cutting cycle is underway, not the inversion itself."
        )
    elif value_bps < CURVE_FLAT:
        return (
            f"The yield curve is flat at {value_bps:+.0f} bps: the 10Y−2Y spread is positive but "
            "compressed. A flat curve reflects limited term premium and implies bond markets expect "
            "short rates to remain near current levels; it warrants monitoring for re-inversion."
        )
    elif value_bps < CURVE_NORMAL:
        return (
            f"The yield curve is modestly upward-sloping at {value_bps:+.0f} bps, consistent with a "
            "normal term structure. Duration earns its carry in this environment; "
            "the Core Fixed Income and TIPS sleeves operate as expected portfolio ballast."
        )
    else:
        return (
            f"The yield curve is steep at {value_bps:+.0f} bps. A steep curve historically reflects "
            "high term premium and often emerges in early-cycle recoveries as short rates are cut "
            "while long-end inflation expectations remain elevated; such a curve has historically "
            "been favorable for duration and early-cycle equity returns."
        )


def interpret_hy_spread(value_bps: float) -> str:
    """Dynamic 1–2 sentence interpretation of HY credit spreads (OAS, in bps)."""
    if value_bps < HY_SPREAD_TIGHT:
        return (
            f"HY spreads at {value_bps:.0f} bps are historically tight (below {HY_SPREAD_TIGHT:.0f} bps), "
            "a sign of late-cycle credit market complacency. Tight spreads limit the cushion for further "
            f"compression; historical episodes of sub-{HY_SPREAD_TIGHT:.0f} bps spreads have preceded "
            "equity peaks and subsequent spread blowouts."
        )
    elif value_bps < HY_SPREAD_NORMAL:
        return (
            f"HY spreads at {value_bps:.0f} bps are in the normal mid-cycle range "
            f"({HY_SPREAD_TIGHT:.0f}–{HY_SPREAD_NORMAL:.0f} bps), "
            "consistent with moderate default risk and a healthy credit environment."
        )
    elif value_bps < HY_SPREAD_WIDE:
        return (
            f"HY spreads at {value_bps:.0f} bps are elevated "
            f"(above the {HY_SPREAD_NORMAL:.0f} bps neutral threshold), "
            "signalling rising market concern about credit and default risk. "
            "This level has historically been associated with late-cycle or early recessionary "
            "conditions and has preceded equity market weakness."
        )
    elif value_bps < HY_SPREAD_RECESSIONARY:
        return (
            f"HY spreads at {value_bps:.0f} bps are in the stress zone "
            f"({HY_SPREAD_WIDE:.0f}–{HY_SPREAD_RECESSIONARY:.0f} bps). "
            "Credit markets are pricing meaningful recession risk; defensive positioning via "
            "Core Fixed Income and high-quality equity sleeves is warranted."
        )
    else:
        return (
            f"HY spreads at {value_bps:.0f} bps are at recessionary levels (above "
            f"{HY_SPREAD_RECESSIONARY:.0f} bps). Credit markets are pricing severe recession "
            "and elevated default rates; such levels have historically coincided with "
            "the most severe equity drawdowns."
        )


def interpret_gdp_growth(value: float) -> str:
    """Dynamic 1–2 sentence interpretation of real GDP growth (QoQ annualized, percent)."""
    if value < 0.0:
        return (
            f"Real GDP growth of {value:.1f}% is negative. Two consecutive quarters of negative "
            "growth satisfies the informal recession definition (NBER uses a broader indicator set). "
            "Negative growth is associated with rising unemployment, falling earnings, and "
            "widening credit spreads, conditions in which duration and quality equity historically outperform."
        )
    elif value < GDP_TREND:
        return (
            f"Real GDP growth of {value:.1f}% is below the long-run trend of "
            f"~{GDP_TREND:.1f}% ({GDP_TREND_SOURCE}). Below-trend growth is consistent with a late-cycle "
            "slowdown or early recovery, where monetary easing becomes more likely and "
            "duration (Core Fixed Income, TIPS) gains defensive value."
        )
    elif value < GDP_ABOVE_TREND:
        return (
            f"Real GDP growth of {value:.1f}% is near the long-run trend of "
            f"~{GDP_TREND:.1f}% ({GDP_TREND_SOURCE}), a mid-cycle Goldilocks range. On-trend growth is associated "
            "with stable corporate earnings and balanced equity risk premiums; "
            "the SAA is calibrated for this baseline environment."
        )
    else:
        return (
            f"Real GDP growth of {value:.1f}% is well above the long-run trend of ~{GDP_TREND:.1f}%. "
            "Above-trend growth typically drives strong corporate earnings momentum but "
            "also upward inflation pressure; cyclical sleeves (US Small Cap, Emerging Markets) "
            "tend to outperform, while TIPS provides inflation protection if growth is sustained."
        )


def interpret_us_vs_intl_spread(spread_pp: float, rolling_mean_pp: float) -> str:
    """
    Dynamic interpretation of the trailing 12-month US vs International return spread.

    Args:
        spread_pp:       12-month US minus Intl return, in percentage points (+ve = US leads).
        rolling_mean_pp: 5-year rolling mean of that spread, in pp.

    Both are differences of returns, so they are stated in percentage points: "4.6%
    below its average of 4.6%" read as a garble when the spread was 0.0 (audit item 5).
    A spread that rounds to zero says the two matched instead of "outperformed by 0.0".
    """
    delta = spread_pp - rolling_mean_pp
    if abs(spread_pp) < 0.05:
        lead = ("Over the trailing 12 months, US equities (SPY) and international developed "
                "(EFA) returned about the same (a spread of 0.0 points).")
    else:
        verb = "outperformed" if spread_pp > 0 else "underperformed"
        lead = (f"Over the trailing 12 months, US equities (SPY) {verb} international "
                f"developed (EFA) by {abs(spread_pp):.1f} percentage points.")
    avg = f"its 5-year rolling average of {rolling_mean_pp:+.1f} points"

    if delta > 10:
        context = (f"That is {delta:.1f} points above {avg}, well into extended "
                   "US-leadership territory. Historically such extremes have mean-reverted via "
                   "valuation convergence and dollar cycle turns, supporting the case for the "
                   "international developed sleeves.")
    elif delta > 3:
        context = (f"That is {delta:.1f} points above {avg}, indicating continued US "
                   "leadership.")
    elif delta > -3:
        context = (f"That is near {avg}: US and international relative performance is close "
                   "to its recent historical norm.")
    elif delta > -10:
        context = (f"That is {abs(delta):.1f} points below {avg}, consistent with "
                   "international narrowing the performance gap.")
    else:
        context = (f"That is {abs(delta):.1f} points below {avg}, a strong reversal in "
                   "international's favor, consistent with the valuation mean-reversion thesis "
                   "underlying the developed-international allocation.")
    return f"{lead} {context}"


def interpret_nfci(value: float) -> str:
    """Banded interpretation of the Chicago Fed National Financial Conditions Index.

    NFCI is standardized (mean 0, std 1): POSITIVE = financial conditions TIGHTER
    than the historical average, NEGATIVE = LOOSER, near-zero = around average.
    Crisis peaks run well above +1; sustained easy-money regimes sit below zero.
    The sign convention is the opposite of a valuation percentile — high NFCI is
    "tight/stressed," not "expensive."
    """
    if value >= 0.5:
        return (
            f"NFCI at {value:+.2f} signals financial conditions materially tighter than the "
            "historical average: elevated composite stress across money, debt, equity, and "
            "shadow-banking markets. Tight conditions historically precede credit-spread widening "
            "and pressure on risk assets, favoring a quality bias in the equity sleeves."
        )
    if value <= -0.5:
        return (
            f"NFCI at {value:+.2f} signals financial conditions materially looser than the "
            "historical average: accommodative composite conditions across money, debt, equity, "
            "and shadow-banking markets, historically supportive of risk assets."
        )
    lean = (
        "leaning slightly tighter than" if value > 0
        else "leaning slightly looser than" if value < 0
        else "right at"
    )
    return (
        f"NFCI at {value:+.2f} is around the historical average ({lean} the long-run norm); "
        "financial conditions are neither notably tight nor notably loose."
    )


def format_ur_delta(delta_bps: float) -> str:
    """Format the unemployment rate year-over-year delta for display.

    Args:
        delta_bps: change in bps of percentage points (current − year_ago) × 100.
    Returns:
        Human-readable string, e.g. "+20 bps from one year ago" or "Flat vs one year ago".
    """
    rounded = round(delta_bps)
    if abs(rounded) < 1:
        return "Flat vs one year ago"
    sign = "+" if rounded > 0 else ""
    return f"{sign}{rounded} bps from one year ago"
