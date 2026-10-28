"""The suite's guard that no thread outlives the test that started it (#429).

tests/conftest.py checks every test at teardown with threads_outliving(). On a clean
tree a guard that never fires looks the same as one that works, so its decision is
tested here both ways: a thread still running is reported and the same thread joined is
not, a thread that is about to finish is still reported (no grace period), and an
allowed target is not reported.

The one exception (#635): a Timer cancelled when it is reported is joined and passes
once it has ended. A Timer never cancelled fails, and so does a plain thread, even when
either ends within the join.

A chart export's own threads (#698), a thread whose target lives in kaleido,
choreographer or logistro, read when it is reported:
- one reported is joined, and passes once it has ended, as a cancelled Timer does; one
  still alive after the join fails;
- one alive beside the PDF's abandoned _render is not reported. Without that _render the
  same threads are.
A thread from any other module fails as before, beside an abandoned _render or ending
within the join.
"""
from __future__ import annotations

import threading
import time

import pytest

from tests.conftest import (ABANDONED_EXPORT, THREADS_ALLOWED_TO_OUTLIVE,
                            fails_after_join, in_an_export, join_for_cleanup,
                            thread_target, threads_outliving)


def _parked(stop: threading.Event, name: str) -> threading.Thread:
    t = threading.Thread(target=stop.wait, args=(30,), name=name, daemon=True)
    t.start()
    return t


def test_a_thread_still_running_is_reported_and_the_same_thread_joined_is_not():
    before = set(threading.enumerate())
    stop = threading.Event()
    t = _parked(stop, "outliver")
    try:
        assert threads_outliving(before) == [t]
    finally:
        stop.set()
        t.join(5)
    assert threads_outliving(before) == []


def test_a_thread_that_would_finish_in_a_moment_has_still_outlived_its_test():
    """No grace period. Whether a leaked thread has ended by the check depends on the
    machine: the warm ended within half a second without a FRED key and was alive 17
    tests later with one, so a guard that waited for it passed in CI and failed
    locally."""
    before = set(threading.enumerate())
    t = threading.Thread(target=threading.Event().wait, args=(0.3,), daemon=True)
    t.start()
    try:
        assert threads_outliving(before) == [t]
    finally:
        join_for_cleanup([t])
    assert not t.is_alive()


def test_an_allowed_target_is_not_reported(monkeypatch):
    before = set(threading.enumerate())
    stop = threading.Event()
    t = _parked(stop, "allowed")
    try:
        monkeypatch.setitem(THREADS_ALLOWED_TO_OUTLIVE, thread_target(t), "this test")
        assert threads_outliving(before) == []
    finally:
        stop.set()
        t.join(5)


# ── a cancelled Timer (#635) ─────────────────────────────────────────────────

class _SlowToWake(threading.Event):
    """A Timer's finished event that wakes a second late, so a cancelled Timer is still
    alive when it is reported, as Streamlit's spinner Timer was in #635."""

    def wait(self, timeout=None):
        woken = super().wait(timeout)
        time.sleep(1.0)
        return woken


def _scheduled(fired: list) -> None:
    fired.append("fired")


def test_a_cancelled_timer_is_joined_and_passes():
    """A Timer cancelled when it is reported can never start its function. It is
    joined, and passes once it has ended."""
    fired: list = []
    before = set(threading.enumerate())
    t = threading.Timer(30, _scheduled, args=[fired])
    t.finished, t.daemon = _SlowToWake(), True
    t.start()
    t.cancel()
    alive = threads_outliving(before)
    assert alive == [t], "premise: the cancelled Timer is still alive when reported"
    assert fails_after_join(alive) == []
    assert not t.is_alive() and fired == []


def test_a_timer_never_cancelled_fails_even_when_it_fires_and_ends_within_the_join():
    fired: list = []
    before = set(threading.enumerate())
    t = threading.Timer(0.3, _scheduled, args=[fired])
    t.daemon = True
    t.start()
    alive = threads_outliving(before)
    assert alive == [t]
    assert fails_after_join(alive) == [t]
    assert not t.is_alive() and fired == ["fired"], "premise: it fired and ended in the join"


def test_a_plain_thread_that_ends_within_the_join_still_fails():
    before = set(threading.enumerate())
    t = threading.Thread(target=threading.Event().wait, args=(0.3,), daemon=True)
    t.start()
    alive = threads_outliving(before)
    assert alive == [t]
    assert fails_after_join(alive) == [t]
    assert not t.is_alive(), "premise: it ended within the join"


def test_a_timer_is_named_by_the_function_it_schedules():
    t = threading.Timer(30, _scheduled, args=[[]])
    assert thread_target(t) == f"{_scheduled.__module__}._scheduled"


# ── an abandoned export's threads (#698) ─────────────────────────────────────

# The eight threads CI's runner showed beside an abandoned export (#698), by name and
# by the module and function of their target.
_RUNNERS_EIGHT = [
    ("Thread-7 (run)", "kaleido._sync_server", "oneshot_async_run.<locals>.run"),
    ("checking_close-0", "choreographer.utils._manual_thread_pool",
     "ManualThreadExecutor._worker"),
    ("checking_close-1", "choreographer.utils._manual_thread_pool",
     "ManualThreadExecutor._worker"),
    ("checking_close-2", "choreographer.utils._manual_thread_pool",
     "ManualThreadExecutor._worker"),
    ("readwrite_thread-0", "choreographer.utils._manual_thread_pool",
     "ManualThreadExecutor._worker"),
    ("readwrite_thread-1", "choreographer.utils._manual_thread_pool",
     "ManualThreadExecutor._worker"),
    ("browser_procThread", "logistro._api", "getPipeLogger.<locals>.read_pipe"),
    ("watchdog_wait-0", "choreographer.utils._manual_thread_pool",
     "ManualThreadExecutor._worker"),
]


def _thread_in(module: str, qualname: str, stop: threading.Event,
               name: str, seconds: float = 30) -> threading.Thread:
    """A parked thread whose target reads as module.qualname to thread_target. It ends
    when ``stop`` is set or after ``seconds``."""
    def wait() -> None:
        stop.wait(seconds)
    wait.__module__, wait.__qualname__ = module, qualname
    t = threading.Thread(target=wait, name=name, daemon=True)
    t.start()
    return t


def _abandoned_render(stop: threading.Event) -> threading.Thread:
    t = _thread_in("src.reports", "_render_chart_to_png.<locals>._render", stop,
                   "Thread-6 (_render)")
    assert thread_target(t) == ABANDONED_EXPORT, "premise: it reads as the PDF's _render"
    return t


def test_an_abandoned_exports_threads_pass():
    before = set(threading.enumerate())
    stop = threading.Event()
    render = _abandoned_render(stop)
    export = [_thread_in(m, q, stop, n) for n, m, q in _RUNNERS_EIGHT]
    try:
        assert all(in_an_export(t) for t in export)
        assert threads_outliving(before) == []
    finally:
        stop.set()
        join_for_cleanup([render, *export])


def test_the_same_threads_with_no_abandoned_render_fail():
    before = set(threading.enumerate())
    render_stop, export_stop = threading.Event(), threading.Event()
    render = _abandoned_render(render_stop)
    export = [_thread_in(m, q, export_stop, n) for n, m, q in _RUNNERS_EIGHT]
    try:
        assert threads_outliving(before) == [], "premise: they pass beside the _render"
        render_stop.set()
        render.join(5)
        assert not render.is_alive()
        assert threads_outliving(before) == export
    finally:
        render_stop.set()
        export_stop.set()
        join_for_cleanup([render, *export])


def test_an_earlier_tests_abandoned_render_still_covers_its_export():
    """An export abandoned in an earlier test keeps starting threads, choreographer's
    close pool among them. Its _render was alive before this test began, and still
    covers them."""
    stop = threading.Event()
    render = _abandoned_render(stop)
    before = set(threading.enumerate())
    late = _thread_in("choreographer.utils._manual_thread_pool",
                      "ManualThreadExecutor._worker", stop, "checking_close-0")
    try:
        assert render in before, "premise: the _render predates this test"
        assert threads_outliving(before) == []
    finally:
        stop.set()
        join_for_cleanup([render, late])


_OTHER_MODULES = [
    ("plotly.io._kaleido", "to_image"),              # names kaleido, lives in plotly
    ("kaleidoscope.render", "run"),                  # a package that starts with it
    ("concurrent.futures.thread", "_worker"),        # an executor's thread
    (__name__, "parked"),                            # this test's own
]


@pytest.mark.parametrize("module, qualname", _OTHER_MODULES)
def test_a_thread_from_any_other_module_fails_beside_an_abandoned_export(module,
                                                                          qualname):
    """Named as one of kaleido's export threads is, so only its module tells it apart."""
    before = set(threading.enumerate())
    stop = threading.Event()
    render = _abandoned_render(stop)
    other = _thread_in(module, qualname, stop, "checking_close-0")
    try:
        assert not in_an_export(other)
        assert threads_outliving(before) == [other]
    finally:
        stop.set()
        join_for_cleanup([render, other])


def test_a_real_abandoned_export_passes_and_its_threads_fail_without_its_render():
    """The real construction, not names typed above. One real chart export, with
    _render_chart_to_png's 25 s join cut to 0.2 s so the export is abandoned in flight,
    as #697's CI run abandoned one past 25 s (#698). Every thread it leaves besides
    _render lives in kaleido, choreographer or logistro. They pass beside its _render,
    and the same threads with the _render out of view are reported. It counts as a later
    export, and the process's first-export flag is put back, so the next real export
    still waits as it would have."""
    import plotly.graph_objects as go
    from src import reports

    join = threading.Thread.join

    def cut(self, timeout=None):
        return join(self, 0.2 if timeout == reports.EXPORT_WAIT_SECONDS else timeout)

    before = set(threading.enumerate())
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(reports, "_first_export_made", True)
        mp.setattr(threading.Thread, "join", cut)
        png = reports._render_chart_to_png(go.Figure(go.Scatter(x=[1, 2, 3], y=[1, 3, 2])))
    left = [t for t in threading.enumerate() if t not in before and t.is_alive()]
    render = [t for t in left if thread_target(t) == ABANDONED_EXPORT]
    export = [t for t in left if t not in render]
    try:
        assert png is None and len(render) == 1, "premise: the export was abandoned"
        assert export, "premise: the abandoned export has threads of its own"
        assert all(in_an_export(t) for t in export), [(t.name, thread_target(t))
                                                      for t in export]
        assert threads_outliving(before) == []
        assert threads_outliving(before, threads=export) == export
    finally:
        join_for_cleanup(render, seconds=120)
        join_for_cleanup([t for t in threading.enumerate() if t not in before])
    assert not [t for t in threading.enumerate() if t not in before], (
        "the abandoned export did not end within 120 s")


# ── a chart export's threads, joined (#698) ──────────────────────────────────

# One of the runner's eight from each package: kaleido's run thread, choreographer's
# close pool and logistro's pipe reader.
_ONE_FROM_EACH = [pytest.param(*_RUNNERS_EIGHT[i], id=p)
                  for i, p in ((0, "kaleido"), (1, "choreographer"), (6, "logistro"))]


@pytest.mark.parametrize("name, module, qualname", _ONE_FROM_EACH)
def test_an_export_thread_that_ends_within_the_join_passes(name, module, qualname):
    """As choreographer's close pool did on GitHub's runner, still alive when its export
    returned and ended 2 to 3 ms later (#698). It is told for an export's when it is
    reported: once it has ended it holds no target to read."""
    before = set(threading.enumerate())
    t = _thread_in(module, qualname, threading.Event(), name, seconds=0.3)
    alive = threads_outliving(before)
    assert alive == [t], "premise: it is reported, with no abandoned _render beside it"
    assert fails_after_join(alive) == []
    assert not t.is_alive(), "premise: it ended within the join"


@pytest.mark.parametrize("name, module, qualname", _ONE_FROM_EACH)
def test_an_export_thread_still_alive_after_the_join_fails_unless_beside_an_abandoned_render(
        name, module, qualname):
    before = set(threading.enumerate())
    stop = threading.Event()
    t = _thread_in(module, qualname, stop, name)
    render = None
    try:
        alive = threads_outliving(before)
        assert alive == [t]
        assert fails_after_join(alive, seconds=0.2) == [t]
        assert t.is_alive(), "premise: the join did not end it"
        render = _abandoned_render(stop)
        assert threads_outliving(before) == []
    finally:
        stop.set()
        join_for_cleanup([t] + ([render] if render else []))


@pytest.mark.parametrize("module, qualname", _OTHER_MODULES)
def test_a_thread_from_any_other_module_fails_even_when_it_ends_within_the_join(module,
                                                                               qualname):
    """Named as one of choreographer's close pool is, so only its module tells it apart."""
    before = set(threading.enumerate())
    t = _thread_in(module, qualname, threading.Event(), "checking_close-0", seconds=0.3)
    alive = threads_outliving(before)
    assert alive == [t]
    assert fails_after_join(alive) == [t]
    assert not t.is_alive(), "premise: it ended within the join"
