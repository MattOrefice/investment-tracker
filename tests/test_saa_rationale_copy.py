"""The SAA sleeve rationales after the writing sweep (2026-09-25 audit, item 14).

The rationales are seeded prose: src/seed_saa.py carries them for a fresh build, and
tools/migrate_saa_rationale_copy.py carried the same text into the committed demo book.
The owner's review of the calibration (14a) cut the aphoristic one-liners, asked for the
serial comma, and restored one caveat as a statement of fact. These pin both books, so
neither can drift back on its own.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.seed_saa import SUB_CLASSES

ROOT = Path(__file__).resolve().parent.parent
SEED = {s["name"]: s["rationale"] for s in SUB_CLASSES if s.get("rationale")}
# Cash / SPAXX's rationale is not part of the sweep's calibration; its SPAXX yield figure
# is decided separately (#370).
SWEPT = sorted(n for n in SEED if n != "Cash / SPAXX")


def _demo_rationales() -> dict[str, str]:
    conn = sqlite3.connect(f"file:{ROOT / 'data' / 'demo.db'}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT name, rationale FROM asset_classes "
                            "WHERE parent_id IS NOT NULL").fetchall()
    finally:
        conn.close()
    return {n: r for n, r in rows}


def test_the_seed_and_the_demo_book_carry_the_same_rationales():
    demo = _demo_rationales()
    assert len(SWEPT) == 12, "premise: twelve strategic sleeves"
    assert set(SWEPT) <= set(demo), sorted(set(SWEPT) - set(demo))
    assert {n: demo[n] for n in SWEPT} == {n: SEED[n] for n in SWEPT}


CUT = [
    "strategic clothing",
    "fee schedule",
    "a tilt, not a thesis",
    "Chicago",
    "Osaka",
    "hunch",
    "not enough to move a return",
    "chasing it after a rally",
    "deserves naming",
    "institutional analytical framing",
]


@pytest.mark.parametrize("phrase", CUT)
def test_a_cut_one_liner_stays_cut(phrase):
    page = (ROOT / "pages" / "1_SAA.py").read_text(encoding="utf-8")
    texts = [page, *SEED.values(), *_demo_rationales().values()]
    assert not any(phrase in t for t in texts), phrase


def test_selection_in_international_small_value_is_not_read_as_skill():
    """The calibration draft cut "it should not be read as skill" as an instruction; the
    owner restored its substance as a fact, because a reader would otherwise take the
    selection effect for skill."""
    fact = ("Attribution will show that premium as selection. It comes from the factor "
            "tilt, not manager skill.")
    assert fact in SEED["International Small Value"]
    assert fact in _demo_rationales()["International Small Value"]


def test_home_bias_is_stated_plainly():
    fact = ("Holding the tilt only in the US would imply that the profitability premium "
            "exists at home but not abroad, which is home bias.")
    assert fact in SEED["International Quality"]
    assert fact in _demo_rationales()["International Quality"]


@pytest.mark.parametrize("sleeve, phrase", [
    ("US Large Core", "best-governed, and highest-quality"),
    ("US Small Cap", "more interest-rate-sensitive, and less correlated"),
    ("International Quality", "return on equity, accruals, and leverage"),
    ("International Quality", "the same index provider, and the same three"),
    ("Emerging Markets", "structurally cheaper valuations, and growth profiles"),
    ("Emerging Markets", "quality, value, and small value"),
    ("Emerging Markets", "3.2%, 2.8%, 1.7%, and 1.5%"),
])
def test_lists_keep_the_serial_comma(sleeve, phrase):
    """As the page renders them: EM's four mirrored sleeves are derived from the
    targets since #406 item 5, so the stored text carries tokens, not the figures."""
    from src.prose_figures import render
    targets = _demo_targets()
    assert phrase in render(SEED[sleeve], targets=targets)
    assert phrase in render(_demo_rationales()[sleeve], targets=targets)


def _demo_targets() -> "dict[str, float]":
    conn = sqlite3.connect(f"file:{ROOT / 'data' / 'demo.db'}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT name, target_weight FROM asset_classes "
                            "ORDER BY parent_id IS NOT NULL").fetchall()
    finally:
        conn.close()
    return {n: w for n, w in rows}
