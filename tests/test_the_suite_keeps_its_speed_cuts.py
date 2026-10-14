"""The four cuts #544 made to the suite's time on GitHub's runner, and the one file it
kept out of the checkout (decided by the owner on October 14, 2026).

#544 measured the CI step at 786 s and named what made its slowest tests slow. Four cuts:

  * the tests that hold FRED until a page gives up wait tests/conftest.py's
    FRED_TEST_WAIT, 0.5 s, not the live page's 15 s;
  * tests/test_imports.py runs its fresh interpreters four at a time;
  * the two real PDF builds make no network calls;
  * tests/test_locked_figures_golden.py's tests share their renders.

And the suite writes the BE/ME breakpoints to a temporary copy, never to
data/cache/ff_beme_breakpoints.csv in the checkout, the one file parallel runs would
share. Undoing a cut fails nothing else, since each only made a test faster, so each
is pinned here.
"""
from __future__ import annotations

import ast
import threading
import time
from pathlib import Path

import src.macro as macro
from tests.conftest import BEME_CACHE_IN_CHECKOUT, FRED_TEST_WAIT

ROOT = Path(__file__).resolve().parent.parent


def _function(rel: str, name: str) -> ast.FunctionDef:
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def _params(rel: str, name: str) -> "list[str]":
    return [a.arg for a in _function(rel, name).args.args]


def _calls(rel: str, name: str, callee: str) -> int:
    return sum(1 for n in ast.walk(_function(rel, name)) if isinstance(n, ast.Call)
               and (getattr(n.func, "attr", None) == callee
                    or getattr(n.func, "id", None) == callee))


def test_the_fred_tests_wait_half_a_second_and_the_live_page_fifteen():
    assert macro.PAGE_WAIT_SECONDS == 15.0, "the live page's wait"
    assert FRED_TEST_WAIT == 0.5
    assert "short_fred_wait" in _params(
        "tests/test_fred_warm.py",
        "test_a_hanging_fred_holds_the_macro_page_for_its_wait_and_no_longer")
    assert "short_fred_wait" in _params("tests/test_fred_bounded.py", "hanging_fred")
    for name in ("test_performance_holds_for_the_wait_at_most_and_states_the_stored_bill_rate",
                 "test_asset_evaluation_waits_once_for_all_four_of_its_fred_series",
                 "test_the_pdfs_macro_section_names_its_stored_copies"):
        assert "hanging_fred" in _params("tests/test_fred_bounded.py", name), name


def test_short_fred_wait_sets_the_wait_the_pages_read(short_fred_wait):
    assert short_fred_wait == FRED_TEST_WAIT == macro.PAGE_WAIT_SECONDS


def test_the_import_checks_run_four_at_a_time():
    """Eight jobs that each hold for a moment: four run at once, never more."""
    from tests.test_imports import POOL_WORKERS, run_four_at_a_time
    assert POOL_WORKERS == 4
    lock, running, most = threading.Lock(), [0], [0]

    def job():
        with lock:
            running[0] += 1
            most[0] = max(most[0], running[0])
        time.sleep(0.2)
        with lock:
            running[0] -= 1
        return True, ""

    results = run_four_at_a_time({str(i): job for i in range(8)})
    assert results == {str(i): (True, "") for i in range(8)}
    assert most[0] == 4, most[0]
    for fixture in ("importable", "db_free"):
        assert _calls("tests/test_imports.py", fixture, "run_four_at_a_time") == 1, fixture


def test_the_two_pdf_builds_run_offline():
    for rel, name in (("tests/test_market_data_immutability.py",
                       "test_pdf_build_writes_no_tracked_data"),
                      ("tests/test_asset_eval_disposition.py",
                       "test_partial_state_through_a_real_pdf_build")):
        assert _calls(rel, name, "block_network") == 1, name


def test_block_network_refuses_every_lookup_and_fetch(monkeypatch):
    import socket

    import pytest

    import src.prices as prices
    from tests.conftest import block_network
    block_network(monkeypatch)
    for call in (lambda: socket.getaddrinfo("example.com", 443),
                 lambda: prices._SESSION.get("https://example.com"),
                 lambda: macro.fetch_fred_series("DGS10", "2026-01-01")):
        with pytest.raises(OSError, match="network blocked"):
            call()


def test_the_golden_tests_share_their_renders():
    rel = "tests/test_locked_figures_golden.py"
    assert "renders" in _params(rel, "test_the_marks_change_nothing_else_in_the_render")
    assert _calls(rel, "test_the_marks_change_nothing_else_in_the_render", "render_book") == 1, (
        "the marked side is the module's render; only the unmarked one is made here")
    for name in ("test_rewording_the_factor_prose_moves_no_figure",
                 "test_every_label_reworded_moves_nothing"):
        assert _params(rel, name) == ["reworded"], name
        assert _calls(rel, name, "render_book") == 0, name
    assert _calls(rel, "reworded", "render_book") == 1


def test_the_suite_writes_the_beme_breakpoints_to_a_temporary_copy():
    import src.factors as factors
    cache = Path(factors._BEME_CACHE).resolve()
    assert cache != BEME_CACHE_IN_CHECKOUT.resolve()
    assert ROOT.resolve() not in cache.parents, cache
    # The page subprocess in tests/test_imports.py, which the session's redirect does
    # not reach, sets its own.
    script = _function("tests/test_imports.py", "_page_importable_offline")
    assert "_factors._BEME_CACHE = pathlib.Path(" in ast.unparse(script)
