"""Complete a committed quarter lock's pending inputs in data/demo.db.

A lock holds each input its sections read, or names it as pending when its data did not
cover the quarter then: French publishes a month's factors about a month late, and the
fund durations are entered by hand. The report path locks a pending input the first time
a report is generated after its data covers the quarter (cache.complete_quarter_inputs).
In the demo that write lands in each container's runtime cache, which a re-seed rebuilds
from whatever files are committed then, so the input would follow the committed files
until the completion itself is committed. This commits it.

One guarded step, and nothing is written unless it changes exactly what is named:
  * the completion is computed by cache.complete_quarter_inputs itself, on a scratch copy
    of the book with gap fetches off: the committed files and prices, never a fetch;
  * the stored payload must round-trip through JSON byte for byte;
  * exactly the inputs named on the command line move from pending to held. One whose
    data still does not cover the quarter refuses, and so does one that would complete
    without being named;
  * every other byte of the payload is unchanged: the new text must be the old payload
    with each named input added to "inputs" and "inputs_through" and taken out of
    "inputs_pending", and nothing else;
  * only that quarter's row changes: the UPDATE is keyed on the quarter and the payload's
    current text and must change one row, and every lock is compared with what it was
    before the commit.
A lock that already holds every input named is left alone, so a second run changes
nothing. Targets data/demo.db explicitly (never get_db_path) and runs from main() only.
The personal book's locks live in its own file, which the report path completes.

Usage:  python tools/complete_demo_lock_inputs.py 2026Q3 etf_metadata
        python tools/complete_demo_lock_inputs.py 2026Q3 ff5_us ff5_developed_exus umd
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"
sys.path.insert(0, str(ROOT))

LOCKS = ("SELECT quarter_id, snapshot_date, captured_at, snapshot_data FROM "
         "quarter_snapshots ORDER BY quarter_id")
SET_PAYLOAD = ("UPDATE quarter_snapshots SET snapshot_data = ? WHERE quarter_id = ? "
               "AND snapshot_data = ?")


def by_the_report_path(book: Path, quarter_id: str) -> str:
    """The quarter's stored payload after cache.complete_quarter_inputs has run on a
    scratch copy of ``book``, with gap fetches off. ``book`` itself is only read."""
    import src.db as db
    import src.prices as prices
    from src.cache import complete_quarter_inputs

    scratch = Path(tempfile.mkdtemp(prefix="complete_inputs_"))
    saved = (db.DB_PATH, db._migrated_paths, db._RUNTIME_CACHE, prices._GAP_FETCH)
    try:
        work = scratch / "book.db"
        shutil.copyfile(book, work)
        work.chmod(0o644)
        db.DB_PATH, db._migrated_paths, db._RUNTIME_CACHE = work, set(), None
        prices.set_gap_fetch(False)          # a read serves the scratch book, never a fetch
        complete_quarter_inputs(quarter_id)
        con = sqlite3.connect(work)
        try:
            return con.execute("SELECT snapshot_data FROM quarter_snapshots WHERE "
                               "quarter_id = ?", (quarter_id,)).fetchone()[0]
        finally:
            con.close()
    finally:
        db.DB_PATH, db._migrated_paths, db._RUNTIME_CACHE = saved[:3]
        prices.set_gap_fetch(saved[3])
        shutil.rmtree(scratch, ignore_errors=True)


def completed(text: str, names: "list[str]", new: str) -> "str | None":
    """``new``, the payload the report path wrote, once it is shown to be ``text`` with
    exactly ``names`` moved from pending to held; or None if the lock already holds them
    all. Raises SystemExit on anything else."""
    from src.input_lock import ALL_INPUTS

    blob = json.loads(text)
    if json.dumps(blob) != text:
        raise SystemExit("ABORT: the payload does not round-trip through JSON")
    unknown = sorted(set(names) - set(ALL_INPUTS))
    if unknown or not names or len(set(names)) != len(names):
        raise SystemExit(f"ABORT: name each input to complete once, from {list(ALL_INPUTS)}; "
                         f"got {names}")
    held, pending = blob.get("inputs") or {}, blob.get("inputs_pending") or {}
    if all(n in held and n not in pending for n in names):
        return None
    not_waiting = [n for n in names if n not in pending]
    if not_waiting:
        raise SystemExit(f"ABORT: the lock is not waiting on {not_waiting}; it waits on "
                         f"{list(pending)}")

    got = json.loads(new)
    still = got.get("inputs_pending") or {}
    moved = [n for n in got.get("inputs") or {} if n in pending and n not in still]
    short = [n for n in names if n not in moved]
    if short:
        raise SystemExit("ABORT: not complete, so nothing was written: "
                         + "; ".join(f"{n}'s data ends {still.get(n)}" for n in short))
    extra = [n for n in moved if n not in names]
    if extra:
        raise SystemExit(f"ABORT: {extra} would complete too and were not named; nothing "
                         f"was written")

    # Every other byte: the old payload with the named inputs moved, and nothing else.
    want = json.loads(text)
    for n in moved:
        want["inputs"][n] = got["inputs"][n]
        want["inputs_through"][n] = got["inputs_through"][n]
        del want["inputs_pending"][n]
    if json.dumps(want) != new:
        raise SystemExit("ABORT: the payload changed beyond the named inputs; nothing was "
                         "written")
    return new


def main(quarter_id: str, names: "list[str]", book: Path = DEMO_DB) -> int:
    con = sqlite3.connect(book)
    try:
        before = {r[0]: tuple(r[1:]) for r in con.execute(LOCKS)}
        if quarter_id not in before:
            raise SystemExit(f"ABORT: {book.name} holds no lock for {quarter_id}")
        text = before[quarter_id][2]
        new = completed(text, names, by_the_report_path(book, quarter_id))
        if new is None:
            print(f"{quarter_id}'s lock in {book.name} already holds {', '.join(names)}. "
                  f"Nothing was written.")
            return 0
        n = con.execute(SET_PAYLOAD, (new, quarter_id, text)).rowcount
        if n != 1:
            raise SystemExit(f"ABORT: {quarter_id}'s payload update changed {n} rows, not 1")
        after = {r[0]: tuple(r[1:]) for r in con.execute(LOCKS)}
        want = dict(before)
        want[quarter_id] = before[quarter_id][:2] + (new,)
        if after != want:
            raise SystemExit("ABORT: a lock other than the one named changed")
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()
    waits = list(json.loads(new).get("inputs_pending") or {})
    print(f"{quarter_id}'s lock in {book.name} now holds {', '.join(names)}"
          + (f"; it still waits on {', '.join(waits)}." if waits else "; it waits on nothing."))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__.split("Usage:")[1])
    raise SystemExit(main(sys.argv[1], sys.argv[2:]))
