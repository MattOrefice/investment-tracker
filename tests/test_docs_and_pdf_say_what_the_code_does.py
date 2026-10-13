"""The docs and the PDF's Price Data line say what the code does (#482).

Three statements had outlived the code they described:

  * README.md, docs/architecture.md and docs/operational_checks.md credited CAPE to
    Yale and gave data/shiller_cape.csv a 30-day TTL. src/shiller.py reads multpl.com
    first, Yale's file is the fallback and has been stale since September 2023, and the
    CSV is a committed input that only tools/refresh_market_data.py writes;
  * data/cache/README.md called data/etf_metadata.json fact-sheet figures for the style
    box. Since #477 it holds fund durations only;
  * the PDF's Price Data entry said a later price update cannot shift the report's
    numbers. Macro Context and Asset Evaluation are built outside the lock, and a
    locked figure has moved by restatement, each named on a cover.

docs/post_launch_backlog.md stays as it was: it is a record of what was known then.
"""
from __future__ import annotations

import inspect
import json
import os
import re
import shutil
from pathlib import Path

import pytest
from markupsafe import escape

from tests.conftest import FROZEN_BOOK, pin_today, point_at_frozen_book, unpin_leftovers

ROOT = Path(__file__).resolve().parent.parent
BOOKS = [ROOT / "data" / "demo.db", FROZEN_BOOK]
CAPE_DOCS = ("README.md", "docs/architecture.md", "docs/operational_checks.md")
LIVE = ("Macro Context and Asset Evaluation are not locked: they are computed each time "
        "the report is generated.")


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ── where CAPE comes from ─────────────────────────────────────────────────────

@pytest.mark.parametrize("doc", CAPE_DOCS)
def test_the_doc_credits_cape_to_multpl_with_yale_as_the_stale_fallback(doc):
    text = _read(doc)
    assert "multpl.com" in text, f"{doc} does not name multpl.com"
    # By sentence, not by line: a sentence wraps where its file wraps it.
    yale = [s for s in re.split(r"(?<=\.)\s", " ".join(text.split())) if "Yale" in s]
    assert yale, f"premise: {doc} still says what Yale's file is"
    for sentence in yale:
        assert "fallback" in sentence, (
            f"{doc} names Yale without calling it the fallback: {sentence!r}")
    assert "stale since September 2023" in text, doc
    for gone in ("Shiller / Yale", "Yale/Shiller", "CAPE from Yale", "dataset at Yale",
                 "Robert Shiller / Yale"):
        assert gone not in text, f"{doc} still credits CAPE to Yale: {gone!r}"


@pytest.mark.parametrize("doc", CAPE_DOCS)
def test_the_doc_calls_the_cape_file_a_committed_input_with_no_ttl(doc):
    text = _read(doc)
    assert "30-day TTL" not in text, doc
    assert "shiller_cape.csv" in text and "no TTL" in text, doc
    flat = " ".join(text.split())
    assert re.search(r"[Cc]ommitted (input|CSV)", flat), doc
    assert "tools/refresh_market_data.py" in text, f"{doc} does not name the file's writer"


def test_the_docs_claims_are_the_loaders_own():
    """What the three docs now say, read from src/shiller.py: multpl is asked first,
    Yale only when it fails, and the reader of the committed file never fetches."""
    from src import shiller
    assert "multpl.com" in shiller._MULTPL_URL and "yale.edu" in shiller._YALE_URL
    fetch = inspect.getsource(shiller.fetch_cape_dataframe)
    assert 0 <= fetch.index("_MULTPL_URL") < fetch.index("_YALE_URL"), (
        "the fetch no longer asks multpl.com before Yale")
    assert "fallback" in shiller.SOURCE_NAMES[shiller.YALE]
    assert "stale at Sep 2023" in shiller.__doc__
    reader = inspect.getsource(shiller.get_cape_series)
    assert "requests" not in reader and "_URL" not in reader, (
        "the reader of the committed file fetches")


def test_the_operational_check_names_the_refresh_that_exists():
    """It told the reader to press a Force Refresh button for CAPE. That button
    re-fetches FRED only, and says so in its own help text."""
    text = " ".join(_read("docs/operational_checks.md").split())
    assert "python tools/refresh_market_data.py --files cape pe" in text
    assert "re-fetches FRED only" in text
    assert "force-refresh via" not in text
    macro_page = " ".join(_read("pages/3_Macro.py").split())
    assert '"Force refresh"' in macro_page
    assert "CAPE and trailing P/E are committed inputs" in macro_page
    assert "asof.MARKET_DATA_STALE_DAYS_VALUATION" in text
    from src import asof
    assert isinstance(asof.MARKET_DATA_STALE_DAYS_VALUATION, int)


def test_the_backlog_stays_as_history():
    """A record of what was known when it was written, kept on purpose."""
    assert ("Data ends Sep 2023 (Yale file last updated at that point)"
            in _read("docs/post_launch_backlog.md"))


# ── what data/etf_metadata.json holds ─────────────────────────────────────────

def test_the_cache_readme_says_the_metadata_file_holds_fund_durations_only():
    text = " ".join(_read("data/cache/README.md").split())
    assert "`data/etf_metadata.json` (fund durations only:" in text
    assert "fact-sheet figures for the style box" not in text
    assert "the style box renders as pending" not in text
    assert "the report's duration line renders as pending" in text
    entries = {k: v for k, v in json.loads(_read("data/etf_metadata.json")).items()
               if not k.startswith("_")}
    assert entries, "premise: the file holds entries"
    for ticker, entry in entries.items():
        assert set(entry) == {"duration_years", "duration_measure", "duration_source",
                              "as_of"}, (ticker, sorted(entry))
    assert "AGG" in entries, "premise: AGG's duration stands for the Bloomberg US Agg"


# ── the PDF's Price Data line ─────────────────────────────────────────────────

@pytest.fixture(params=BOOKS, ids=lambda p: p.name)
def book(request, tmp_path, monkeypatch):
    import src.db as db
    copy = tmp_path / request.param.name
    shutil.copyfile(request.param, copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    return copy


def _locked_quarters() -> "list[str]":
    from src.db import get_connection
    conn = get_connection()
    try:
        return [r[0] for r in conn.execute(
            "SELECT quarter_id FROM quarter_snapshots ORDER BY quarter_id")]
    finally:
        conn.close()


def test_every_committed_lock_gets_the_same_three_statements(book):
    from src.cache import get_quarter_snapshot, price_data_lock_note
    from src.input_lock import DIVIDENDS
    quarters = _locked_quarters()
    assert len(quarters) >= 5, quarters
    for q in quarters:
        snap = get_quarter_snapshot(q)[0]
        assert DIVIDENDS in snap.inputs, (q, "premise: the lock holds dividends")
        assert price_data_lock_note(snap, "Sep 25, 2026") == (
            "Quarter prices locked Sep 25, 2026. The lock holds prices and dividends "
            "through the quarter's last day, and a locked figure changes only by a "
            "restatement the cover names. " + LIVE), q


def test_the_line_follows_the_lock(book):
    """The contrast: a lock from before inputs were locked holds prices alone and the
    line says so; with no lock, or no lock date, there is no line."""
    from src.cache import UNLOCKED_SECTIONS, get_quarter_snapshot, price_data_lock_note
    snap = get_quarter_snapshot("2026Q2")[0]
    bare = price_data_lock_note(snap._replace(inputs=None, inputs_pending=None),
                                "Sep 25, 2026")
    assert "The lock holds prices through the quarter's last day" in bare
    assert "dividends" not in bare
    assert price_data_lock_note(None, "Sep 25, 2026") is None
    assert price_data_lock_note(snap, None) is None
    for name in UNLOCKED_SECTIONS:
        assert name in bare


@pytest.fixture(scope="module")
def q2_report(tmp_path_factory):
    """The frozen book's committed Q2 2026 report, as rendered text."""
    from src import reports
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
        mp.setattr(reports, "_render_pdf", lambda html: html.encode("utf-8"))
        pin_today(mp)
        point_at_frozen_book(mp, tmp_path_factory.mktemp("q2-price-data"))
        html = reports.generate_quarterly_report_bytes("2026-03-31", "2026-06-30",
                                                       is_demo=True).decode("utf-8")
    unpin_leftovers()
    return " ".join(html.split())


def test_the_report_prints_the_line_once_under_price_data(q2_report):
    locked = re.search(r"Prices locked ([^<]+?)</p>", q2_report)
    assert locked, "premise: the cover prints the lock's date"
    line = str(escape(
        f"Quarter prices locked {locked.group(1).strip()}. The lock holds prices and "
        "dividends through the quarter's last day, and a locked figure changes only by a "
        "restatement the cover names. " + LIVE))
    assert q2_report.count(line) == 1
    entry = q2_report.split("<dt>Price Data</dt>", 1)[1].split("</dd>", 1)[0]
    assert line in entry


def test_nothing_says_a_later_price_update_cannot_shift_the_report(q2_report):
    for claim in ("cannot shift this report", "a later price update"):
        assert claim not in q2_report, claim
        assert claim not in _read("templates/quarterly_report.html"), claim
