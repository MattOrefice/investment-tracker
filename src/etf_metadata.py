"""The ETF metadata file, data/etf_metadata.json: each bond fund's duration, and AGG's for
the Bloomberg US Agg, with its measure, source and as-of date (#455, #462).

A quarter lock snapshots it as the input ``etf_metadata`` (input_lock.ETF_METADATA) and
takes it only when every ``as_of`` falls inside the quarter (#389). It also held the
equity style box's fact-sheet figures until the style box was withdrawn (#468): they had
no recorded source.
"""
from __future__ import annotations

import json
from pathlib import Path

META_PATH = Path(__file__).resolve().parent.parent / "data" / "etf_metadata.json"


def load_metadata() -> dict:
    """The metadata as a quarter lock holds it inside the lock (#382), else the file."""
    from src.input_lock import ETF_METADATA, locked
    held = locked(ETF_METADATA)
    if held is not None:
        return json.loads(json.dumps(held))
    with open(META_PATH) as f:
        return json.load(f)
