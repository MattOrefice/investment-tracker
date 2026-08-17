"""PR 3 — the banner is the ONLY thing that changes on any page.

PR 2 could lean on "the three control digests hold" as its guard: markers were
supposed to appear in one condition and not the other, so an unchanged control
render proved the disclosure was keyed on the gap. PR 3 has no such check —
the banner renders on every page in every condition, and today's state is 2 rather
than 1, so all six digests move. This file is the replacement guard.

It compares each page against ITSELF with the legacy clock-read banner patched
back in, and requires every differing element to be a banner element. No stored
baseline, no diff to read by eye, and it fails rather than reports.
"""
import os
import shutil
import socket
import sqlite3
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
TRACKER_DB = ROOT / "data" / "tracker.db"
KILL = ("VOO", "VEA")

# Any element carrying one of these is a banner element. The legacy render emits
# the first; the new one emits one of the rest.
BANNER_TOKENS = ("Live data as of", "Prices through", "No committed price data")

# Pages that render an as-of banner: the two that can now supply a coverage record,
# and one that cannot — the nine-page case must be covered too, since that is where
# a coverage claim would be fabricated.
PAGES = ["1_SAA.py", "2_Performance.py", "8_Research.py"]


def _skip_without_personal_inputs():
    from src.household_data import find_latest_positions_csv
    if (find_latest_positions_csv() is None or not TRACKER_DB.exists()
            or not (ROOT / "private" / "account_map.json").exists()):
        pytest.skip("personal-mode inputs absent")


def _scratch(tmp_path, mode):
    db = tmp_path / f"{mode}.db"
    shutil.copyfile(TRACKER_DB, db)
    os.chmod(db, 0o644)
    if mode == "partial":
        conn = sqlite3.connect(db)
        conn.execute(
            f"DELETE FROM prices WHERE ticker IN ({','.join('?' * len(KILL))})", KILL)
        conn.commit()
        conn.close()
    return db


def _stream(page, db, monkeypatch, legacy: bool):
    """Render one page and return its text elements. With legacy=True the banner is
    the old pure clock read, so the two runs differ ONLY in the banner."""
    import requests

    import src.asof as asof
    import src.db
    import src.prices
    import streamlit as st

    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: (_ for _ in ()).throw(socket.gaierror("blocked")))
    monkeypatch.setattr(src.prices._SESSION, "get",
                        lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("blocked")))
    monkeypatch.setattr(src.db, "DB_PATH", db)
    monkeypatch.setattr(src.db, "_migrated_paths", set())
    src.prices._reset_trailing_memo()

    if legacy:
        monkeypatch.setattr(
            asof, "as_of_live_line",
            lambda today=None, **_kw: f"Live data as of {asof.format_long_date(today or date.today())}.")

    # st.cache_data outlives an AppTest instance and is keyed on the as-of date,
    # not on the database or the patched banner behind it.
    st.cache_data.clear()
    st.cache_resource.clear()

    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "pages" / page), default_timeout=300).run()
    assert not at.exception, f"{page} raised (legacy={legacy}): {at.exception}"

    out = []
    for kind in ("warning", "error", "info", "success", "caption", "markdown",
                 "subheader", "title", "header"):
        out.extend(f"{kind}|{el.value}" for el in getattr(at, kind, []))
    for m in at.metric:
        out.append(f"metric|{m.label}|{m.value}|{m.delta}")
    return out


@pytest.mark.parametrize("page", PAGES)
@pytest.mark.parametrize("mode", ["control", "partial"])
def test_banner_is_the_only_element_that_changes(page, mode, tmp_path, monkeypatch):
    _skip_without_personal_inputs()
    import difflib

    before = _stream(page, _scratch(tmp_path, mode), monkeypatch, legacy=True)
    monkeypatch.undo()
    after = _stream(page, _scratch(tmp_path, mode), monkeypatch, legacy=False)

    changed = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=before, b=after).get_opcodes():
        if tag == "equal":
            continue
        changed.extend(before[i1:i2] + after[j1:j2])

    assert changed, f"{page}/{mode}: the banner did not change at all"
    for el in changed:
        assert any(tok in el for tok in BANNER_TOKENS), (
            f"{page}/{mode}: a NON-banner element changed — this PR must touch "
            f"nothing but the banner: {el[:200]!r}"
        )


@pytest.mark.parametrize("page", PAGES)
def test_partial_banner_differs_from_control_banner(page, tmp_path, monkeypatch):
    """Non-vacuity: a banner that renders the same text in both conditions would
    satisfy the test above (the banner "changed" versus legacy in each) while
    disclosing nothing. Pages with a coverage record must differ between
    conditions; the page without one must NOT, since it cannot see the gap."""
    _skip_without_personal_inputs()

    def banner_of(mode):
        stream = _stream(page, _scratch(tmp_path, mode), monkeypatch, legacy=False)
        monkeypatch.undo()
        return [e for e in stream if any(t in e for t in BANNER_TOKENS)]

    control, partial = banner_of("control"), banner_of("partial")
    assert control and partial, f"{page}: no banner element found"
    if page == "8_Research.py":
        assert control == partial, (
            "a page with no coverage record must report the same vintage in both "
            f"conditions — it cannot see the gap: {control} vs {partial}")
    else:
        assert control != partial, (
            f"{page}: the banner is identical with two holdings unpriced: {control}")
