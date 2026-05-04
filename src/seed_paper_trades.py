"""
Seed paper trades: deploy $1,000 across 10 holdings on 2025-05-01.

Fractional shares are used so the full $1,000 deploys cleanly.
Pass force=True (or --force on the CLI) to wipe existing trades first.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import get_connection, initialize_db
from src.prices import get_prices

TRADE_DATE   = "2025-05-01"
TOTAL_CASH   = 1_000.0
ACCOUNT_ID   = 1
TRADE_NOTES  = "Paper trade — $1k inception seed"

# 10 ETF holdings in sleeve order
HOLDINGS = ["VOO", "SPHQ", "VTV", "AVUV", "VEA", "IEMG", "VGIT", "SCHP", "VNQ", "PDBC"]


def _get_position_thesis_map(conn) -> dict[str, int]:
    """Return ticker → thesis_id map by parsing position thesis titles."""
    rows = conn.execute(
        "SELECT thesis_id, title FROM theses WHERE level = 'position'"
    ).fetchall()
    result = {}
    for r in rows:
        parts = r["title"].split(" — ")
        if len(parts) >= 2:
            result[parts[-1]] = r["thesis_id"]
    return result


def _ensure_spaxx_in_securities(conn) -> None:
    """Add SPAXX to securities table if not present (needed for trade FK)."""
    if conn.execute("SELECT 1 FROM securities WHERE ticker = 'SPAXX'").fetchone():
        return
    cash_ac = conn.execute(
        "SELECT asset_class_id FROM asset_classes WHERE name = 'Cash / SPAXX'"
    ).fetchone()
    if not cash_ac:
        raise RuntimeError("'Cash / SPAXX' asset class not found in DB.")
    conn.execute(
        """INSERT INTO securities (ticker, name, asset_class_id, security_type)
           VALUES ('SPAXX', 'Fidelity Government Money Market Fund', ?, 'Money Market')""",
        (cash_ac["asset_class_id"],),
    )
    print("  Added SPAXX to securities table.")


def _price_on_or_after(ticker: str, from_date: str) -> tuple[str, float]:
    """
    Return (date_str, close_price) for the first available trading day
    on or after from_date. Fetches a 10-day window to handle holidays.
    """
    from datetime import date, timedelta
    window_end = (date.fromisoformat(from_date) + timedelta(days=10)).isoformat()
    prices = get_prices(ticker, from_date, window_end)
    if prices.empty:
        raise ValueError(f"No price data found for {ticker} on or after {from_date}.")
    first_date = prices.index[0]
    close = float(prices.loc[first_date, "close"])
    from src.prices import _to_iso
    return _to_iso(first_date), close


def seed(force: bool = False):
    initialize_db()

    with get_connection() as conn:
        existing = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        if existing > 0:
            if not force:
                print(
                    f"Trades table already has {existing} row(s). "
                    "Aborting to prevent duplicates.\n"
                    "Pass force=True (or --force) to wipe and re-seed."
                )
                return
            conn.execute("DELETE FROM trades")
            print(f"Wiped {existing} existing trade(s). Re-seeding...")

        _ensure_spaxx_in_securities(conn)

        thesis_map = _get_position_thesis_map(conn)

        # ── Fetch prices and compute allocation ────────────────────────────
        print(f"Fetching prices for {TRADE_DATE}...")
        prices_used: dict[str, tuple[str, float]] = {}
        for ticker in HOLDINGS:
            trade_date_used, price = _price_on_or_after(ticker, TRADE_DATE)
            prices_used[ticker] = (trade_date_used, price)
            if trade_date_used != TRADE_DATE:
                print(f"  {ticker}: no price on {TRADE_DATE}, using {trade_date_used}")

        # ── Look up target weights from position theses ────────────────────
        weight_rows = conn.execute("""
            SELECT t.title, t.target_weight
            FROM theses t
            WHERE t.level = 'position'
        """).fetchall()
        weight_map: dict[str, float] = {}
        for r in weight_rows:
            parts = r["title"].split(" — ")
            ticker = parts[-1]
            weight_map[ticker] = r["target_weight"] or 0.0

        # ── Compute shares (fractional — full target_dollars deployed) ────────
        allocations = []
        total_deployed = 0.0
        for ticker in HOLDINGS:
            target_w = weight_map.get(ticker, 0.0)
            target_dollars = TOTAL_CASH * target_w
            trade_date_used, price = prices_used[ticker]
            shares = round(target_dollars / price, 6) if price > 0 else 0.0
            cost   = shares * price
            total_deployed += cost
            allocations.append(
                {
                    "ticker":      ticker,
                    "trade_date":  trade_date_used,
                    "price":       price,
                    "target_$":    target_dollars,
                    "shares":      shares,
                    "cost":        cost,
                    "target_w":    target_w,
                    "thesis_id":   thesis_map.get(ticker),
                }
            )

        residual_cash = TOTAL_CASH - total_deployed

        # ── Insert ETF trades ──────────────────────────────────────────────
        for a in allocations:
            conn.execute(
                """INSERT INTO trades
                   (account_id, ticker, thesis_id, trade_date, action,
                    shares, price, fees, notes)
                   VALUES (?, ?, ?, ?, 'Buy', ?, ?, 0, ?)""",
                (
                    ACCOUNT_ID, a["ticker"], a["thesis_id"],
                    a["trade_date"], a["shares"], a["price"], TRADE_NOTES,
                ),
            )

        # ── Insert SPAXX residual ──────────────────────────────────────────
        spaxx_thesis_id = thesis_map.get("SPAXX")
        conn.execute(
            """INSERT INTO trades
               (account_id, ticker, thesis_id, trade_date, action,
                shares, price, fees, notes)
               VALUES (?, 'SPAXX', ?, ?, 'Buy', ?, 1.0, 0, ?)""",
            (ACCOUNT_ID, spaxx_thesis_id, TRADE_DATE,
             round(residual_cash, 2), TRADE_NOTES),
        )

    # ── Print summary ──────────────────────────────────────────────────────
    print(f"\n{'Ticker':<8} {'Shares':>8}  {'Price':>8}  {'Cost':>10}  {'Target%':>8}  {'Actual%':>8}")
    print("-" * 62)
    grand_total = total_deployed + residual_cash
    for a in allocations:
        actual_pct = (a["cost"] / grand_total * 100) if grand_total else 0
        print(
            f"{a['ticker']:<8} {a['shares']:>8}  "
            f"${a['price']:>7.2f}  "
            f"${a['cost']:>9.2f}  "
            f"{a['target_w']*100:>7.1f}%  "
            f"{actual_pct:>7.1f}%"
        )
    print("-" * 62)
    spaxx_pct = residual_cash / grand_total * 100 if grand_total else 0
    target_spaxx = weight_map.get("SPAXX", 0.03)
    print(
        f"{'SPAXX':<8} {residual_cash:>8.2f}  "
        f"${'1.00':>7}  "
        f"${residual_cash:>9.2f}  "
        f"{target_spaxx*100:>7.1f}%  "
        f"{spaxx_pct:>7.1f}%"
    )
    print("-" * 62)
    print(
        f"{'TOTAL':<8} {'':>8}  {'':>8}  "
        f"${grand_total:>9.2f}  "
        f"{'100.0%':>8}  "
        f"{'100.0%':>8}"
    )
    print(f"\nDeployed in ETFs:  ${total_deployed:,.2f}")
    print(f"Residual (SPAXX):  ${residual_cash:,.2f}")
    print(f"Total:             ${grand_total:,.2f}")

    # ── Verification query ────────────────────────────────────────────────
    print("\n=== Verification ===")
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT ticker, shares, price, ROUND(shares*price,2) AS cost "
            "FROM trades ORDER BY ticker"
        ).fetchall()
        print(f"{'Ticker':<8} {'Shares':>10}  {'Price':>8}  {'Cost':>10}")
        for r in rows:
            print(f"{r['ticker']:<8} {r['shares']:>10.3f}  ${r['price']:>7.2f}  ${r['cost']:>9.2f}")
        total_cost = sum(r["cost"] for r in rows)
        print(f"\nTotal cost in DB: ${total_cost:,.2f}")
        print(f"Trades inserted:  {len(rows)}")


if __name__ == "__main__":
    seed(force="--force" in sys.argv)
