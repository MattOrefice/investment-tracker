"""Give each committed quarter lock the book's SAA policy, recorded today (#525).

A lock holds prices, dividends and its provider inputs, and since #525 the book's
policy: everything a locked report reads from asset_classes and the ticker-to-sleeve
mapping (src/policy.py). A lock taken from now holds the policy as it stands when the
lock is taken. The eleven locks committed before that (six in data/demo.db, five in
the frozen test book) held none, so their reports read the book's policy at each render,
and a target changed today would have moved every one of them.

This gives each lock the book's own policy as it stands on RECORDED_ON, marked as
recorded after the quarter locked, never as the quarter's own. The cover says so
(cache.policy_recorded_note). No figure moves: the policy recorded is the one every
report already reads, and tests/test_locked_figures_golden.py passes unchanged.

The policy goes where a lock keeps its inputs: inputs["policy"], with the day it was
held in inputs_through["policy"]. One transaction per book, rolled back unless every
lock changes exactly as named:
  * the book holds exactly the locks named below, and each holds inputs but no policy;
    a lock that already holds one is left alone, so a second run changes nothing;
  * its payload round-trips through JSON byte for byte;
  * the policy given is the book's own, read in the same transaction, and every lock
    in the book is given the same one;
  * the new payload parses to the old one plus exactly those two entries, and with
    them removed it is the old text, every byte;
  * the UPDATE is keyed on the quarter and the payload's current text, and changes one
    row.

    python tools/hold_policy_in_demo_locks.py           # data/demo.db
    python tools/hold_policy_in_demo_locks.py frozen    # tests/fixtures/frozen_book.db

The frozen book holds five of the same locks and is changed in place by the same steps:
it cannot be rebuilt from demo.db without moving everything else in it. The personal
book holds no committed lock; its first lock will hold its policy when taken.
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEMO_DB = ROOT / "data" / "demo.db"
FROZEN_BOOK = ROOT / "tests" / "fixtures" / "frozen_book.db"
BOOKS = {"demo": DEMO_DB, "frozen": FROZEN_BOOK}

POLICY = "policy"
RECORDED_ON = "2026-10-14"
# Every lock each book holds.
QUARTERS = {DEMO_DB.name: ("2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2", "2026Q3"),
            FROZEN_BOOK.name: ("2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2")}
SET_PAYLOAD = ("UPDATE quarter_snapshots SET snapshot_data = ? WHERE quarter_id = ? "
               "AND snapshot_data = ?")


def without_policy(text: str) -> str:
    """A payload with its held policy removed, as the text it was before: the inverse
    of with_policy. A payload with none is returned as it is."""
    blob = json.loads(text)
    if POLICY not in (blob.get("inputs") or {}):
        return text
    del blob["inputs"][POLICY]
    del blob["inputs_through"][POLICY]
    return json.dumps(blob)


def with_policy(text: str, held: dict) -> "str | None":
    """The payload with ``held`` (src.policy.encode's form) as its policy, or None if it
    holds one already. Raises SystemExit on anything unexpected, rather than guess."""
    blob = json.loads(text)
    if json.dumps(blob) != text:
        raise SystemExit("ABORT: a payload does not round-trip through JSON")
    if "inputs" not in blob or "inputs_through" not in blob:
        raise SystemExit("ABORT: a lock holds no inputs; a lock from before #382 is not "
                         "one this tool expects")
    if POLICY in blob["inputs"] or POLICY in blob["inputs_through"]:
        return None
    blob["inputs"][POLICY] = held
    blob["inputs_through"][POLICY] = held["held_on"]
    new = json.dumps(blob)

    # Nothing else changed, twice over: parsed, the old payload plus the two entries;
    # as text, the old payload once they are removed.
    want = json.loads(text)
    want["inputs"][POLICY] = held
    want["inputs_through"][POLICY] = held["held_on"]
    if json.loads(new) != want:
        raise SystemExit("ABORT: the payload changed beyond the policy")
    if without_policy(new) != text:
        raise SystemExit("ABORT: removing the policy does not give back the old payload")
    return new


def main(book: "Path | None" = None, quarters: "tuple | None" = None) -> int:
    """``quarters``, the locks the book must hold: QUARTERS' entry for a committed book
    by default. A test's copy names its own."""
    from src import policy
    book = Path(book or DEMO_DB)
    expected = tuple(quarters) if quarters is not None else QUARTERS.get(book.name)
    conn = sqlite3.connect(book)
    changed = 0
    try:
        with conn:
            held = policy.encode(policy.read_book(conn), RECORDED_ON, recorded_after_lock=True)
            if not held["asset_classes"] or not held["securities"]:
                raise SystemExit(f"ABORT: {book.name} holds no policy to record")
            rows = dict(conn.execute("SELECT quarter_id, snapshot_data FROM quarter_snapshots"))
            if expected is None or sorted(rows) != sorted(expected):
                raise SystemExit(f"ABORT: {book.name} holds the locks {sorted(rows)}, not "
                                 f"{sorted(expected or ())}")
            for qid in expected:
                new = with_policy(rows[qid], held)
                if new is None:
                    continue
                n = conn.execute(SET_PAYLOAD, (new, qid, rows[qid])).rowcount
                if n != 1:
                    raise SystemExit(f"ABORT: {qid}'s payload update changed {n} rows, not 1")
                changed += 1
    finally:
        conn.close()
    print(f"{book.name}: {changed} lock(s) given the SAA policy as it stood on {RECORDED_ON}")
    return 0


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "demo"
    if which not in BOOKS:
        raise SystemExit(f"usage: hold_policy_in_demo_locks.py [{' | '.join(BOOKS)}]")
    raise SystemExit(main(BOOKS[which]))
