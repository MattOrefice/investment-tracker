"""Which write surfaces the trap covers, and the one thing that covers the rest.

#271. Two claims, and they are different in kind:

  1. write_trap catches the six mechanisms below. That is an ENUMERATION and it
     will go stale; these tests are what make it go stale LOUDLY.
  2. The content backstop in tests/conftest.py catches a write through a surface
     nobody enumerated. That is the claim that does not decay, and
     test_an_unenumerated_surface_is_invisible_to_the_trap proves it rather than
     asserting it.

Nothing here writes a tracked file. Every target is tracked, so write_trap refuses
each attempt at the moment of the call; the assertions read the recorded attempt.
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
from test_market_data_immutability import _tracked_data_files, write_trap  # noqa: E402,F401

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSV = ROOT / "data" / "shiller_cape.csv"
PARQUET = ROOT / "data" / "cache" / "prices_hyg.parquet"


def _skip_without(path):
    if not path.exists():
        pytest.skip(f"{path.name} absent")


def _attempted(attempts, path) -> bool:
    want = os.path.normcase(str(path.resolve()))
    return any(rec[0] == want for rec in attempts)


# ── the six mechanisms, each shown caught ─────────────────────────────────────

def test_builtins_open_is_caught(write_trap):
    _skip_without(CSV)
    with pytest.raises(AssertionError):
        builtins.open(CSV, "a")
    assert _attempted(write_trap, CSV)


def test_io_open_is_caught(write_trap):
    """MISSED before #271: builtins.open and io.open are the same object under
    two names, and patching one leaves the other."""
    _skip_without(CSV)
    with pytest.raises(AssertionError):
        io.open(CSV, "a")
    assert _attempted(write_trap, CSV)


def test_pathlib_write_text_is_caught(write_trap):
    """MISSED before #271, and the most consequential of the three — this is the
    idiomatic file write in modern Python. It reaches io.open."""
    _skip_without(CSV)
    with pytest.raises(AssertionError):
        pathlib.Path(CSV).write_text("x")
    assert _attempted(write_trap, CSV)


def test_pathlib_write_bytes_is_caught(write_trap):
    """Same route as write_text, and the form actually used in this codebase at
    src/reports.py:1760 (against a gitignored target, so not a defect there)."""
    _skip_without(CSV)
    with pytest.raises(AssertionError):
        pathlib.Path(CSV).write_bytes(b"x")
    assert _attempted(write_trap, CSV)


def test_os_replace_is_caught(write_trap, tmp_path):
    """MISSED before #271. The atomic-write idiom — write a temp file, then
    replace — is the RECOMMENDED pattern for exactly this kind of file, so its
    absence from the trap was the gap most likely to be walked into."""
    _skip_without(CSV)
    src = tmp_path / "src.csv"
    src.write_text("a\n")
    with pytest.raises(AssertionError):
        os.replace(src, CSV)
    assert _attempted(write_trap, CSV)


def test_pandas_to_csv_is_caught(write_trap):
    _skip_without(CSV)
    with pytest.raises(AssertionError):
        pd.DataFrame({"a": [1]}).to_csv(CSV)
    assert _attempted(write_trap, CSV)


def test_pandas_to_parquet_is_caught(write_trap):
    """Predicted to be the GAP, on the theory that pyarrow opens files in C. It
    does not — it routes through builtins.open and was caught all along. Pinned
    because the prediction was wrong in the direction that matters: reasoning
    about which surface a library uses is not a substitute for measuring it."""
    _skip_without(PARQUET)
    with pytest.raises(AssertionError):
        pd.DataFrame({"a": [1]}).to_parquet(PARQUET)
    assert _attempted(write_trap, PARQUET)


# ── the claim that does not decay ─────────────────────────────────────────────

def test_an_unenumerated_surface_is_invisible_to_the_trap(write_trap, tmp_path):
    """THE ARGUMENT FOR THE BACKSTOP, PROVEN RATHER THAN ASSERTED.

    os.open + os.write is a raw file-descriptor write. It goes through none of the
    four surfaces write_trap patches, so the trap records NOTHING — and would
    refuse nothing. The target here is a scratch file, because the point is the
    trap's blindness, not a real write.

    This test passing means the trap has a hole. That is the finding. The hole is
    covered by the content backstop in tests/conftest.py, which watches bytes
    instead of call surfaces — see test_the_content_backstop_is_installed.
    """
    scratch = tmp_path / "probe.bin"
    fd = os.open(scratch, os.O_WRONLY | os.O_CREAT)
    try:
        os.write(fd, b"written via a surface nothing patches")
    finally:
        os.close(fd)

    assert scratch.read_bytes(), "the raw write did not happen; test proves nothing"
    assert not write_trap, (
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
