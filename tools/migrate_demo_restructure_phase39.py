"""Phase 39: rebalance the demo paper book into the four international sleeves.

The split gave the demo portfolio three strategic sleeves it holds nothing in.
pages/11 prices only tickers the portfolio HOLDS (11_Capital_Deployment.py:74
iterates holdings.index, not securities), so those sleeves have no priced ticker,
suggest_contributions skips them, and the Suggested $ invariant at
src/rebalance.py:267 fires before the page renders. Seeding prices alone does not
fix it — the book has to actually hold them.

WHY A TRIM AND NOT A RE-SEED
----------------------------
seed_paper_trades.seed(force=True) would produce a balanced book in one step, but
it runs DELETE FROM trades. The 49 DRIP rows are the only dividend-reinvestment
transactions in either database — the fixture that makes money-weighted vs
time-weighted return testable at all (test_bf_reconciles_*). Deleting a unique
fixture to fix a balance is not a trade worth making, so this models the actual
rebalance instead: VEA is trimmed and the proceeds buy the three tilt sleeves.

CASH-NEUTRAL BY CONSTRUCTION
---------------------------
The three buys are sized to their DB targets; the VEA sell is sized to exactly
fund them (proceeds == sum of buys, to the cent). All four trades carry the SAME
date, and get_external_cashflow_series sums flows per date — its docstring calls
out same-date buy/sell pairs as netting toward zero — so this registers no
external flow and the "inception seed only" invariant survives.

VEA lands slightly under its 7.08% target rather than exactly on it, because it
entered the rebalance at ~20.05% rather than the full 20.41% international
target. That residual is real drift, well inside the sleeve's +/-2% band, and is
left alone rather than papered over with a plug trade.

LOT ACCOUNTING
--------------
src/tax_lots.py is the authority: "The trades table is the lot ledger. Each BUY
row is one lot. SELLs are matched against open lots on a FIFO basis." The sell is
written as action='Sell' (get_lot_inventory matches on the lowercased string) and
consumes VEA's open lots oldest-first. VEA's DRIP lots are action='Buy' rows and
so are themselves lots eligible for FIFO matching; none are deleted or rewritten
here — only new rows are inserted.

DEMO ONLY
---------
Guarded on the filename. run_pending_migrations also runs against the personal
tracker.db, and writing paper trades into a real household ledger would corrupt
it. Everything else in this file is derived from the DB at run time (targets,
prices, VEA's live share count), so a rebuilt demo.db reproduces these rows
rather than depending on bytes that happen to be committed.

Idempotent: re-running finds the restructure note already present and returns.
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ACCOUNT_ID = 1
LOT_SOURCE = "Manual"
NOTE       = "Phase 39 restructure — international split"

# ticker -> sleeve it is bought to fill. Core is absent deliberately: VEA
# already holds it and is the funding source, not a purchase.
BUYS = {
    "IDHQ": "International Quality",
    "AVIV": "International Large Value",
    "AVDV": "International Small Value",
}
FUNDER = "VEA"


def _price_on(conn, ticker: str, as_of: str) -> float:
    row = conn.execute(
        "SELECT close FROM prices WHERE ticker = ? AND price_date <= ? "
        "ORDER BY price_date DESC LIMIT 1",
        (ticker, as_of),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"no price for {ticker} on/before {as_of}")
    return float(row[0])


def migrate_db(db_path) -> None:
    db_path = Path(db_path)
    if db_path.name != "demo.db" or not db_path.exists():
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if conn.execute("SELECT name FROM sqlite_master WHERE name='trades'").fetchone() is None:
            return
        if conn.execute("SELECT 1 FROM trades WHERE notes = ?", (NOTE,)).fetchone():
            return  # already applied

        # Trade date: the latest date every participating ticker can be priced on.
        as_of = min(
            conn.execute(
                "SELECT MAX(price_date) FROM prices WHERE ticker = ?", (t,)
            ).fetchone()[0]
            for t in [FUNDER, *BUYS]
        )
        if as_of is None:
            return

        targets = {
            r["name"]: r["target_weight"]
            for r in conn.execute(
                "SELECT name, target_weight FROM asset_classes "
                "WHERE parent_id IS NOT NULL AND target_weight > 0"
            )
        }
        if not all(s in targets for s in BUYS.values()):
            return  # split not applied to this DB yet

        # Invested value = market value of every non-cash holding, priced at as_of.
        held = conn.execute(
            """SELECT ticker,
                      SUM(CASE WHEN LOWER(action)='buy' THEN shares ELSE -shares END) AS sh
                 FROM trades WHERE trade_date <= ? GROUP BY ticker""",
            (as_of,),
        ).fetchall()
        invested = 0.0
        vea_shares = 0.0
        for r in held:
            if r["sh"] <= 0 or r["ticker"] == "SPAXX":
                continue
            invested += r["sh"] * _price_on(conn, r["ticker"], as_of)
            if r["ticker"] == FUNDER:
                vea_shares = float(r["sh"])
        if invested <= 0 or vea_shares <= 0:
            return

        thesis = {}
        for r in conn.execute("SELECT thesis_id, title FROM theses WHERE level='position'"):
            thesis[r["title"].split(" — ")[-1]] = r["thesis_id"]

        # Buys sized to target, with shares AND price rounded to their stored
        # precision (6dp / 2dp) FIRST, so the funding sell is computed against the
        # dollars that will actually be written — not the pre-rounding target.
        vea_price = round(_price_on(conn, FUNDER, as_of), 2)
        buy_rows, total_buy = [], 0.0
        for ticker, sleeve in BUYS.items():
            price  = round(_price_on(conn, ticker, as_of), 2)
            shares = round(invested * targets[sleeve] / price, 6)
            total_buy += shares * price        # the exact float that hits the ledger
            buy_rows.append((ticker, "Buy", shares, price))

        # Sell shares are the funding amount divided by the (rounded) VEA price and
        # left at FULL precision — NOT rounded to 6dp. Rounding the sell is what
        # left the earlier 1.25c residual; at full precision (sell*price) === the
        # summed buy dollars bit-for-bit, so the same-date net external flow is
        # exactly 0.0 and the demo TWR-vs-flows invariant survives.
        sell_shares = total_buy / vea_price
        if sell_shares >= vea_shares:
            raise RuntimeError(
                f"trim would sell {sell_shares:.6f} of {vea_shares:.6f} {FUNDER} shares — "
                "refusing to close the funding sleeve entirely"
            )
        residual = total_buy - sell_shares * vea_price
        if abs(residual) > 1e-9:
            raise RuntimeError(
                f"restructure is not cash-neutral: residual ${residual:.10f} — refusing "
                "to write a same-date flow the inception-seed-only invariant would flag"
            )

        rows = [(FUNDER, "Sell", sell_shares, vea_price), *buy_rows]
        for ticker, action, shares, price in rows:
            conn.execute(
                """INSERT INTO trades
                     (account_id, ticker, thesis_id, trade_date, action,
                      shares, price, fees, notes, lot_source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                (ACCOUNT_ID, ticker, thesis.get(ticker), as_of, action,
                 shares, price, NOTE, LOT_SOURCE),
            )
            print(f"  {action:4s} {ticker:5s} {shares:14.8f} sh @ ${price:7.2f} "
                  f"= ${shares * price:9,.2f}")

        conn.commit()

        proceeds = sell_shares * vea_price
        print(f"  cash-neutral residual: ${total_buy - proceeds:+.6f}")
    finally:
        conn.close()


def main() -> None:
    path = ROOT / "data" / "demo.db"
    print(f"Rebalancing paper book in {path} ...")
    migrate_db(path)


if __name__ == "__main__":
    main()
