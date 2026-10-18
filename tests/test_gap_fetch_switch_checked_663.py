"""The suite's guard that no test leaves the gap-fetch switch changed (#663).

tests/conftest.py reads src.prices._GAP_FETCH before every test's setup and compares it
after the test's own teardown (gap_fetch_switch_left_changed). On a clean tree a guard
that never fires looks the same as one that works, so its decision is tested here both
ways: a switch left changed, either way, is reported naming the test and put back; a
switch set through monkeypatch, and so put back at teardown, is not.
"""
from __future__ import annotations

import sys

import pytest

import src.prices as prices
from tests.conftest import gap_fetch_switch, gap_fetch_switch_left_changed

TEST = "tests/test_example.py::test_example"


@pytest.mark.parametrize("found", [True, False])
def test_a_switch_left_changed_is_reported_naming_the_test_and_put_back(monkeypatch, found):
    monkeypatch.setattr(prices, "_GAP_FETCH", found)        # as the test found it
    prices.set_gap_fetch(not found)                          # as it left it
    assert gap_fetch_switch_left_changed(TEST, found) == (
        f"{TEST} left the gap-fetch switch (src.prices._GAP_FETCH) {not found} past its "
        f"teardown; it found it {found}. Every later test in the process would read with "
        f"it {not found} (#663). Set it with monkeypatch.setattr(prices, '_GAP_FETCH', "
        f"...), which puts it back. It has been put back to {found}.")
    assert prices._GAP_FETCH is found


def test_a_switch_set_through_monkeypatch_is_not_reported():
    found = gap_fetch_switch()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(prices, "_GAP_FETCH", not found)
        assert gap_fetch_switch() is (not found), "premise: it was changed"
    assert gap_fetch_switch_left_changed(TEST, found) is None


def test_a_switch_as_the_test_found_it_is_not_reported():
    assert gap_fetch_switch_left_changed(TEST, gap_fetch_switch()) is None


def test_before_src_prices_is_imported_the_switch_reads_its_default(monkeypatch):
    monkeypatch.delitem(sys.modules, "src.prices")
    assert gap_fetch_switch() is True
    assert gap_fetch_switch_left_changed(TEST, True) is None
