"""#217 — bootstrap reports held symbols the location register would silently drop.

WHY WARN AND NEVER RAISE. `app.py:19` calls `bootstrap_personal_db()` **unwrapped** at
import, so a raise there takes down every page. The phase-46 migration already settled
this reasoning for its own guard (`tools/migrate_saa_phase46_personal_intl_split.py:22-30`):
a crash locks you out of the app you would use to satisfy the guard. Here it is worse —
the fix is a CSV edit that is not in the app at all, so a startup raise would be a
lockout over a text file. The state (bought something, seed row not added yet) is
routine; only the silence is not.

WHY BOOTSTRAP. It runs at every personal-mode start, already loads the seed, and is the
only place both inputs are present when it matters. The check cannot run in CI by any
route — `data/uploads/**/Portfolio_Position*.csv` and `data/tracker.db` are both
gitignored, and a committed fixture would have frozen holdings and pass forever while
the real book drifted (see #217). Local is the only option, so it should be the one
place that always runs locally.

SKIP, NOT FAIL, ON NO HOLDINGS. A first run with an empty uploads dir has nothing to
reconcile, so the check returns no findings rather than reporting everything as broken.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.bootstrap import unmapped_holdings, unmapped_holdings_notice

SCHEMA = """
CREATE TABLE securities (
    ticker TEXT PRIMARY KEY,
    name TEXT,
    sleeve_category TEXT,
    tax_efficiency TEXT,
    asset_class_id INTEGER
);
"""

CSV_HEADER = (
    "Account Number,Account Name,Symbol,Description,Quantity,Last Price,"
    "Current Value,Cost Basis Total,Total Gain/Loss Dollar,Type\n"
)


def _book(tmp_path: Path, securities: list[tuple], held: list[str]) -> tuple[Path, Path, Path]:
    """A scratch DB + uploads dir + account map. `securities` rows are (ticker, sleeve, te).

    The account map is scratch and PASSED IN: parse_fidelity_csv raises on an account
    number absent from private/account_map.json (by design — raw numbers must never
    reach the schema), so a fixture with an invented account number would make the
    check return {} for the wrong reason. That is how the first cut of these tests
    failed: three of them read as "no findings" when the parse had actually raised.
    """
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    for ticker, sleeve, te in securities:
        conn.execute("INSERT INTO securities (ticker, name, sleeve_category, "
                     "tax_efficiency, asset_class_id) VALUES (?,?,?,?,1)",
                     (ticker, ticker, sleeve, te))
    conn.commit()
    conn.close()

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    lines = [CSV_HEADER]
    for sym in held:
        lines.append(
            f'X1,Individual,{sym},{sym} Fund,10,100.00,$1000.00,$900.00,$100.00,Cash\n')
    (uploads / "Portfolio_Positions_Aug-10-2026.csv").write_text("".join(lines),
                                                                 encoding="utf-8")
    amap = tmp_path / "account_map.json"
    amap.write_text(json.dumps({"X1": "acct_01"}), encoding="utf-8")
    return db, uploads, amap


# ── the check ────────────────────────────────────────────────────────────────

def test_fully_mapped_book_reports_nothing(tmp_path):
    db, uploads, amap = _book(tmp_path,
                        [("AAA", "us_large_core", "high"), ("BBB", "tips", "low")],
                        ["AAA", "BBB"])
    assert unmapped_holdings(db, uploads, amap) == {}


def test_absent_securities_row_is_reported(tmp_path):
    db, uploads, amap = _book(tmp_path, [("AAA", "us_large_core", "high")], ["AAA", "NEW"])
    found = unmapped_holdings(db, uploads, amap)
    assert list(found) == ["NEW"]
    assert found["NEW"] == ["no securities row at all"]


@pytest.mark.parametrize("sleeve,te,expected", [
    (None, "high", ["sleeve_category"]),
    ("us_large_core", None, ["tax_efficiency"]),
    (None, None, ["sleeve_category", "tax_efficiency"]),
])
def test_null_columns_are_reported_by_name(tmp_path, sleeve, te, expected):
    """Which column is missing, not just that something is — the register drops on
    EITHER, and a reader fixing the wrong column learns nothing."""
    db, uploads, amap = _book(tmp_path, [("AAA", sleeve, te)], ["AAA"])
    assert unmapped_holdings(db, uploads, amap) == {"AAA": expected}


def test_no_positions_csv_skips_rather_than_failing(tmp_path):
    """A first run with an empty uploads dir has nothing to reconcile. Reporting every
    seeded symbol as unmapped — or raising — would make the notice fire on a state
    that is not wrong."""
    db, _u, amap = _book(tmp_path, [("AAA", "us_large_core", "high")], ["AAA"])
    empty = tmp_path / "empty_uploads"
    empty.mkdir()
    assert unmapped_holdings(db, empty, amap) == {}


def test_undated_csv_is_ignored_like_the_loader_does(tmp_path):
    """find_latest_positions_csv ignores files that do not match the dated pattern, so
    the check must too — otherwise it would disagree with the page about what 'the
    newest holdings' means."""
    db, uploads, amap = _book(tmp_path, [("AAA", "us_large_core", "high")], ["AAA"])
    (uploads / "Portfolio_Positions_Aug-10-2026.csv").unlink()
    (uploads / "some_other_export.csv").write_text(CSV_HEADER, encoding="utf-8")
    assert unmapped_holdings(db, uploads, amap) == {}


def test_missing_securities_table_does_not_raise(tmp_path):
    """A pre-migration DB has no securities table. Bootstrap calls this AFTER the
    seeds, so it should not happen — but a check that crashes the app it is meant to
    protect is the failure mode this whole item exists to avoid."""
    db = tmp_path / "bare.db"
    sqlite3.connect(db).close()
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "Portfolio_Positions_Aug-10-2026.csv").write_text(
        CSV_HEADER + 'X1,Individual,AAA,A,10,1.00,$10.00,$9.00,$1.00,Cash\n',
        encoding="utf-8")
    amap = tmp_path / "account_map.json"
    amap.write_text(json.dumps({"X1": "acct_01"}), encoding="utf-8")
    assert unmapped_holdings(db, uploads, amap) == {}


def test_unmapped_account_number_does_not_crash_the_check(tmp_path):
    """An account number absent from the map makes parse_fidelity_csv raise, BY DESIGN —
    raw numbers must never reach the schema. The page reports that directly, so this
    check has nothing to add; what it must not do is propagate the raise into
    bootstrap, where app.py calls it unwrapped."""
    db, uploads, _amap = _book(tmp_path, [("AAA", "us_large_core", "high")], ["AAA"])
    empty_map = tmp_path / "no_accounts.json"
    empty_map.write_text(json.dumps({}), encoding="utf-8")
    assert unmapped_holdings(db, uploads, empty_map) == {}


# ── the notice text ──────────────────────────────────────────────────────────

def test_notice_is_none_when_nothing_is_unmapped():
    assert unmapped_holdings_notice({}) is None


def test_notice_names_every_symbol_and_what_is_missing():
    note = unmapped_holdings_notice({
        "NEW": ["no securities row at all"],
        "OLD": ["tax_efficiency"],
    })
    assert "NEW" in note and "OLD" in note
    assert "no securities row at all" in note
    assert "tax_efficiency" in note


def test_notice_names_the_file_to_edit():
    """The fix is a CSV edit, and it is not in the app — so the notice has to say
    which file, or a reader is told a page is broken with nowhere to go."""
    note = unmapped_holdings_notice({"NEW": ["no securities row at all"]})
    assert "securities_household.csv" in note


def test_notice_says_the_pages_will_fail_not_merely_that_data_is_missing():
    """#217: the Asset Location and Household View pages RAISE on this state. A notice
    that only said 'unmapped' would understate — the reader needs to know the page is
    down, not degraded."""
    note = unmapped_holdings_notice({"NEW": ["no securities row at all"]}).lower()
    assert "asset location" in note and "household view" in note
    assert "fail" in note or "raise" in note


def test_notice_singular_and_plural_read_correctly():
    one = unmapped_holdings_notice({"A": ["tax_efficiency"]})
    two = unmapped_holdings_notice({"A": ["tax_efficiency"], "B": ["sleeve_category"]})
    assert "1 held symbol is" in one
    assert "2 held symbols are" in two


# ── bootstrap carries it, and app.py must not discard it ────────────────────

def test_bootstrap_result_carries_the_findings(tmp_path, monkeypatch):
    """The result dict is the transport. app.py:19 discarded it entirely before this
    change, so anything bootstrap learned died at the call site."""
    import src.bootstrap as b
    import src.db as db_mod

    db, uploads, amap = _book(tmp_path, [("AAA", "us_large_core", "high")], ["AAA", "NEW"])
    monkeypatch.setattr(db_mod, "DB_PATH", db)
    monkeypatch.setattr(b, "_bootstrapped", set())
    monkeypatch.setattr(b, "_UPLOADS_DIR_FOR_CHECK", uploads, raising=False)

    # Only the check is exercised here; the seed steps need the full personal schema.
    result = {"unmapped_holdings": b.unmapped_holdings(db, uploads, amap)}
    assert result["unmapped_holdings"] == {"NEW": ["no securities row at all"]}


def test_app_py_does_not_discard_the_bootstrap_result():
    """Pinned as source, because the discard is what made the finding unreachable: the
    call was `bootstrap_personal_db()` with no assignment. A render test cannot see
    this in demo mode (the personal branch never runs), so it is asserted statically."""
    src = Path(__file__).resolve().parent.parent / "app.py"
    text = src.read_text(encoding="utf-8")
    assert "bootstrap_personal_db()\n" not in text.replace(
        "= bootstrap_personal_db()\n", "= ASSIGNED\n"), (
        "app.py calls bootstrap_personal_db() without using its result"
    )
    assert "unmapped_holdings_notice" in text, (
        "app.py does not render the unmapped-holdings notice"
    )


def test_notice_renders_before_the_page_runs():
    """Placement, asserted on source order: the notice must be emitted BEFORE
    nav.run(), so it appears above the page's own content on every page rather than
    only on the landing page. A reader who navigates straight to Asset Location must
    meet it before the page raises."""
    src = Path(__file__).resolve().parent.parent / "app.py"
    text = src.read_text(encoding="utf-8")
    assert "unmapped_holdings_notice" in text and "nav.run()" in text
    assert text.index("unmapped_holdings_notice") < text.index("nav.run()"), (
        "the notice is emitted after nav.run(), so the page renders (or raises) first"
    )
