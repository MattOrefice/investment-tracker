"""Record, in each of the five quarter locks #469 rewrote, that its gaps list was
rewritten (#478).

Each of the five locks that existed before #469 carried one gap, 'VNQ+DBC': the lock's
ticker list could not parse the Real Assets parent row's legacy benchmark spec, and each
cover said one series "could not be fetched when prices were locked". No price was missing.
#469 (tools/migrate_demo_parent_benchmark.py, merged September 28, 2026) cleared the
spec and emptied each lock's gaps list, in data/demo.db and in the frozen test book.
That removed a disclosure from five covers and left nothing in the lock to say so.

This adds the record, as #477 recorded the style box's withdrawal: one key,
"gap_corrections", holding the day of the rewrite and the spec it removed. The cover
reads it (cache.gap_corrections_note).

One transaction per book, rolled back unless each of the five locks changes exactly as
named:
  * the lock exists, was captured before the rewrite, holds an empty gaps list and no
    such record yet;
  * its payload round-trips through JSON byte for byte;
  * the new payload is the old one, every byte, with the record appended as its last
    key, and parses to the old payload plus the record;
  * the UPDATE is keyed on the quarter and the payload's current text, and changes one
    row.
A lock that already carries the record is left alone, so a second run changes nothing.
A quarter locked since (2026Q3) never carried the gap and is not touched.

    python tools/restate_demo_gap_record.py           # data/demo.db
    python tools/restate_demo_gap_record.py frozen    # tests/fixtures/frozen_book.db

The frozen book holds the same five locks and is changed in place by the same steps: it
cannot be rebuilt from demo.db without moving everything else in it. The personal book
holds no committed locks.
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"
FROZEN_BOOK = ROOT / "tests" / "fixtures" / "frozen_book.db"
BOOKS = {"demo": DEMO_DB, "frozen": FROZEN_BOOK}

RECORD = "gap_corrections"
# The five locks #469 rewrote: every lock that existed on the day it merged.
QUARTERS = ("2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2")
RESTATED_ON = "2026-09-28"
REMOVED = ["VNQ+DBC"]
SET_PAYLOAD = ("UPDATE quarter_snapshots SET snapshot_data = ? WHERE quarter_id = ? "
               "AND snapshot_data = ?")


def recorded(text: str, captured_at: str) -> "str | None":
    """The payload with the record appended, or None if it already carries one. Raises
    SystemExit on anything unexpected, rather than guess."""
    blob = json.loads(text)
    if json.dumps(blob) != text:
        raise SystemExit("ABORT: a payload does not round-trip through JSON")
    if blob.get(RECORD):
        return None
    if str(captured_at)[:10] >= RESTATED_ON:
        raise SystemExit(f"ABORT: a lock captured {captured_at} was taken on or after the "
                         f"rewrite and never carried the gap")
    if blob.get("gaps") != []:
        raise SystemExit(f"ABORT: unexpected gaps {blob.get('gaps')!r}; the rewrite left "
                         "an empty list")
    record = {"restated_on": RESTATED_ON, "removed": list(REMOVED)}
    blob[RECORD] = record
    new = json.dumps(blob)

    # Nothing else changed, twice over: as text, the old payload less its closing brace
    # followed by the record; parsed, the old payload plus the record.
    if new != text[:-1] + ", " + json.dumps(RECORD) + ": " + json.dumps(record) + "}":
        raise SystemExit("ABORT: the new payload is not the old one with the record appended")
    want = json.loads(text)
    want[RECORD] = record
    if json.loads(new) != want:
        raise SystemExit("ABORT: the payload changed beyond the record")
    return new


def main(book: "Path | None" = None) -> int:
    book = Path(book or DEMO_DB)
    conn = sqlite3.connect(book)
    changed = 0
    try:
        with conn:
            rows = {q: (cap, text) for q, cap, text in conn.execute(
                "SELECT quarter_id, captured_at, snapshot_data FROM quarter_snapshots")}
            missing = [q for q in QUARTERS if q not in rows]
            if missing:
                raise SystemExit(f"ABORT: {book.name} holds no lock for {missing}")
            for qid in QUARTERS:
                captured_at, text = rows[qid]
                new = recorded(text, captured_at)
                if new is None:
                    continue
                n = conn.execute(SET_PAYLOAD, (new, qid, text)).rowcount
                if n != 1:
                    raise SystemExit(f"ABORT: {qid}'s payload update changed {n} rows, not 1")
                changed += 1
    finally:
        conn.close()
    print(f"{book.name}: {changed} lock(s) given the record of the {RESTATED_ON} rewrite")
    return 0


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "demo"
    if which not in BOOKS:
        raise SystemExit(f"usage: restate_demo_gap_record.py [{' | '.join(BOOKS)}]")
    raise SystemExit(main(BOOKS[which]))
