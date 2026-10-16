"""Every demo-mode process reads the committed demo.db through the read-only overlay, from
its first connection (#440, decided by the owner on October 16, 2026).

app.py entered the overlay with use_runtime_cache() before anything read, and a process
that imported src in demo mode without that call got a plain writable connection: a
price read's gap fill wrote 168 rows and 3 dividends into the committed demo.db. Now the
first connection enters the overlay whenever DB_PATH is still the committed demo.db
src.db chose at import, and a tool that means to write demo.db repoints DB_PATH, as each
already does.

Run in a subprocess, on a scratch copy standing in for the committed demo.db (src.config's
_ROOT points at a temporary directory before src.db is imported). In-process, the suite's
own redirect (tests/conftest.py) would catch a write to data/demo.db and let the test pass
whatever src.db did.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FROZEN = ROOT / "tests" / "fixtures" / "frozen_book.db"

SCRIPT = r'''
import json, sqlite3, sys
from pathlib import Path
root, repoint = Path(sys.argv[1]), sys.argv[2] == "repoint"
import src.config as config
config._ROOT = root
import src.db as db
import src.prices as prices
from tests.test_runtime_cache import _session_get
if repoint:
    db.DB_PATH = root / "data" / "demo.db"     # as lock_demo_quarters and the rest do
prices.set_gap_fetch(True)
prices._SESSION.get = _session_get
got = prices.get_prices("VOO", "2026-07-01", "2026-07-31")
cache = db._RUNTIME_CACHE
fetched = []
if cache is not None:
    con = sqlite3.connect(str(cache[1]))
    fetched = [r[0] for r in con.execute(
        "SELECT price_date FROM runtime_prices WHERE ticker = 'VOO' AND price_date > '2026-07-20'")]
    con.close()
print(json.dumps({"served_to": str(max(got.index)), "overlay": cache is not None,
                  "in_cache": fetched}))
'''


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(tmp_path: Path, mode: str) -> "tuple[dict, str, str]":
    root = tmp_path / mode
    (root / "data").mkdir(parents=True)
    book = root / "data" / "demo.db"
    shutil.copyfile(FROZEN, book)
    os.chmod(book, 0o644)
    before = _sha(book)
    env = {**os.environ, "TRACKER_MODE": "demo", "PYTHONPATH": str(ROOT),
           "DEMO_RUNTIME_CACHE": str(tmp_path / f"{mode}-cache.db"),
           "DEMO_DAILY_FETCH": "0", "DEMO_FRED_WARM": "0"}
    env.pop("FRED_API_KEY", None)
    r = subprocess.run([sys.executable, "-c", SCRIPT, str(root), mode], cwd=str(ROOT),
                       env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1]), before, _sha(book)


def test_a_demo_mode_price_read_with_gap_fetches_on_leaves_demo_db_byte_identical(tmp_path):
    """No use_runtime_cache() call: the read's gap fill lands in the runtime cache, and
    demo.db is byte-identical."""
    out, before, after = _run(tmp_path, "default")
    assert out["served_to"] == "2026-07-31", "premise: the read gap-filled past July 20"
    assert out["overlay"] and "2026-07-31" in out["in_cache"], out
    assert after == before


def test_a_tool_that_repoints_db_path_still_writes_demo_db(tmp_path):
    """The contrast, as lock_demo_quarters, restate_demo_hyg and migrate_demo_proxy_history
    do it: DB_PATH assigned to demo.db itself. The same read writes demo.db, and no
    overlay is entered, so the bytes moving here is what the test above rules out."""
    out, before, after = _run(tmp_path, "repoint")
    assert out["served_to"] == "2026-07-31"
    assert not out["overlay"] and out["in_cache"] == [], out
    assert after != before
