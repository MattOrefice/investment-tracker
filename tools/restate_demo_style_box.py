"""Restate every committed quarter lock in data/demo.db for the equity style box's
withdrawal (#468), as an error correction.

Each lock's ETF metadata input held the style box's fact-sheet figures for SPY, VOO, VTV,
SPHQ and AVUV, stamped April 15, 2026. They had no recorded source: SPY and VOO matched
in all five fields, and #468 found them far from the issuers' own. The style box is
withdrawn, so the five entries come out of each lock, and the removal is recorded in the
lock under input_corrections, as #386 recorded HYG's. The cover says so in one sentence
(cache.style_box_withdrawn_note).

One transaction, rolled back unless each lock changes exactly as named: its payload must
round-trip through JSON byte for byte, must hold all five entries (and no duration among
them), and after the edit must equal the old payload parsed, less those entries, plus the
record; the UPDATE is keyed on the quarter and the payload's current text. A lock already
restated is left alone, so a second run changes nothing. Targets data/demo.db explicitly
and runs from main() only; the frozen book is rebuilt from it (tools/build_frozen_book.py).
The personal book holds no committed locks.
"""
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"
sys.path.insert(0, str(ROOT))

INPUT = "etf_metadata"                      # input_lock.ETF_METADATA
EQUITY = ["SPY", "VOO", "VTV", "SPHQ", "AVUV"]
SET_PAYLOAD = ("UPDATE quarter_snapshots SET snapshot_data = ? WHERE quarter_id = ? "
               "AND snapshot_data = ?")


def restated(text: str, restated_on: date) -> "str | None":
    """The payload with the five equity entries removed and the removal recorded, or None
    if the lock was already restated. Raises SystemExit on anything unexpected."""
    blob = json.loads(text)
    if json.dumps(blob) != text:
        raise SystemExit("ABORT: a payload does not round-trip through JSON")
    if (blob.get("input_corrections") or {}).get(INPUT):
        return None
    data = blob["inputs"][INPUT]["data"]
    missing = [t for t in EQUITY if t not in data]
    if missing:
        raise SystemExit(f"ABORT: the lock's ETF metadata lacks {missing}")
    if any("duration_years" in data[t] for t in EQUITY):
        raise SystemExit("ABORT: an equity entry carries a duration")
    for t in EQUITY:
        del data[t]
    blob.setdefault("input_corrections", {})[INPUT] = {
        "restated_on": restated_on.isoformat(), "removed": list(EQUITY)}
    new = json.dumps(blob)

    # Nothing else changed: the old payload, less the five entries, plus the record.
    want = json.loads(text)
    for t in EQUITY:
        del want["inputs"][INPUT]["data"][t]
    want.setdefault("input_corrections", {})[INPUT] = blob["input_corrections"][INPUT]
    if json.loads(new) != want:
        raise SystemExit("ABORT: the payload changed beyond the five entries and the record")
    return new


def main(restated_on: "date | None" = None) -> int:
    from src.asof import today_et
    on = restated_on or today_et()
    conn = sqlite3.connect(DEMO_DB)
    changed = 0
    try:
        with conn:
            for qid, text in conn.execute("SELECT quarter_id, snapshot_data FROM "
                                          "quarter_snapshots ORDER BY quarter_id").fetchall():
                new = restated(text, on)
                if new is None:
                    continue
                n = conn.execute(SET_PAYLOAD, (new, qid, text)).rowcount
                if n != 1:
                    raise SystemExit(f"ABORT: {qid}'s payload update changed {n} rows, not 1")
                changed += 1
    finally:
        conn.close()
    print(f"{DEMO_DB.name}: {changed} lock(s) restated on {on.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
