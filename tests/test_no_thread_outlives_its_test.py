"""The suite's guard that no thread outlives the test that started it (#429).

tests/conftest.py checks every test at teardown with threads_outliving(). On a clean
tree a guard that never fires looks the same as one that works, so its decision is
tested here both ways: a thread still running is reported and the same thread joined is
not, a thread that is about to finish is still reported (no grace period), and an
allowed target is not reported.

The one exception (#635): a Timer cancelled when it is reported is joined and passes
once it has ended. A Timer never cancelled fails, and so does a plain thread, even when
either ends within the join.
"""
from __future__ import annotations

import threading
import time

from tests.conftest import (THREADS_ALLOWED_TO_OUTLIVE, fails_after_join,
                            join_for_cleanup, thread_target, threads_outliving)


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
