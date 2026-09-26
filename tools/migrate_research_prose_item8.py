"""Correct three securities rows in data/demo.db that the Research page shows (audit item 8).

  * DBC's name. The row carries DJP's, "iPath Bloomberg Commodity Index Total Return
    ETN": Phase 41 replaced the delisted DJP with DBC but inserted DBC only where no
    DBC row existed, and demo.db already had one under the old name. The seed source
    and tracker.db both say "Invesco DB Commodity Index Tracking Fund".
  * IEMG's rationale called EEM "identical exposure" on "the same MSCI Emerging Markets
    index". IEMG tracks MSCI Emerging Markets IMI, which adds small caps; EEM tracks
    MSCI Emerging Markets.
  * PDBC's rationale called it a C-corporation and its benchmark DJP, an ETN. From its
    prospectus, PDBC is a regulated investment company holding its futures through a
    Cayman Islands subsidiary and reporting on Form 1099, benchmarked to the index DBC
    tracks. DBC, the sleeve's benchmark, is a commodity pool that issues a K-1, and it
    costs more (0.85%), so "at the cost of a higher ER" against K-1 alternatives no
    longer holds for the one the page names.

Each UPDATE is guarded on the old value and must change exactly one row, or the run
rolls back; a row already carrying the new value is left alone, so a second run
changes nothing. Targets data/demo.db explicitly (never get_db_path). main() only,
so bootstrap never runs it. src/seed_securities.py carries the same text.
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"
sys.path.insert(0, str(ROOT))

DBC_OLD_NAME = "iPath Bloomberg Commodity Index Total Return ETN"
DBC_NAME = "Invesco DB Commodity Index Tracking Fund"

IEMG_OLD = (
    "IEMG is the holding because EEM costs 0.70% for identical exposure — seven times more "
    "expensive, and the largest same-index fee gap in the portfolio at 61 bps. IEMG at 0.09% "
    "tracks the same MSCI Emerging Markets index with the same ~27% China weight. "
)
IEMG_NEW = (
    "IEMG is the holding because EEM costs 0.70% — seven times more expensive, and the "
    "largest fee gap in the portfolio at 61 bps. The two are not the same exposure: IEMG at "
    "0.09% tracks MSCI Emerging Markets IMI, which adds small caps to the large and mid caps "
    "of EEM's MSCI Emerging Markets index. "
)

PDBC_OLD = (
    "PDBC uses a C-corporation structure instead, eliminating K-1 at the cost of a higher ER "
    "(0.59%). For a 5% position in a taxable account, tax simplicity is worth materially more "
    "than the fee difference versus K-1-issuing alternatives. DJP (the benchmark) is an "
    "exchange-traded note with counterparty risk — used for attribution only, never as a "
    "holding.\n\n**Would revisit if** a broad no-K-1 commodity fund appeared at materially "
    "lower cost or with better liquidity. The 0.59% is paid for tax structure rather than "
    "strategy, so a cheaper equivalent would make this holding indefensible on its own terms."
)
PDBC_NEW = (
    "PDBC is a regulated investment company that holds its futures through a wholly-owned "
    "Cayman Islands subsidiary, so it reports on Form 1099 instead. It is benchmarked to the "
    "DBIQ Optimum Yield Diversified Commodity Index, the index DBC (the benchmark) tracks as a "
    "commodity pool that issues a K-1, and it costs 0.59% against DBC's 0.85%. DBC is used for "
    "attribution only, never as a holding.\n\n**Would revisit if** a broad no-K-1 commodity "
    "fund appeared at materially lower cost or with better liquidity."
)


def _rewrite(conn, ticker: str, column: str, old: str, new: str) -> int:
    """Replace ``old`` with ``new`` inside one row's column. Returns rows changed (0 or 1)."""
    (current,) = conn.execute(f"SELECT {column} FROM securities WHERE ticker = ?",
                              (ticker,)).fetchone()
    if new in current and old not in current:
        return 0
    if current.count(old) != 1:
        raise SystemExit(f"ABORT: {ticker}.{column} carries neither the old text nor the new")
    n = conn.execute(f"UPDATE securities SET {column} = ? WHERE ticker = ? AND {column} = ?",
                     (current.replace(old, new), ticker, current)).rowcount
    if n != 1:
        raise SystemExit(f"ABORT: {ticker}.{column} update changed {n} rows, not 1")
    return 1


def main() -> int:
    conn = sqlite3.connect(DEMO_DB)
    try:
        with conn:
            changed = _rewrite(conn, "DBC", "name", DBC_OLD_NAME, DBC_NAME)
            changed += _rewrite(conn, "IEMG", "holding_rationale", IEMG_OLD, IEMG_NEW)
            changed += _rewrite(conn, "PDBC", "holding_rationale", PDBC_OLD, PDBC_NEW)
    finally:
        conn.close()
    print(f"{DEMO_DB.name}: {changed} row(s) changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
