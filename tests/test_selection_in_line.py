"""A selection difference that rounds to 0 bps reads "in line with" (#602, decided by the
owner on October 15, 2026).

The benchmark prose's cross-reference names the top selection drivers:
"AVUV outperformed IWM by 754 bps". A difference whose printed figure is 0 read
"outperformed ... by 0 bps". It reads "AVUV performed in line with IWM", as the class #600
added to the versus_benchmark family for the summary's alpha.
"""
from __future__ import annotations

import pytest

from src import factors


def _result() -> dict:
    """A significant intercept, so the paragraph names its drivers."""
    return {"betas": {"Bench-RF": 0.99, "HML": 0.012, "SMB": 0.021, "RMW": -0.008},
            "t_stats": {"Bench-RF": 97.5, "HML": 1.1, "SMB": 2.0, "RMW": -0.4},
            "alpha_annual_bps": 138.0, "t_alpha": 3.0, "T": 290, "r_squared": 0.981,
            "sample_start": "2025-05-01", "sample_end": "2026-06-30",
            "_hac_bse": {"const": 0.0000390}}


@pytest.mark.parametrize("sel_bps, clause", [
    (0.3, "AVUV performed in line with IWM"),
    (-0.4, "AVUV performed in line with IWM"),
    (0.5, "AVUV performed in line with IWM"),
    (0.6, "AVUV outperformed IWM by 1 bps"),
    (-12.2, "AVUV underperformed IWM by 12 bps"),
    (754.0, "AVUV outperformed IWM by 754 bps"),
])
def test_a_selection_that_rounds_to_zero_reads_in_line(sel_bps, clause):
    paragraph = factors.build_benchmark_prose(
        _result(), [{"holding": "AVUV", "bench": "IWM", "sel_bps": sel_bps}])[1]
    assert clause in paragraph, paragraph
    assert "by 0 bps" not in paragraph, paragraph
