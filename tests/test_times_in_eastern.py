"""Every rendered time is New York time, labelled ET (2026-09-25 demo audit, item 2).

The banner's fetch and retry lines, the FRED retry captions, Macro's "Last updated"
and the PDF's dates were UTC, or, for "Last updated", the server's clock with no zone
at all. The public demo's server runs on UTC, so its own clock is four or five hours
ahead of the market and a day ahead after 8 PM ET.

ET is a named zone, not an offset: September is EDT (UTC-4), January is EST (UTC-5),
and the label is "ET" for both.
"""
from __future__ import annotations

import os
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import src.asof as asof

ROOT = Path(__file__).resolve().parent.parent
UTC = timezone.utc


@pytest.mark.parametrize("t, text", [
    (datetime(2026, 9, 26, 0, 4, tzinfo=UTC), "September 25, 2026 at 8:04 PM ET"),   # EDT
    (datetime(2026, 9, 25, 16, 46, tzinfo=UTC), "September 25, 2026 at 12:46 PM ET"),
    (datetime(2026, 1, 15, 17, 5, tzinfo=UTC), "January 15, 2026 at 12:05 PM ET"),    # EST
    (datetime(2026, 1, 15, 5, 5, tzinfo=UTC), "January 15, 2026 at 12:05 AM ET"),
    (datetime(2026, 3, 8, 6, 59, tzinfo=UTC), "March 8, 2026 at 1:59 AM ET"),         # EST ...
    (datetime(2026, 3, 8, 7, 0, tzinfo=UTC), "March 8, 2026 at 3:00 AM ET"),          # ... EDT
    ("2026-09-23T21:05+00:00", "September 23, 2026 at 5:05 PM ET"),                   # ISO
    (datetime(2026, 9, 25, 22, 30, tzinfo=UTC), "September 25, 2026 at 6:30 PM ET"),
])
def test_a_time_renders_in_new_york_with_the_et_label(t, text):
    assert asof._when(t) == text


def test_the_zone_is_named_not_a_fixed_offset():
    assert asof.ET.key == "America/New_York"
    summer = datetime(2026, 7, 1, 12, tzinfo=UTC).astimezone(asof.ET).utcoffset()
    winter = datetime(2026, 1, 1, 12, tzinfo=UTC).astimezone(asof.ET).utcoffset()
    assert (summer.total_seconds(), winter.total_seconds()) == (-4 * 3600, -5 * 3600)


def test_no_rendered_time_says_utc_est_or_edt():
    for t in (datetime(2026, 9, 26, 0, 4, tzinfo=UTC), datetime(2026, 1, 15, 17, 5, tzinfo=UTC)):
        s = asof._when(t)
        assert s.endswith(" ET") and not any(z in s for z in ("UTC", "EST", "EDT"))


def test_the_pdf_dates_are_new_york_dates():
    """At 10 PM ET on September 25 the UTC date is already the 26th."""
    from src.reports import report_dates
    ten_pm_et = datetime(2026, 9, 26, 2, 0, tzinfo=UTC)
    assert asof.today_et(ten_pm_et) == date(2026, 9, 25)
    generated, locked = report_dates("2026-07-01T02:30:00+00:00", now=ten_pm_et)
    assert generated == "September 25, 2026"
    assert locked == "June 30, 2026", "10:30 PM ET on June 30 locked June 30, not July 1"
    assert report_dates(None, now=ten_pm_et) == ("September 25, 2026", None)


def test_a_new_lock_records_an_aware_utc_moment(tmp_path, monkeypatch):
    """So its date converts to New York correctly whatever machine reads it."""
    from src import reports
    from src.cache import capture_quarter_snapshot
    from tests.conftest import pin_today, point_at_frozen_book
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    pin_today(monkeypatch)
    point_at_frozen_book(monkeypatch, tmp_path)
    _snap, captured_at = capture_quarter_snapshot("2026Q2")
    assert datetime.fromisoformat(captured_at).utcoffset().total_seconds() == 0


def test_macros_last_updated_line_names_new_york_time(tmp_path, monkeypatch):
    import streamlit as st
    import src.config as config
    import src.db as db
    import src.macro as macro
    import src.prices as prices
    copy = tmp_path / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(config, "IS_DEMO", True)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(prices._SESSION, "get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(macro, "fetch_fred_series",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("FRED down")))
    st.cache_data.clear()
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "pages" / "3_Macro.py"), default_timeout=600).run()
    st.cache_data.clear()
    assert not at.exception, [str(e.value) for e in at.exception]
    line = next(c.value for c in at.caption if str(c.value).startswith("Last updated: "))
    stamp = line.split(". ")[0].removeprefix("Last updated: ")
    assert stamp.endswith(" ET") and " at " in stamp and "UTC" not in stamp, line
    assert stamp.split(" at ")[0] == asof.format_long_date(asof.today_et()), line
