"""Rebuild the demo's DRIP lots from its ledger, priced at the actual close (#406 item 12).

The demo's committed DRIP lots were a fixture, computed once and never regenerated.
By 2026-09-27 they no longer described the book: priced at adj_close, which
dividend_adjusted re-anchors on every later dividend; two pay dates that fell on
market holidays reinvested at the previous session's close; May 2026's distribution
written twice for VGIT and SCHP; nothing after 2026-05-06; no lots at all for AVDV,
AVIV and IDHQ; and VEA's computed on shares the re-dated Phase 39 sale had already
sold. This removes them and derives every lot again through the write path
(src.drip.backfill_all_drip_lots), from the committed trades, prices and dividends
through the committed price frontier, with no fetch.

Before anything is written, positions must still close: no sale may exceed the
shares held on its date, and a position the ledger closes may keep no residual
beyond tax_lots' #364 tolerance. Either aborts.

A locked quarter holds prices and inputs, not trades, so its report reads the
rebuilt lots. Each lock whose quarter-end holdings changed records the rebuild
(cache.record_lot_rebuild), and its report says so on the cover. Nothing else in a
lock changes.

Idempotent: when the committed lots already equal the derivation, nothing is
written. Run it after the demo's committed prices advance (the quarterly close-out);
tests/test_demo_drip_lots.py fails until it has been.

Targets data/demo.db explicitly (never get_db_path), offline. main() only, so
bootstrap never runs it.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"
sys.path.insert(0, str(ROOT))

# Two derivations of the same data by the same code agree exactly; this only absorbs
# a platform's last-bit difference, and is far below any share or price grain.
REL_TOL = 1e-12

_LOTS_SQL = ("SELECT ticker, trade_date, shares, price FROM trades "
             "WHERE lot_source = 'drip' AND account_id = ? ORDER BY ticker, trade_date")


def _refuse(*_a, **_k):
    raise OSError("offline: DRIP lots are derived from committed data only")


@contextmanager
def _offline():
    """No fetch inside the block: gap fills off and the price session refused. Both
    are restored after, so a test calling in leaves the process as it found it."""
    import src.prices as prices
    saved = (prices._GAP_FETCH, prices._SESSION.get)
    prices.set_gap_fetch(False)
    prices._SESSION.get = _refuse
    try:
        yield
    finally:
        prices.set_gap_fetch(saved[0])
        prices._SESSION.get = saved[1]


@contextmanager
def _pointed_at(path: Path):
    """Every read and write of src.db goes to ``path`` inside the block."""
    import src.db as db
    saved = (db.DB_PATH, db._migrated_paths, db._RUNTIME_CACHE)
    db.DB_PATH, db._migrated_paths, db._RUNTIME_CACHE = path, set(), None
    try:
        yield
    finally:
        db.DB_PATH, db._migrated_paths, db._RUNTIME_CACHE = saved


def _account_and_window() -> "tuple[int, str, str]":
    from src.holdings import committed_price_frontier, get_inception_date, get_portfolio_account_id
    acct = get_portfolio_account_id()
    frontier = committed_price_frontier(account_id=acct)
    if frontier is None:
        raise SystemExit("ABORT: no committed price frontier: the book has no priced holdings.")
    return acct, get_inception_date(account_id=acct), frontier


def read_lots(path: Path) -> "list[tuple]":
    """(ticker, date, shares, price) for every DRIP lot in ``path``'s portfolio account."""
    with _pointed_at(path):
        acct = _account_and_window()[0]
    con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        return [tuple(r) for r in con.execute(_LOTS_SQL, (acct,))]
    finally:
        con.close()


def _replace_lots(path: Path, expected_removed: "int | None" = None) -> None:
    """In ``path``: delete the account's DRIP lots and derive them all through the write
    path, through the committed frontier. ``src.db`` must already point at ``path``."""
    from src.drip import backfill_all_drip_lots
    acct, inception, frontier = _account_and_window()
    con = sqlite3.connect(path)
    try:
        cur = con.execute("DELETE FROM trades WHERE lot_source = 'drip' AND account_id = ?",
                          (acct,))
        if expected_removed is not None and cur.rowcount != expected_removed:
            con.rollback()
            raise SystemExit(f"ABORT: DELETE matched {cur.rowcount} DRIP rows, expected "
                             f"{expected_removed}. Rolled back.")
        con.commit()
    finally:
        con.close()
    results = backfill_all_drip_lots(inception, frontier, account_id=acct)
    skipped = {t: r for t, r in results.items() if r.skipped and r.status != "no_distributions"}
    if skipped:
        raise SystemExit(f"ABORT: the derivation skipped {sorted(skipped)}: {skipped}")


def derive_lots(source: Path) -> "list[tuple]":
    """What the write path derives from ``source``'s committed trades, prices and
    dividends through its frontier: on a scratch copy, offline. ``source`` is not
    touched."""
    tmp = Path(tempfile.mkdtemp(prefix="drip-derive-"))
    try:
        copy = tmp / "book.db"
        shutil.copyfile(source, copy)
        os.chmod(copy, 0o644)
        with _offline(), _pointed_at(copy):
            _replace_lots(copy)
        return read_lots(copy)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def lots_differ(a: "list[tuple]", b: "list[tuple]") -> "list[str]":
    """Every lot on which ``a`` and ``b`` disagree, keyed on (ticker, date). A key held
    twice on one side is itself a difference: a lot written twice is not one lot."""
    from collections import Counter
    out = []
    for side, lots in (("left", a), ("right", b)):
        for key, n in Counter((t, d) for t, d, _s, _p in lots).items():
            if n > 1:
                out.append(f"{key[0]} {key[1]}: {n} lots on the {side}")
    da = {(t, d): (s, p) for t, d, s, p in a}
    db_ = {(t, d): (s, p) for t, d, s, p in b}
    for key in sorted(set(da) | set(db_)):
        if key not in db_:
            out.append(f"{key[0]} {key[1]}: only on the left")
        elif key not in da:
            out.append(f"{key[0]} {key[1]}: only on the right")
        else:
            for name, x, y in zip(("shares", "price"), da[key], db_[key]):
                if abs(x - y) > REL_TOL * max(abs(x), abs(y)):
                    out.append(f"{key[0]} {key[1]}: {name} {x!r} vs {y!r}")
    return out


def positions_that_do_not_close(path: Path) -> "list[str]":
    """Sales that exceed the shares held on their date, and positions the ledger
    closes that keep a residual, with DRIP lots counted. Tolerance: tax_lots' #364."""
    from src.tax_lots import _SHARE_TOLERANCE as TOL
    with _pointed_at(path):
        acct = _account_and_window()[0]
    con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT ticker, trade_date, LOWER(action), shares, COALESCE(lot_source, 'initial') "
            "FROM trades WHERE account_id = ? AND ticker != 'SPAXX' "
            "ORDER BY ticker, trade_date, trade_id", (acct,)).fetchall()
    finally:
        con.close()
    out, held, ledger = [], {}, {}
    for ticker, day, action, shares, source in rows:
        sign = 1.0 if action == "buy" else -1.0
        held[ticker] = held.get(ticker, 0.0) + sign * shares
        if source != "drip":
            ledger[ticker] = ledger.get(ticker, 0.0) + sign * shares
        if held[ticker] < -TOL:
            out.append(f"{ticker} {day}: sale leaves {held[ticker]!r} shares held")
    for ticker, n in ledger.items():
        if abs(n) <= TOL and abs(held[ticker]) > TOL:
            out.append(f"{ticker}: the ledger closes it, {held[ticker]!r} shares remain")
    return out


def quarters_whose_holdings_changed(old: "list[tuple]", new: "list[tuple]",
                                    locks: "list[tuple[str, str]]") -> "list[str]":
    """The locked quarters (quarter_id, last day) whose lots held at the quarter's end
    differ between ``old`` and ``new``."""
    return [qid for qid, end in locks
            if lots_differ([l for l in old if l[1] <= end], [l for l in new if l[1] <= end])]


def main() -> int:
    if not DEMO_DB.exists():
        print(f"ABORT: {DEMO_DB} not found.")
        return 1
    os.environ.setdefault("DEMO_DAILY_FETCH", "0")
    with _offline():
        return _main()


def _main() -> int:
    from src.asof import today_et
    from src.cache import record_lot_rebuild

    committed = read_lots(DEMO_DB)
    derived = derive_lots(DEMO_DB)
    if not lots_differ(committed, derived):
        print(f"demo.db's {len(committed)} DRIP lots already equal the derivation: "
              "nothing to do.")
        return 0

    # Positions must close on the rebuilt book before the committed one is touched.
    tmp = Path(tempfile.mkdtemp(prefix="drip-check-"))
    try:
        copy = tmp / "book.db"
        shutil.copyfile(DEMO_DB, copy)
        os.chmod(copy, 0o644)
        with _pointed_at(copy):
            _replace_lots(copy)
        problems = positions_that_do_not_close(copy)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if problems:
        print("ABORT: with the rebuilt lots, positions do not close:")
        for p in problems:
            print("  " + p)
        return 1

    con = sqlite3.connect(f"file:{DEMO_DB.resolve().as_posix()}?mode=ro", uri=True)
    locks = con.execute("SELECT quarter_id, snapshot_date FROM quarter_snapshots "
                        "ORDER BY quarter_id").fetchall()
    con.close()
    moved = quarters_whose_holdings_changed(committed, derived, locks)

    with _pointed_at(DEMO_DB):
        _replace_lots(DEMO_DB, expected_removed=len(committed))
        written = read_lots(DEMO_DB)
        mismatch = lots_differ(written, derived)
        if mismatch:
            print("ABORT: the lots written differ from the derivation; restore "
                  "data/demo.db with git checkout:")
            for m in mismatch[:20]:
                print("  " + m)
            return 1
        today = today_et()
        recorded = [qid for qid in moved if record_lot_rebuild(qid, today)]

    print(f"removed {len(committed)} DRIP lots, wrote {len(written)}, each at the raw "
          f"close on its reinvestment date.")
    print(f"locks recording the rebuild: {', '.join(recorded) or 'none'}"
          f"{'' if recorded == moved else f' (already recorded: {sorted(set(moved) - set(recorded))})'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
