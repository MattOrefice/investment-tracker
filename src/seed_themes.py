"""Seed five starter themes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.db import get_connection, initialize_db

THEMES = [
    {
        "name": "Valuation-driven",
        "description": (
            "Theses based on relative or absolute valuation gaps (CAPE, P/B, P/E spreads)"
        ),
    },
    {
        "name": "Regime change",
        "description": (
            "Theses anticipating shifts in macro regime (rates, inflation, dollar, geopolitics)"
        ),
    },
    {
        "name": "Factor tilt",
        "description": (
            "Theses expressing academic factor premia (quality, value, size, profitability)"
        ),
    },
    {
        "name": "Tax efficiency",
        "description": (
            "Theses focused on after-tax return optimization (vehicle choice, structure)"
        ),
    },
    {
        "name": "Drawdown protection",
        "description": (
            "Theses oriented toward preserving capital in tail scenarios"
        ),
    },
]


def seed():
    initialize_db()
    seeded = 0
    with get_connection() as conn:
        for t in THEMES:
            if conn.execute(
                "SELECT theme_id FROM themes WHERE name = ?", (t["name"],)
            ).fetchone() is None:
                conn.execute(
                    "INSERT INTO themes (name, description) VALUES (?, ?)",
                    (t["name"], t["description"]),
                )
                seeded += 1
    if seeded == 0:
        print("Themes already seeded, skipping.")
    else:
        print(f"Seeded {seeded} themes.")


if __name__ == "__main__":
    seed()
