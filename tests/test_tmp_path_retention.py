"""A passing test's tmp_path goes when the test ends (#713).

The suite's tests copy whole books into their tmp_path. Under pytest's default
retention, "all", every one of those copies stayed until the session ended: 16.0 GB at
e6b2725. pytest.ini sets tmp_path_retention_policy = failed, which cut the peak to 2.6
GB. These tests hold that setting, and check that the pinned pytest does what its
reference says the setting does, in a session of its own.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_suite_keeps_a_tmp_path_only_for_a_test_that_failed(request):
    assert request.config.getini("tmp_path_retention_policy") == "failed"


def test_this_pytest_removes_a_passing_tests_directory_and_keeps_a_failing_ones(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pytest.ini").write_text("[pytest]\ntmp_path_retention_policy = failed\n",
                                     encoding="utf-8")
    (proj / "test_inner.py").write_text(
        "def test_passes(tmp_path):\n"
        "    (tmp_path / 'copy.db').write_bytes(b'x' * 1024)\n"
        "\n"
        "def test_fails(tmp_path):\n"
        "    (tmp_path / 'copy.db').write_bytes(b'x' * 1024)\n"
        "    assert False\n",
        encoding="utf-8")
    base = tmp_path / "base"
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
         f"--basetemp={base}", "test_inner.py"],
        cwd=proj, env=env, capture_output=True, text=True, timeout=120)
    assert "1 failed, 1 passed" in run.stdout, run.stdout + run.stderr
    # On Linux pytest also links "<name>current" to the newest numbered directory
    # (_force_symlink, _pytest/pathlib.py); Windows makes no link. A link is not a kept
    # directory, and is_dir() follows it, so links are left out.
    kept = sorted(p.name for p in base.iterdir() if p.is_dir() and not p.is_symlink())
    assert kept == ["test_fails0"], kept
    assert (base / "test_fails0" / "copy.db").exists()
