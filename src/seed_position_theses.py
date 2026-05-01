"""Seed position theses — one per holding (10 ETFs + SPAXX)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.db import get_connection, initialize_db

SPAXX_VEHICLE_RATIONALE = (
    "SPAXX is Fidelity's default money market fund and the natural cash vehicle — no transaction "
    "cost, immediate liquidity, currently ~4-5% yield. BIL exists only for attribution comparison. "
    "The 3% cash weight is operational liquidity: it funds rebalancing trades, covers small drawdowns "
    "without forced selling, and buffers position friction. Would reduce toward 1% if short rates fall "
    "materially below 2%."
)


def seed():
    initialize_db()
    seeded = 0
    with get_connection() as conn:
        # Build sleeve_name → {thesis_id, conviction} from investment theses
        inv_map: dict[str, dict] = {}
        for r in conn.execute(
            "SELECT thesis_id, conviction, target_sleeves "
            "FROM theses WHERE level = 'investment' AND target_sleeves IS NOT NULL"
        ).fetchall():
            try:
                for sleeve in json.loads(r["target_sleeves"]):
                    inv_map[sleeve] = {
                        "thesis_id": r["thesis_id"],
                        "conviction": r["conviction"],
                    }
            except (json.JSONDecodeError, TypeError):
                pass

        # Count ETF holdings per sleeve (needed for weight split)
        sleeve_etf_count: dict[str, int] = {}
        for r in conn.execute(
            "SELECT ac.name AS sleeve, COUNT(*) AS n "
            "FROM securities s "
            "JOIN asset_classes ac ON s.asset_class_id = ac.asset_class_id "
            "WHERE s.security_type = 'ETF' "
            "GROUP BY ac.name"
        ).fetchall():
            sleeve_etf_count[r["sleeve"]] = r["n"]

        # ETF holdings in sleeve sort order
        etf_rows = conn.execute("""
            SELECT s.ticker, s.holding_rationale,
                   ac.name AS sleeve_name, ac.target_weight
            FROM securities s
            JOIN asset_classes ac ON s.asset_class_id = ac.asset_class_id
            WHERE s.security_type = 'ETF'
            ORDER BY ac.sort_order, s.ticker
        """).fetchall()

        for row in etf_rows:
            sleeve_name    = row["sleeve_name"]
            ticker         = row["ticker"]
            title          = f"{sleeve_name} — {ticker}"

            if conn.execute(
                "SELECT thesis_id FROM theses WHERE title = ?", (title,)
            ).fetchone():
                continue

            inv      = inv_map.get(sleeve_name, {})
            n        = max(sleeve_etf_count.get(sleeve_name, 1), 1)
            tw       = row["target_weight"] / n
            rationale = row["holding_rationale"] or ""
            conviction = inv.get("conviction", 3)

            conn.execute(
                """INSERT INTO theses
                   (title, macro_view, view_summary, conviction, level, status,
                    parent_thesis_id, target_sleeves, target_weight, vehicle_rationale,
                    horizon_months)
                   VALUES (?, ?, ?, ?, 'position', 'active', ?, ?, ?, ?, 60)""",
                (
                    title,
                    rationale,
                    rationale,
                    conviction,
                    inv.get("thesis_id"),
                    json.dumps([sleeve_name]),
                    tw,
                    rationale,
                ),
            )
            seeded += 1

        # SPAXX — synthetic holding not in securities table
        cash_sleeve = conn.execute(
            "SELECT name, target_weight FROM asset_classes WHERE name = 'Cash / SPAXX'"
        ).fetchone()
        if cash_sleeve:
            spaxx_title = "Cash / SPAXX — SPAXX"
            if not conn.execute(
                "SELECT thesis_id FROM theses WHERE title = ?", (spaxx_title,)
            ).fetchone():
                inv = inv_map.get("Cash / SPAXX", {})
                conn.execute(
                    """INSERT INTO theses
                       (title, macro_view, view_summary, conviction, level, status,
                        parent_thesis_id, target_sleeves, target_weight, vehicle_rationale,
                        horizon_months)
                       VALUES (?, ?, ?, ?, 'position', 'active', ?, ?, ?, ?, NULL)""",
                    (
                        spaxx_title,
                        SPAXX_VEHICLE_RATIONALE,
                        SPAXX_VEHICLE_RATIONALE,
                        inv.get("conviction", 5),
                        inv.get("thesis_id"),
                        json.dumps(["Cash / SPAXX"]),
                        cash_sleeve["target_weight"],
                        SPAXX_VEHICLE_RATIONALE,
                    ),
                )
                seeded += 1

    if seeded == 0:
        print("Position theses already seeded, skipping.")
    else:
        print(f"Seeded {seeded} position theses.")


if __name__ == "__main__":
    seed()
