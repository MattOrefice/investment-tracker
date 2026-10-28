"""Which write surfaces the trap covers, and the one thing that covers the rest.

#271. Two claims, and they are different in kind:

  1. The trap catches the six mechanisms below. That is an ENUMERATION and it
     will go stale; these tests are what make it go stale LOUDLY.
  2. The content backstop in tests/conftest.py catches a write through a surface
     nobody enumerated. That is the claim that does not decay, and
     test_an_unenumerated_surface_is_invisible_to_the_trap proves it rather than
     asserting it.

Nothing here writes, or tries to write, a tracked file (decided by the owner on
October 22, 2026, #678). Every target is a scratch file the real trap is told to treat
as tracked, so the trap refuses each attempt at the moment of the call, the assertions
read the recorded attempt, and the file's bytes show nothing reached it. Until #678 the
target was data/shiller_cape.csv, and when pandas 3.1.0 took to_parquet past the trap,
the parquet bytes landed in it on CI and failed every later test that read it.
"""
import builtins
import io
import os
import pathlib
import sys

import pandas as pd
import pytest

# The REAL trap, imported rather than reimplemented: a local copy would drift from
# the shipped fixture and these tests would then prove nothing about it.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_market_data_immutability import _tracked_data_files, install_write_trap  # noqa: E402

CONTENT = b"date,cape\n2026-09-30,38.1\n"


def _key(path) -> str:
    return os.path.normcase(str(pathlib.Path(path).resolve()))


@pytest.fixture
def trap(monkeypatch, tmp_path):
    """(the scratch file the real trap treats as tracked, the attempts it recorded).

    Written before the trap is installed, so the trap refuses only the test's own
    attempts; it is outside data/, so nothing git tracks is in the trap's set."""
    target = tmp_path / "tracked.csv"
    target.write_bytes(CONTENT)
    assert _key(target) not in _tracked_data_files()
    return target, install_write_trap(monkeypatch, {_key(target)})


def _refused(trap) -> bool:
    """The trap recorded an attempt on the target, and nothing reached its bytes."""
    target, attempts = trap
    return (any(rec[0] == _key(target) for rec in attempts)
            and target.read_bytes() == CONTENT)


# ── the six mechanisms, each shown caught ─────────────────────────────────────

def test_builtins_open_is_caught(trap):
    with pytest.raises(AssertionError):
        builtins.open(trap[0], "a")
    assert _refused(trap)


def test_io_open_is_caught(trap):
    """MISSED before #271: builtins.open and io.open are the same object under
    two names, and patching one leaves the other."""
    with pytest.raises(AssertionError):
        io.open(trap[0], "a")
    assert _refused(trap)


def test_pathlib_write_text_is_caught(trap):
    """MISSED before #271, and the most consequential of the three — this is the
    idiomatic file write in modern Python. It reaches io.open."""
    with pytest.raises(AssertionError):
        pathlib.Path(trap[0]).write_text("x")
    assert _refused(trap)


def test_pathlib_write_bytes_is_caught(trap):
    """Same route as write_text, and the form actually used in this codebase at
    src/reports.py:1760 (against a gitignored target, so not a defect there)."""
    with pytest.raises(AssertionError):
        pathlib.Path(trap[0]).write_bytes(b"x")
    assert _refused(trap)


def test_os_replace_is_caught(trap, tmp_path):
    """MISSED before #271. The atomic-write idiom — write a temp file, then
    replace — is the RECOMMENDED pattern for exactly this kind of file, so its
    absence from the trap was the gap most likely to be walked into."""
    src = tmp_path / "src.csv"
    src.write_text("a\n")
    with pytest.raises(AssertionError):
        os.replace(src, trap[0])
    assert _refused(trap)


def test_pandas_to_csv_is_caught(trap):
    with pytest.raises(AssertionError):
        pd.DataFrame({"a": [1]}).to_csv(trap[0])
    assert _refused(trap)


def test_pandas_to_parquet_is_caught(trap):
    """Predicted wrong twice, so pinned on every version the suite runs. At #271
    it was predicted to be the GAP, on the theory that pyarrow opens files in C;
    with pandas 3.0.6 it routes through builtins.open and was caught all along.
    pandas 3.1.0 then did move the open into C++ (pyarrow.OSFile), and this test
    failed DID NOT RAISE on CI with its bytes in data/shiller_cape.csv (#678).
    install_write_trap now guards the pandas function that opens the destination
    on both versions; its docstring has the measurement. Reasoning about which
    surface a library uses is not a substitute for measuring it.

    Aimed at a CSV since #386 retired the only tracked parquet. The trap guards
    PATHS, not formats, so the writer is still what is measured."""
    with pytest.raises(AssertionError):
        pd.DataFrame({"a": [1]}).to_parquet(trap[0])
    assert _refused(trap)


# ── the claim that does not decay ─────────────────────────────────────────────

def test_an_unenumerated_surface_is_invisible_to_the_trap(trap):
    """THE ARGUMENT FOR THE BACKSTOP, PROVEN RATHER THAN ASSERTED.

    os.open + os.write is a raw file-descriptor write. It goes through none of the
    surfaces install_write_trap patches, so the trap records NOTHING — and would
    refuse nothing — though the file is one the trap treats as tracked. It is a
    scratch file, because the point is the trap's blindness, not a real write.

    This test passing means the trap has a hole. That is the finding. The hole is
    covered by the content backstop in tests/conftest.py, which watches bytes
    instead of call surfaces — see test_the_content_backstop_is_installed.
    """
    target, attempts = trap
    fd = os.open(target, os.O_WRONLY | os.O_APPEND)
    try:
        os.write(fd, b"written via a surface nothing patches")
    finally:
        os.close(fd)

    assert target.read_bytes() != CONTENT, "the raw write did not happen; test proves nothing"
    assert not attempts, (
        "os.open/os.write was recorded — the trap grew a surface, so this test's "
        "premise changed. Re-measure which surfaces are covered and update #271.")


def test_the_content_backstop_is_installed():
    """The backstop is autouse in conftest, so nothing here claims it. Assert it
    is present and armed, or a silently-disabled backstop looks exactly like a
    clean repo."""
    import conftest

    assert conftest._TRACKED_DATA, "backstop watches nothing — it would be vacuous"
    tracked_names = {p.name for p in conftest._TRACKED_DATA}
    assert "demo.db" in tracked_names, (
        "the backstop must cover the DB channel too — that is what catches a "
        "subprocess write or a run with the redirect kill switch set")
    assert not os.environ.get("TRACKER_TEST_NO_DATA_BACKSTOP"), "backstop disabled"


def test_the_backstop_is_wider_than_the_trap():
    """The trap deliberately excludes *.db; the backstop deliberately includes it.
    If they ever converge, one of the two docstrings is wrong."""
    import conftest

    trap_set = _tracked_data_files()
    backstop = {os.path.normcase(str(p.resolve())) for p in conftest._TRACKED_DATA}
    only_backstop = backstop - trap_set
    assert only_backstop, (
        "the backstop no longer covers anything the trap misses — check whether "
        "the trap started including *.db")
    assert all(p.endswith(".db") for p in only_backstop), (
        f"unexpected files covered only by the backstop: {sorted(only_backstop)}")
