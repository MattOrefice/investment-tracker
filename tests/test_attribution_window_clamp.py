"""#334 — an attribution window that starts before inception is CLAMPED to inception
and DISCLOSED, instead of computing zero rows and rendering nothing.

Found by #303: page 2's attribution section computed 0 Brinson-Fachler rows for any
window starting before the portfolio's inception, and rendered no section and no
reason. The price-series side (value series and naive benchmark) already begins at
inception, so the clamp makes the attribution window agree with it.
"""
import pathlib

import pytest

from src.returns import clamped_period_bounds, period_bounds

INC = "2026-06-09"


@pytest.mark.parametrize("anchor, clamped", [
    ("2026-09-06", True),    # 3M start 2026-06-08: one day before inception
    ("2026-09-07", False),   # 3M start 2026-06-09: exactly inception
    ("2026-09-22", False),
])
def test_the_clamp_is_two_sided_at_inception(anchor, clamped):
    start, end, was = clamped_period_bounds("3M", anchor, INC)
    assert was is clamped
    assert start == (INC if clamped else period_bounds("3M", anchor, INC)[0])
    assert end == anchor


def test_since_inception_and_ytd_are_never_clamped():
    assert clamped_period_bounds("SI", "2026-09-22", INC)[2] is False
    assert clamped_period_bounds("1Y", "2026-09-22", INC) == (INC, "2026-09-22", True)


def test_page2_discloses_and_renders_a_clamped_window_live(monkeypatch):
    """On the real book (inception 2026-06-09), 1Y starts before inception: the page
    must SAY attribution runs from inception, and the section must RENDER."""
    ROOT = pathlib.Path(__file__).resolve().parent.parent
    tracker = ROOT / "data" / "tracker.db"
    from src.household_data import find_latest_positions_csv
    if find_latest_positions_csv() is None or not tracker.exists():
        pytest.skip("personal-mode inputs absent")
    import src.config
    import src.db
    monkeypatch.setattr(src.config, "IS_DEMO", False)
    monkeypatch.setattr(src.db, "DB_PATH", tracker)
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "pages" / "2_Performance.py"), default_timeout=120).run()
    [r for r in at.radio if r.key == "bf_period"][0].set_value("1Y").run()
    assert not at.exception, f"page raised: {at.exception}"
    infos = " ".join(str(i.value) for i in at.info)
    assert "Attribution from inception, June 9, 2026" in infos, infos
    caps = " ".join(str(c.value) for c in at.caption)
    assert "price-series methodology" in caps, "the two-stage section did not render"
