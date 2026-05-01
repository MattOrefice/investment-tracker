"""Phase 3 schema migration: two-tier theses with theme tags.

Idempotent — safe to run multiple times.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.db import get_connection, initialize_db


def migrate():
    initialize_db()
    with get_connection() as conn:

        # 1. Create themes table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS themes (
                theme_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL UNIQUE,
                description TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 2. Create thesis_themes join table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS thesis_themes (
                thesis_id INTEGER NOT NULL,
                theme_id  INTEGER NOT NULL,
                PRIMARY KEY (thesis_id, theme_id),
                FOREIGN KEY (thesis_id) REFERENCES theses(thesis_id),
                FOREIGN KEY (theme_id)  REFERENCES themes(theme_id)
            )
        """)

        # 3. Add new columns to theses (idempotent via PRAGMA check)
        existing = {r[1] for r in conn.execute("PRAGMA table_info(theses)").fetchall()}

        new_cols = [
            ("level",                     "TEXT NOT NULL DEFAULT 'investment'"),
            ("parent_thesis_id",          "INTEGER"),
            ("target_sleeves",            "TEXT"),
            ("invalidation_conditions",   "TEXT"),
            ("expected_return_scenario",  "TEXT"),
            ("vehicle_rationale",         "TEXT"),
            ("target_weight",             "REAL"),
            ("view_summary",              "TEXT"),
            ("outcome",                   "TEXT"),
            ("realized_pnl_pct",          "REAL"),
            ("what_i_got_right",          "TEXT"),
            ("what_i_got_wrong",          "TEXT"),
            ("would_repeat",              "TEXT"),
        ]

        added = []
        for col_name, col_def in new_cols:
            if col_name not in existing:
                conn.execute(f"ALTER TABLE theses ADD COLUMN {col_name} {col_def}")
                added.append(col_name)

        if added:
            print(f"Added columns to theses: {', '.join(added)}")
        else:
            print("theses columns already up to date.")

        print("Migration complete.")


if __name__ == "__main__":
    migrate()
