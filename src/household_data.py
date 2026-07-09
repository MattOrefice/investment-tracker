"""Shared loader for the personal-mode household positions CSV.

Selects the most recent ``Portfolio_Positions_<Mon>-DD-YYYY>.csv`` in
``data/uploads/`` by the DATE IN THE FILENAME (not filesystem mtime), parses
it via ``parse_fidelity_csv``, and reports which file was chosen so the UI can
surface it. The Household View page and the test-suite both import from here
rather than hardcoding a dated filename.

Personal-mode only: the CSV lives in ``data/uploads/`` (gitignored) and is
resolved to pseudonyms via ``private/account_map.json`` inside
``parse_fidelity_csv``. Demo mode never ingests, so this is never imported there.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import NamedTuple

import pandas as pd

from src.ingestion.fidelity import parse_fidelity_csv

_REPO_ROOT   = Path(__file__).resolve().parents[1]
_UPLOADS_DIR = _REPO_ROOT / "data" / "uploads"

# Month abbreviations resolved explicitly (locale-independent — strptime("%b")
# would depend on the runner's locale, which CI does not pin).
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}

# Portfolio_Positions_Jul-08-2026.csv  ->  ('Jul', '08', '2026')
# Strict: a file that does not match this exact dated shape (e.g. a
# "… (1).csv" duplicate) is not a candidate and is ignored.
_NAME_RE = re.compile(r"^Portfolio_Positions_([A-Z][a-z]{2})-(\d{2})-(\d{4})\.csv$")


class LoadedPositions(NamedTuple):
    positions_df: pd.DataFrame
    path: Path
    as_of_date: date


def parse_date_from_name(name: str) -> date | None:
    """Return the date encoded in a positions filename, or None if it doesn't match.

    ``"Portfolio_Positions_Jul-08-2026.csv" -> date(2026, 7, 8)``.
    """
    m = _NAME_RE.match(name)
    if m is None:
        return None
    mon, dd, yyyy = m.group(1), int(m.group(2)), int(m.group(3))
    month = _MONTHS.get(mon)
    if month is None:
        return None
    try:
        return date(yyyy, month, dd)
    except ValueError:
        return None


def find_latest_positions_csv(uploads_dir: str | Path | None = None) -> Path | None:
    """Return the newest ``Portfolio_Positions_*.csv`` by filename date, or None.

    "Newest" is decided by the Mon-DD-YYYY date parsed from the filename, NOT by
    filesystem mtime (a re-downloaded older export must not win on mtime). Files
    whose names don't match the dated pattern are ignored. Ties break on the
    filename string for determinism.
    """
    d = Path(uploads_dir) if uploads_dir is not None else _UPLOADS_DIR
    if not d.exists():
        return None
    dated: list[tuple[date, str, Path]] = []
    for p in d.glob("Portfolio_Positions_*.csv"):
        parsed = parse_date_from_name(p.name)
        if parsed is not None:
            dated.append((parsed, p.name, p))
    if not dated:
        return None
    dated.sort(key=lambda t: (t[0], t[1]))
    return dated[-1][2]


def load_latest_positions(
    uploads_dir: str | Path | None = None,
    account_map_path: str | Path | None = None,
) -> LoadedPositions:
    """Parse the newest dated positions CSV in ``data/uploads/``.

    Returns a ``LoadedPositions`` (positions_df, path, as_of_date). Raises
    ``FileNotFoundError`` if no dated ``Portfolio_Positions_*.csv`` is present,
    so callers can replicate the prior warning + ``st.stop()`` behavior.
    """
    path = find_latest_positions_csv(uploads_dir)
    if path is None:
        d = Path(uploads_dir) if uploads_dir is not None else _UPLOADS_DIR
        raise FileNotFoundError(
            f"No Portfolio_Positions_<Mon>-DD-YYYY>.csv found in {d}."
        )
    positions_df = parse_fidelity_csv(path, account_map_path=account_map_path)
    as_of = parse_date_from_name(path.name)
    return LoadedPositions(positions_df, path, as_of)
