"""#347 — seeding the SAA taxonomy twice does not duplicate it.

src/seed_saa.seed() runs from bootstrap on every personal-mode start. asset_classes has
no unique constraint beyond its primary key, so nothing in the SCHEMA stops a second run
from inserting every parent and sleeve again: the empty-table check at the top of seed()
is the sole guard. This pins that guard. Remove it and the second seed below inserts the
whole taxonomy a second time, and this test fails instead of a book silently doubling.
"""
import sqlite3


def _taxonomy(path):
    c = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return (c.execute("SELECT COUNT(*) FROM asset_classes").fetchone()[0],
                sorted(c.execute("SELECT name, parent_id IS NULL FROM asset_classes").fetchall()))
    finally:
        c.close()


def test_a_second_seed_leaves_the_taxonomy_unchanged(tmp_path, monkeypatch):
    import src.db
    import src.seed_saa as seed_saa
    db = tmp_path / "fresh.db"
    monkeypatch.setattr(src.db, "DB_PATH", db)
    monkeypatch.setattr(src.db, "_migrated_paths", set())

    seed_saa.seed()
    first = _taxonomy(db)
    assert first[0] == len(seed_saa.PARENTS) + len(seed_saa.SUB_CLASSES), (
        f"the first seed wrote {first[0]} rows; the premise (a full taxonomy) failed")

    seed_saa.seed()
    assert _taxonomy(db) == first, "a second seed changed asset_classes: the taxonomy duplicated"
