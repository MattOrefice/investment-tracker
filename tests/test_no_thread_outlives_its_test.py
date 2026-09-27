"""The suite's guard that no thread outlives the test that started it (#429).

tests/conftest.py checks every test at teardown with threads_outliving(). On a clean
tree a guard that never fires looks the same as one that works, so its decision is
tested here both ways: a thread still running is reported and the same thread joined is
not, a thread that is about to finish is still reported (no grace period), and an
allowed target is not reported.
"""
from __future__ import annotations

import threading

from tests.conftest import (THREADS_ALLOWED_TO_OUTLIVE, join_for_cleanup, thread_target,
                            threads_outliving)


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
