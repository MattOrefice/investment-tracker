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

Streamlit's script thread with its run over (#741), read when it is reported: AppTest
returns once the runner records SHUTDOWN, before the thread exits. One held past that
return, inside the SHUTDOWN send or after its target has returned (#741's own state), is
joined and passes once it has ended, both here and at this suite's own teardown. One still
alive after the join fails, and so does one reported while its page still runs, even
when it ends within the join. A thread past its run that is not Streamlit's fails as any
thread does. A failure shows each thread's stack as it was reported.
"""
from __future__ import annotations

import threading
import time

import pytest
from streamlit.runtime.scriptrunner import ScriptRunnerEvent
from streamlit.testing.v1 import AppTest
from streamlit.testing.v1 import app_test as _app_test
from streamlit.testing.v1.local_script_runner import LocalScriptRunner

from tests.conftest import (ABANDONED_EXPORT, SCRIPT_THREAD_NAME, SCRIPT_THREAD_TARGET,
                            THREADS_ALLOWED_TO_OUTLIVE, fails_after_join,
                            finished_script_thread, in_an_export, join_for_cleanup,
                            outlived_threads_fail, thread_target, threads_outliving)


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


# ── Streamlit's script thread with its run over (#741) ───────────────────────

def _page() -> None:
    import streamlit as st
    st.write("a page")


def _page_that_waits(gate) -> None:
    import streamlit as st
    st.write("before the wait")
    gate.wait(30)
    st.write("after the wait")


def _hold_after_shutdown(monkeypatch, seconds: float, release: threading.Event) -> list:
    """AppTest's runner holds its script thread for up to ``seconds`` inside the send of
    SHUTDOWN, the event AppTest returns on. The thread is alive past AppTest's return
    with its target still on the stack. Returns the list each held thread joins."""
    held: list = []

    class _HeldAfterShutdown(LocalScriptRunner):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            send = self.on_event.send

            def send_then_hold(*a, **kw):
                shutdown = kw.get("event") == ScriptRunnerEvent.SHUTDOWN
                if shutdown:
                    held.append(threading.current_thread())
                sent = send(*a, **kw)
                if shutdown:
                    release.wait(seconds)
                return sent

            self.on_event.send = send_then_hold

    monkeypatch.setattr(_app_test, "LocalScriptRunner", _HeldAfterShutdown)
    return held


class _HeldPastRun(threading.Thread):
    """A thread kept alive for up to ``seconds`` after Thread.run has returned, its target
    already deleted."""

    def __init__(self, *args, release: threading.Event, seconds: float, **kwargs):
        super().__init__(*args, **kwargs)
        self._held_release, self._held_seconds = release, seconds

    def run(self) -> None:
        super().run()
        self._held_release.wait(self._held_seconds)


def _hold_past_run(monkeypatch, seconds: float, release: threading.Event) -> list:
    """AppTest's script thread outlives its own target: #741's state, reported with its
    target gone. ScriptRunner.start as streamlit 1.57.0 writes it, on _HeldPastRun."""
    held: list = []

    def start(runner) -> None:
        runner._script_thread = _HeldPastRun(target=runner._run_script_thread,
                                             name=SCRIPT_THREAD_NAME,
                                             release=release, seconds=seconds)
        held.append(runner._script_thread)
        runner._script_thread.start()

    monkeypatch.setattr(LocalScriptRunner, "start", start)
    return held


def _target_returned(t: threading.Thread, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while getattr(t, "_target", None) is not None and time.monotonic() < deadline:
        time.sleep(0.005)
    return getattr(t, "_target", None) is None


def test_a_script_thread_held_in_its_shutdown_send_is_joined_and_passes(monkeypatch):
    """AppTest has returned and the runner has recorded SHUTDOWN, so no rerun, fragment
    or callback can run. The thread is still alive inside the send."""
    held = _hold_after_shutdown(monkeypatch, 1.0, threading.Event())
    before = set(threading.enumerate())
    at = AppTest.from_function(_page).run()
    assert not at.exception
    (t,) = held
    alive = threads_outliving(before)
    assert alive == [t] and t.name == SCRIPT_THREAD_NAME, "premise: alive past the return"
    assert thread_target(t) == SCRIPT_THREAD_TARGET, "premise: its target is still running"
    assert finished_script_thread(t)
    assert fails_after_join(alive) == []
    assert not t.is_alive()


def test_a_script_thread_reported_with_its_target_returned_is_joined_and_passes(
        monkeypatch):
    """#741's own thread: thread_target named it threading.Thread because Thread.run had
    already deleted its target."""
    held = _hold_past_run(monkeypatch, 1.0, threading.Event())
    before = set(threading.enumerate())
    AppTest.from_function(_page).run()
    (t,) = held
    assert _target_returned(t) and t.is_alive(), "premise: alive with its target gone"
    alive = threads_outliving(before)
    assert alive == [t]
    assert finished_script_thread(t)
    assert fails_after_join(alive) == []
    assert not t.is_alive()


@pytest.mark.parametrize("hold", [_hold_after_shutdown, _hold_past_run])
def test_a_script_thread_still_alive_after_the_join_fails(monkeypatch, hold):
    release = threading.Event()
    held = hold(monkeypatch, 30, release)
    before = set(threading.enumerate())
    AppTest.from_function(_page).run()
    (t,) = held
    try:
        alive = threads_outliving(before)
        assert alive == [t]
        assert finished_script_thread(t), "premise: excused when reported"
        assert fails_after_join(alive, seconds=0.2) == [t]
        assert t.is_alive()
    finally:
        release.set()
        join_for_cleanup([t])


def test_a_script_thread_reported_while_its_page_runs_fails_even_if_it_ends_in_the_join():
    """Whether its run is over is read when it is reported, as a Timer's cancellation
    is. A page still running is the hazard #429 exists for, however soon it ends."""
    gate = threading.Event()
    at = AppTest.from_function(_page_that_waits, args=(gate,), default_timeout=30)
    before = set(threading.enumerate())
    driver = threading.Thread(target=at.run, name="drives the AppTest", daemon=True)
    opener = threading.Timer(0.3, gate.set)
    driver.start()
    try:
        deadline = time.monotonic() + 10
        scripts: list = []
        while not scripts and time.monotonic() < deadline:
            time.sleep(0.005)
            scripts = [t for t in threads_outliving(before) if t.name == SCRIPT_THREAD_NAME]
        (script,) = scripts
        assert not finished_script_thread(script), "premise: its page is still running"
        opener.start()
        assert fails_after_join([script]) == [script]
        assert not script.is_alive(), "premise: it ended within the join"
    finally:
        gate.set()
        join_for_cleanup([driver] + ([opener] if opener.ident else []))
    assert not at.exception and "after the wait" in [m.value for m in at.markdown]


@pytest.mark.parametrize("name", ["someone else's", SCRIPT_THREAD_NAME])
def test_a_thread_past_its_run_that_is_not_streamlits_fails(name):
    """Its target is gone as #741's was, but no ScriptRunContext of Streamlit's is on it:
    the name alone does not excuse it."""
    before = set(threading.enumerate())
    t = _HeldPastRun(target=lambda: None, name=name, release=threading.Event(),
                     seconds=0.3, daemon=True)
    t.start()
    assert _target_returned(t) and t.is_alive(), "premise: alive with its target gone"
    alive = threads_outliving(before)
    assert alive == [t]
    assert not finished_script_thread(t)
    assert fails_after_join(alive) == [t]
    assert not t.is_alive(), "premise: it ended within the join"


def test_a_page_thread_held_past_apptests_return_is_left_to_this_suites_teardown(
        monkeypatch):
    """This test returns with the script thread still alive past AppTest's return, its run
    over, as #741's test did. The suite's own teardown check passes it once it ends;
    without the third exception the test errors at teardown."""
    held = _hold_after_shutdown(monkeypatch, 2.0, threading.Event())
    AppTest.from_function(_page).run()
    (t,) = held
    assert t.is_alive(), "premise: the script thread outlives the body"


def test_a_failure_shows_each_threads_stack_as_it_was_reported():
    """The stack is read before the join, so a thread that ends within it still shows
    where it was when its test ended."""
    before = set(threading.enumerate())
    t = threading.Thread(target=threading.Event().wait, args=(0.3,), name="waits a moment",
                         daemon=True)
    t.start()
    with pytest.raises(AssertionError) as raised:
        outlived_threads_fail("tests/a_file.py::a_test", before)
    assert not t.is_alive(), "premise: it ended within the join"
    said = str(raised.value)
    assert said.startswith("tests/a_file.py::a_test left 1 thread(s) running past its "
                           "teardown: [('waits a moment', 'threading.Event.wait')].")
    stack = said.split("Stack of ('waits a moment', 'threading.Event.wait') when "
                       "reported:\n", 1)[1]
    assert "in _bootstrap_inner" in stack and ", in wait\n" in stack


def test_no_failure_when_nothing_is_left():
    before = set(threading.enumerate())
    outlived_threads_fail("tests/a_file.py::a_test", before)
