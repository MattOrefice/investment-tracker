"""core_fi_treasury's yield declares a basis. #286.

WHAT THE ISSUE GOT WRONG, recorded because the correction is the finding. #286 set
the authored 4.00% against the NOMINAL YIELD CURVE at VGIT's ~5.5y duration (4.36%)
and called it a 36bp understatement worth +$0.11/yr. But this table's declared basis
— for every proxied entry — is a trailing-twelve-month DISTRIBUTION yield, which is
coupon income actually paid and is not a point on the nominal curve. Compared like
with like, IEF measures 3.97% against the authored 4.00%: **3bp, overstated, worth
-$0.01/yr.** The framing survived; the magnitude did not, and the sign flipped.

WHY IT IS STILL WORTH DOING. Not the money. The entry arrived at 6cd6f99
(2026-06-01) in a block whose FIFTEEN entries are all exact multiples of 0.5%, with
no comment then or since — authored, never measured. Of that block, three have since
been examined and three moved: tips 0.0250 -> 0.0461 (+84%), real_assets_commodities
0.0150 -> unmodellable (measured range 0.00%-40.38%), high_yield_muni 0.0450 ->
0.0400. Being close by luck is not the same as being measured, and only the
measurement distinguishes them.

THIS IS THE SECOND-SMALLEST MEASURABLE ENTRY. It should not read as progress on the
underlying problem: 96.1% of the drag KPI still rests on undeclared bases, and
hedged_equity alone is 89x this one. See the coverage issue.
"""
import pathlib
import re

import pytest

import src.location_config as lc

ROOT = pathlib.Path(lc.__file__).resolve().parent.parent
AS_OF = "2026-08-11"


def test_the_sleeve_declares_a_proxy():
    assert lc.SLEEVE_YIELD_PROXY.get("core_fi_treasury") == "IEF"


def test_the_value_is_the_proxys_ttm_not_a_round_number():
    """The tell this replaces: 15 of 15 entries in the original block were exact
    multiples of 0.5%. A declared value that lands on one again would mean the
    measurement was overwritten by an assumption."""
    v = lc.SLEEVE_ASSUMED_YIELD["core_fi_treasury"]
    assert v == pytest.approx(0.0397, abs=1e-9)
    assert abs(v * 200 - round(v * 200)) > 1e-9, (
        "the value is an exact multiple of 0.5% again — the signature of an authored "
        "number, which is what this entry stopped being")


def test_the_benchmark_convention_is_followed_not_the_held_ticker():
    """BENCHMARK THROUGHOUT. VGIT is the held ticker and measures 3.90%; IEF is the
    benchmark its own rationale compares against. Taking the held ticker on one row
    would be held-weighted reasoning applied selectively — the config says so, and it
    is what made intl_large_value take EFV over AVIV at an +85% spread."""
    assert lc.SLEEVE_YIELD_PROXY["core_fi_treasury"] == "IEF"
    held = {"VGIT"}
    assert lc.SLEEVE_YIELD_PROXY["core_fi_treasury"] not in held


def test_the_entry_is_no_longer_undeclared():
    """The defect in one line: it was in SLEEVE_ASSUMED_YIELD and in neither basis
    map."""
    s = "core_fi_treasury"
    assert s in lc.SLEEVE_ASSUMED_YIELD
    assert (s in lc.SLEEVE_YIELD_PROXY) or (s in lc.SLEEVE_YIELD_CONSTRUCTION)


def test_the_comment_records_that_it_was_authored_and_never_measured():
    """"Measured once and went stale" and "never measured" need different fixes, and
    the next reader cannot tell them apart from a value alone. The provenance is the
    part that does not survive without being written down."""
    src = (ROOT / "src" / "location_config.py").read_text(encoding="utf-8")
    flat = re.sub(r"\s+", " ", re.sub(r"^\s*#\s?", "", src, flags=re.M))
    i = flat.index('"core_fi_treasury": 0.0397')
    above = flat[max(0, i - 700):i]
    assert "6cd6f99" in above, "the originating commit is not recorded"
    assert "multiples of 0.5%" in above, "the round-number evidence is not recorded"
    assert "Never measured" in above or "never measured" in above


def test_the_shared_as_of_is_stated_and_not_a_per_entry_one():
    """A per-entry as-of silently fails the recomputation test, which resolves EVERY
    proxy at one date. The first draft of this entry claimed its own as-of and did
    exactly that."""
    src = (ROOT / "src" / "location_config.py").read_text(encoding="utf-8")
    flat = re.sub(r"\s+", " ", re.sub(r"^\s*#\s?", "", src, flags=re.M))
    assert "SHARED (2026-08-11)" in flat or "shared as-of of 2026-08-11" in flat
    i = flat.index('"core_fi_treasury": "IEF"')
    near = flat[max(0, i - 500):i]
    assert "2026-08-11" in near


# ── the correction to the issue's own sizing ──────────────────────────────────

def test_a_distribution_yield_is_not_a_curve_point():
    """#286's arithmetic error, pinned so the wrong comparison cannot be re-derived.

    The nominal 5.5y point was ~4.36% and the measured distribution yield is 3.97% —
    39bp apart and on opposite sides of the authored 4.00%. They are different
    quantities: a fund holding older, lower-coupon bonds distributes less than current
    yield-to-maturity. Anyone re-opening this must not compare the entry against a
    curve.
    """
    v = lc.SLEEVE_ASSUMED_YIELD["core_fi_treasury"]
    assert v < 0.0400, "the entry should be BELOW the authored 4.00%, not above it"
    assert v < 0.0436, (
        "the entry matches the nominal-curve estimate — the wrong quantity has been "
        "used; this table measures distribution yields")


# ── the deleted set left no dangling pointers ─────────────────────────────────

def test_no_comment_points_at_the_deleted_exempt_set():
    """#278 deleted FEDERALLY_EXEMPT_SLEEVES and its sweep reads through `ast` — which
    is why it could not flag its own documentation, and equally could not see two
    COMMENTS still pointing at the gone symbol. The AST choice that fixed one problem
    caused this one.

    Scoped to live pointers: the correction block explaining the deletion legitimately
    names it, so this checks the two former sites specifically rather than the file.
    """
    src = (ROOT / "src" / "location_config.py").read_text(encoding="utf-8")
    flat = re.sub(r"\s+", " ", re.sub(r"^\s*#\s?", "", src, flags=re.M))
    for probe in ("its exemption lives in", "federal exemption is a separate fact"):
        i = flat.index(probe)
        window = flat[i:i + 160]
        assert "FEDERALLY_EXEMPT_SLEEVES" not in window, (
            f"a comment still points at the deleted set: {window[:120]!r}")
        assert "SLEEVE_TAX_CHARACTER" in window


def test_the_deleted_set_resolves_nowhere():
    """The property, not the absence: if the name is mentioned anywhere outside the
    correction that explains its deletion, it had better be resolvable — and it is
    not."""
    assert not hasattr(lc, "FEDERALLY_EXEMPT_SLEEVES")
