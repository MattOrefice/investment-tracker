"""Clear the Real Assets PARENT row's benchmark spec in data/demo.db, and remove the false
gap it put into every committed quarter lock (#459).

The parent row (parent_id NULL) carried 'VNQ+DBC', a legacy unweighted spec; the sleeve
under it carries the weighted 'VNQ (60%) + DBC (40%)'. Nothing reads a parent's benchmark
but the lock's ticker list (cache._get_all_snapshot_tickers expands every DISTINCT spec),
which could not parse it and disclosed it on every lock's cover as a series "that could
not be fetched". No price was ever missing: VNQ and DBC are locked as holdings and as
the sleeve's legs. src/seed_saa.py already writes None for the parent.

Two guarded steps, one transaction, rolled back unless each changes exactly what it names:
  1. the parent row's spec, keyed on its name, parent_id NULL and its current text;
  2. each lock's payload: its "gaps" list, when it holds exactly that one entry, becomes
     empty. Done on the stored text, so every other byte of the payload is unchanged,
     and checked by parsing: the payload before and after differ in "gaps" alone.
A second run changes nothing. Targets data/demo.db explicitly and runs from main() only;
the frozen book is rebuilt from it (tools/build_frozen_book.py). The personal book's
parent rows carry no spec and it holds no locks.
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"

PARENT = "Real Assets"
LEGACY_SPEC = "VNQ+DBC"

CLEAR_SPEC = ("UPDATE asset_classes SET benchmark_ticker = NULL WHERE name = ? "
              "AND parent_id IS NULL AND benchmark_ticker = ?")
SET_PAYLOAD = ("UPDATE quarter_snapshots SET snapshot_data = ? WHERE quarter_id = ? "
               "AND snapshot_data = ?")


def without_the_gap(text: str) -> "str | None":
    """The payload with its one LEGACY_SPEC gap removed, or None if it has none. Raises
    if the gaps list holds anything else, rather than guess what to keep."""
    payload = json.loads(text)
    gaps = payload.get("gaps") or []
    if not gaps:
        return None
    if len(gaps) != 1 or gaps[0][0] != LEGACY_SPEC:
        raise SystemExit(f"ABORT: unexpected gaps {[g[0] for g in gaps]}")
    old = '"gaps": ' + json.dumps(gaps)
    if text.count(old) != 1:
        raise SystemExit(f"ABORT: the gaps entry appears {text.count(old)} times as text")
    new = text.replace(old, '"gaps": []')
    after = json.loads(new)
    if after.pop("gaps") != [] or {k: v for k, v in payload.items() if k != "gaps"} != after:
        raise SystemExit("ABORT: the payload changed beyond its gaps")
    return new


def main() -> int:
    conn = sqlite3.connect(DEMO_DB)
    specs = locks = 0
    try:
        with conn:
            row = conn.execute("SELECT benchmark_ticker FROM asset_classes WHERE name = ? AND "
                               "parent_id IS NULL", (PARENT,)).fetchall()
            if len(row) != 1:
                raise SystemExit(f"ABORT: {len(row)} '{PARENT}' parent rows")
            if row[0][0] is not None:
                n = conn.execute(CLEAR_SPEC, (PARENT, LEGACY_SPEC)).rowcount
                if n != 1:
                    raise SystemExit(f"ABORT: the parent spec update changed {n} rows, not 1")
                specs += 1
            for qid, text in conn.execute(
                    "SELECT quarter_id, snapshot_data FROM quarter_snapshots ORDER BY "
                    "quarter_id").fetchall():
                new = without_the_gap(text)
                if new is None:
                    continue
                n = conn.execute(SET_PAYLOAD, (new, qid, text)).rowcount
                if n != 1:
                    raise SystemExit(f"ABORT: {qid}'s payload update changed {n} rows, not 1")
                locks += 1
    finally:
        conn.close()
    print(f"{DEMO_DB.name}: {specs} parent spec(s) cleared, {locks} lock gap(s) removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
