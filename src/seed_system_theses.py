"""Seed system theses: rebalance and cash_management."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.db import get_connection, initialize_db

SYSTEM_THESES = [
    {
        "title": "system:rebalance",
        "view_summary": (
            "Routine rebalancing trades to maintain SAA target weights within tolerance bands. "
            "Not a market view."
        ),
        "conviction":      5,
        "level":           "investment",
        "status":          "active",
        "horizon_months":  None,
        "exit_conditions": "Never closes — operational thesis.",
    },
    {
        "title": "system:cash_management",
        "view_summary": (
            "Operational cash flows: deposits, withdrawals, dividend reinvestment. "
            "Not a market view."
        ),
        "conviction":      5,
        "level":           "investment",
        "status":          "active",
        "horizon_months":  None,
        "exit_conditions": "Never closes — operational thesis.",
    },
]


def seed():
    initialize_db()
    seeded = 0
    with get_connection() as conn:
        for t in SYSTEM_THESES:
            if conn.execute(
                "SELECT thesis_id FROM theses WHERE title = ?", (t["title"],)
            ).fetchone() is None:
                conn.execute(
                    """INSERT INTO theses
                       (title, macro_view, view_summary, conviction, level, status,
                        horizon_months, exit_conditions)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        t["title"],
                        t["view_summary"],   # macro_view (legacy NOT NULL)
                        t["view_summary"],   # view_summary (canonical)
                        t["conviction"],
                        t["level"],
                        t["status"],
                        t["horizon_months"],
                        t["exit_conditions"],
                    ),
                )
                seeded += 1
    if seeded == 0:
        print("System theses already seeded, skipping.")
    else:
        print(f"Seeded {seeded} system theses.")


if __name__ == "__main__":
    seed()
