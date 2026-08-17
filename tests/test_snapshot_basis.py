"""#193 — a locked snapshot must not serve adj_close under the name `close`.

Written BEFORE the implementation. Three defects were bundled in the issue; this
covers two of them (the third, coverage, is #204):

  A  the WRITER captures one basis (cache.py:116 stores df["adj_close"] only), so a
     raw-close consumer cannot be served from a snapshot at all
  B  the READER papers over that by returning the one series under BOTH column
     names, converting "cannot serve this" into "serves something wrong"

The writer is not wrong for its stated purpose and the cover claim is incomplete
rather than false. The aliasing is the defect.

WHY THE LEGACY SNAPSHOT IS A FIXTURE HERE. The three real rows are being deleted so
that no single-basis snapshot survives to be mis-served. That removes the only
instance of the state the raise exists for — so the raise path would go untested the
moment the PR lands. The fixture keeps it tested forever, and it is also the only
honest way to test it: after the deletion there is nothing in either database that
looks like a legacy snapshot.

R2 is the approved shape: the reader serves only the columns it actually holds, and
`total_return_series` encapsulates the adj_close-with-close-fallback that fourteen
sites currently open-code. Primary-basis readers keep `p["close"]` and get a
KeyError inside a legacy snapshot; fallback readers stop touching close at all. The
fourteen per-site verdicts (drip.py:283 correct to fill, risk.py:135 manufacturing a
return spike) stay deferred — the helper becomes their single home.
"""
import json
import sqlite3

import pandas as pd
import pytest

AS_OF = "2025-06-30"
QUARTER = "2025Q2"


# ── fixtures: a legacy single-basis snapshot, and a modern two-basis one ───────

def _single_basis_frame():
    """What every stored snapshot looks like today: ONE series per ticker, holding
    adj_close, with no way to tell that is what it holds."""
    return pd.DataFrame(
        {"VOO": [563.0345, 564.10], "SCHP": [25.8388, 25.90]},
        index=pd.to_datetime(["2025-06-27", "2025-06-30"]).date,
    )


def _two_basis_frames():
    """What A should capture: TWO FLAT FRAMES, one per basis.

    Not a column MultiIndex. A MultiIndex would need to survive
    to_json(orient="split") round-tripping, and defending that is resting a durable
    artifact on a pandas behaviour upstream does not guarantee across the open
    pandas>=2.2.0 range — the same argument that kept the coverage record off
    .attrs. Two frames under top-level keys serialize with no round-trip question,
    are readable by inspection, and make legacy detection trivial: a legacy
    snapshot is one whose blob has no `close` key, rather than one whose
    deserialized frame has the wrong `nlevels`.
    """
    from src.cache import SnapshotFrames
    idx = pd.to_datetime(["2025-06-27", "2025-06-30"]).date
    return SnapshotFrames(
        adj_close=pd.DataFrame({"VOO": [563.0345, 564.10], "SCHP": [25.8388, 25.90]}, index=idx),
        close=pd.DataFrame({"VOO": [568.03, 569.20], "SCHP": [26.68, 26.75]}, index=idx),
    )


# ── B: a legacy snapshot must refuse `close`, not alias it ────────────────────

def test_a_legacy_blob_is_detected_by_its_missing_close_key(tmp_path, monkeypatch):
    """Legacy detection is a key lookup on the blob, not structural inspection of a
    deserialized frame. A pre-existing snapshot is a bare split-orient frame with no
    `close`/`adj_close` keys at all."""
    import json as _json
    import sqlite3 as _sq

    import src.cache as cache
    import src.db

    db = tmp_path / "legacy.db"
    conn = _sq.connect(db)
    conn.execute("CREATE TABLE quarter_snapshots (quarter_id TEXT PRIMARY KEY, "
                 "snapshot_date TEXT, captured_at TEXT, snapshot_data BLOB NOT NULL)")
    legacy_blob = _single_basis_frame().to_json(orient="split", date_format="iso")
    conn.execute("INSERT INTO quarter_snapshots VALUES (?,?,?,?)",
                 (QUARTER, AS_OF, "2026-05-09T13:33:19", legacy_blob))
    conn.execute("CREATE TABLE trades (account_id INTEGER, ticker TEXT, "
                 "trade_date TEXT, shares REAL, action TEXT, lot_source TEXT)")
    conn.execute("CREATE TABLE prices (ticker TEXT, price_date TEXT, close REAL, "
                 "adj_close REAL)")
    conn.execute("CREATE TABLE securities (ticker TEXT, asset_class_id INTEGER)")
    conn.execute("CREATE TABLE asset_classes (asset_class_id INTEGER PRIMARY KEY, "
                 "name TEXT, parent_id INTEGER, target_weight REAL, "
                 "tolerance_band REAL, benchmark_ticker TEXT, rationale TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(src.db, "DB_PATH", db)
    monkeypatch.setattr(src.db, "_migrated_paths", set())

    frames, captured = cache.get_quarter_snapshot(QUARTER)
    assert captured == "2026-05-09T13:33:19"
    assert frames.close is None, (
        "a legacy blob with no close key was read as though it had one")
    assert "VOO" in frames.adj_close.columns


def test_legacy_snapshot_does_not_serve_close(monkeypatch):
    """The defect, stated as a test. Today the reader returns
    {"close": filtered.values, "adj_close": filtered.values} — the same numbers
    under both names — so a caller asking for the account's worth silently receives
    a total-return series instead."""
    import src.prices
    from src.cache import snapshot_price_context

    with snapshot_price_context(_single_basis_frame()):
        frame = src.prices.get_prices("VOO", "2025-06-01", AS_OF)
        assert "close" not in frame.columns, (
            "a single-basis snapshot served a `close` column it does not have — "
            f"columns={list(frame.columns)}"
        )


def test_primary_basis_read_raises_inside_a_legacy_snapshot():
    """The consumer-visible half: the PDF Holdings Market Value column reads
    p["close"], and inside a legacy snapshot that must fail loudly rather than
    return adjusted numbers under a "Prices locked" cover."""
    import src.prices
    from src.cache import snapshot_price_context

    with snapshot_price_context(_single_basis_frame()):
        frame = src.prices.get_prices("VOO", "2025-06-01", AS_OF)
        with pytest.raises(KeyError):
            frame["close"]


def test_adj_close_is_still_served_from_a_legacy_snapshot():
    """Non-vacuity, and the whole reason B is not simply "raise on legacy": the
    total-return basis IS present and legitimately usable. Refusing everything would
    remove the feature rather than harden it."""
    import src.prices
    from src.cache import snapshot_price_context

    with snapshot_price_context(_single_basis_frame()):
        frame = src.prices.get_prices("VOO", "2025-06-01", AS_OF)
        assert "adj_close" in frame.columns
        assert float(frame["adj_close"].iloc[-1]) == pytest.approx(564.10)


# ── A: a fresh capture must store both bases ──────────────────────────────────

def test_a_two_basis_snapshot_serves_close_and_adj_close_distinctly():
    import src.prices
    from src.cache import snapshot_price_context

    with snapshot_price_context(_two_basis_frames()):
        frame = src.prices.get_prices("SCHP", "2025-06-01", AS_OF)
        assert {"close", "adj_close"} <= set(frame.columns)
        assert float(frame["close"].iloc[-1]) == pytest.approx(26.75)
        assert float(frame["adj_close"].iloc[-1]) == pytest.approx(25.90)
        assert float(frame["close"].iloc[-1]) != float(frame["adj_close"].iloc[-1]), (
            "the two bases came back identical — the aliasing is still in place"
        )


def test_capture_stores_both_bases(tmp_path, monkeypatch):
    """A, at the writer. cache.py:116 keeps only df["adj_close"]; a snapshot that
    stores one basis is the state B has to refuse, so the writer must stop
    producing it."""
    import src.cache as cache
    import src.db

    db = tmp_path / "cap.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE securities (ticker TEXT, asset_class_id INTEGER)")
    conn.execute("INSERT INTO securities VALUES ('VOO', 1)")
    conn.execute("CREATE TABLE asset_classes (asset_class_id INTEGER PRIMARY KEY, "
                 "name TEXT, parent_id INTEGER, target_weight REAL, "
                 "tolerance_band REAL, benchmark_ticker TEXT, rationale TEXT)")
    # get_connection() runs _auto_migrate on first touch, which inspects these.
    conn.execute("CREATE TABLE trades (account_id INTEGER, ticker TEXT, "
                 "trade_date TEXT, shares REAL, action TEXT, lot_source TEXT)")
    conn.execute("CREATE TABLE prices (ticker TEXT, price_date TEXT, close REAL, "
                 "adj_close REAL)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(src.db, "DB_PATH", db)
    monkeypatch.setattr(src.db, "_migrated_paths", set())

    def _fake_get_prices(ticker, start, end=None):
        idx = pd.to_datetime(["2025-06-27", "2025-06-30"]).date
        return pd.DataFrame({"close": [568.03, 569.20],
                             "adj_close": [563.0345, 564.10]}, index=idx)

    monkeypatch.setattr(cache._prices_module, "get_prices", _fake_get_prices)

    snap_df, _captured = cache.capture_quarter_snapshot(QUARTER)

    assert snap_df.close is not None, "capture stored no close basis at all"
    assert "VOO" in snap_df.close.columns and "VOO" in snap_df.adj_close.columns, (
        f"capture stored a single basis: close={None if snap_df.close is None else list(snap_df.close.columns)} "
        f"adj_close={list(snap_df.adj_close.columns)}")


def test_a_captured_snapshot_round_trips_both_bases(tmp_path, monkeypatch):
    """Both bases must survive the JSON blob. A plain assertion that the two keys
    come back — NOT a defence of an index structure. Two flat frames under
    top-level keys have no round-trip question to defend."""
    import src.cache as cache
    import src.db

    db = tmp_path / "rt.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE securities (ticker TEXT, asset_class_id INTEGER)")
    conn.execute("INSERT INTO securities VALUES ('VOO', 1)")
    conn.execute("CREATE TABLE asset_classes (asset_class_id INTEGER PRIMARY KEY, "
                 "name TEXT, parent_id INTEGER, target_weight REAL, "
                 "tolerance_band REAL, benchmark_ticker TEXT, rationale TEXT)")
    # get_connection() runs _auto_migrate on first touch, which inspects these.
    conn.execute("CREATE TABLE trades (account_id INTEGER, ticker TEXT, "
                 "trade_date TEXT, shares REAL, action TEXT, lot_source TEXT)")
    conn.execute("CREATE TABLE prices (ticker TEXT, price_date TEXT, close REAL, "
                 "adj_close REAL)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(src.db, "DB_PATH", db)
    monkeypatch.setattr(src.db, "_migrated_paths", set())
    monkeypatch.setattr(cache._prices_module, "get_prices",
                        lambda t, s, e=None: pd.DataFrame(
                            {"close": [568.03], "adj_close": [563.0345]},
                            index=pd.to_datetime(["2025-06-30"]).date))

    cache.capture_quarter_snapshot(QUARTER)
    reread, _ = cache.get_quarter_snapshot(QUARTER)
    assert reread is not None
    assert reread.close is not None, "the close basis did not survive the blob"
    assert "VOO" in reread.close.columns and "VOO" in reread.adj_close.columns


# ── the fallback helper: fourteen sites' behaviour, in one place ──────────────

def test_total_return_series_fills_adj_close_from_close():
    """The fourteen open-coded `adj_close.fillna(close)` sites move here verbatim.
    This pins the behaviour they have TODAY so the migration is mechanical and the
    per-site verdicts stay deferred — drip.py:283 is right to fill, risk.py:135
    manufactures a return spike, and neither is decided by this PR."""
    from src.prices import total_return_series

    frame = pd.DataFrame(
        {"close": [100.0, 101.0, 102.0], "adj_close": [99.0, None, 101.5]},
        index=pd.to_datetime(["2025-06-26", "2025-06-27", "2025-06-30"]).date,
    )
    out = total_return_series(frame)
    assert list(out) == pytest.approx([99.0, 101.0, 101.5]), (
        "the middle value must come from close, exactly as adj_close.fillna(close) does"
    )


def test_total_return_series_works_without_a_close_column():
    """The point of the helper: a legacy single-basis snapshot has no close, and a
    total-return caller must not need one. This is what stops B's raise from
    breaking the fourteen fallback sites."""
    from src.prices import total_return_series

    frame = pd.DataFrame({"adj_close": [99.0, 100.0]},
                         index=pd.to_datetime(["2025-06-27", "2025-06-30"]).date)
    out = total_return_series(frame)
    assert list(out) == pytest.approx([99.0, 100.0])
