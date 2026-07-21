"""Seed investment theses derived from SAA sleeve rationales."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.db import get_connection, initialize_db

# Per-sleeve metadata not derivable from the DB
SLEEVE_META = {
    "US Large Core": {
        "exit_conditions": (
            "Would reduce if active factor tilts consistently underperform pure cap-weight "
            "for multiple decades, removing the rationale for maintaining an un-opinionated anchor."
        ),
        "invalidation_conditions": (
            "Factor tilts outperform pure cap-weight by >3% per year for 10+ years, making a "
            "pure-factor-only portfolio structurally more efficient."
        ),
        "expected_return_scenario": (
            "S&P 500 long-run real return of 5-7% annually; anchor exposure ensures participation "
            "in broad equity rallies when factor tilts underperform."
        ),
        "themes": ["Drawdown protection"],
    },
    "US Large Quality": {
        "exit_conditions": (
            "Would reduce if quality screens become dominated by a single sector to the point of "
            "losing diversification benefits across the factor."
        ),
        "invalidation_conditions": (
            "Quality factor alpha disappears post-publication, or a single sector comes to "
            "represent over 60% of quality-screened indices."
        ),
        "expected_return_scenario": (
            "Quality premium of ~1-2% over market annually with materially lower drawdowns; "
            "equity-like returns with a smoother compounding path."
        ),
        "themes": ["Factor tilt"],
    },
    "US Large Value": {
        "exit_conditions": (
            "Would reduce if growth earnings advantage compounds another 5+ years without the "
            "value-growth valuation spread beginning to narrow."
        ),
        "invalidation_conditions": (
            "Value-growth CAPE spread compresses below historical median and remains there for "
            "5+ years with no fundamental catalyst for mean reversion."
        ),
        "expected_return_scenario": (
            "Mean reversion of the current 2000-level value-growth spread yields ~3-4% "
            "incremental return over a 5-7 year horizon."
        ),
        "themes": ["Factor tilt", "Valuation-driven"],
    },
    "US Small Cap": {
        "exit_conditions": (
            "Would reduce if real rates fail to normalize and the valuation discount vs. "
            "large-cap narrows without the size premium reasserting."
        ),
        "invalidation_conditions": (
            "Size premium shown to be fully arbitraged away with no evidence of reassertion "
            "after 20+ years post-publication."
        ),
        "expected_return_scenario": (
            "Modest ~1% premium over large-cap if size effect reasserts; primary value is "
            "diversification from mega-cap tech concentration risk."
        ),
        "themes": ["Factor tilt", "Valuation-driven"],
    },
    # Phase 39 — the four international sleeves. The exit, invalidation and
    # expected-return text below is the previously authored "International
    # Developed" thesis carried forward UNCHANGED: it is a region-level view
    # (CAPE gap, dollar mean reversion, structural reform) that applies to all
    # four sleeves equally, so propagating it is faithful rather than invented.
    # Only `themes` differs, adding the factor overlay each tilt expresses.
    # The per-sleeve factor arguments live in seed_saa.py; no sleeve-specific
    # exit or invalidation conditions have been authored for the three tilts yet.
    "International Core": {
        "exit_conditions": (
            "Would reduce if European or Japanese structural reform stalls further and the "
            "valuation gap with US narrows toward historical median."
        ),
        "invalidation_conditions": (
            "International developed CAPE reaches within 5 points of US CAPE and structural "
            "dollar support persists indefinitely."
        ),
        "expected_return_scenario": (
            "Valuation gap closure plus potential currency tailwind yields ~3-5% incremental "
            "return over US equities across a 7-10 year horizon."
        ),
        "themes": ["Valuation-driven", "Regime change"],
    },
    "International Quality": {
        "exit_conditions": (
            "Would reduce if European or Japanese structural reform stalls further and the "
            "valuation gap with US narrows toward historical median."
        ),
        "invalidation_conditions": (
            "International developed CAPE reaches within 5 points of US CAPE and structural "
            "dollar support persists indefinitely."
        ),
        "expected_return_scenario": (
            "Valuation gap closure plus potential currency tailwind yields ~3-5% incremental "
            "return over US equities across a 7-10 year horizon."
        ),
        "themes": ["Factor tilt", "Valuation-driven", "Regime change"],
    },
    "International Large Value": {
        "exit_conditions": (
            "Would reduce if European or Japanese structural reform stalls further and the "
            "valuation gap with US narrows toward historical median."
        ),
        "invalidation_conditions": (
            "International developed CAPE reaches within 5 points of US CAPE and structural "
            "dollar support persists indefinitely."
        ),
        "expected_return_scenario": (
            "Valuation gap closure plus potential currency tailwind yields ~3-5% incremental "
            "return over US equities across a 7-10 year horizon."
        ),
        "themes": ["Factor tilt", "Valuation-driven", "Regime change"],
    },
    "International Small Value": {
        "exit_conditions": (
            "Would reduce if European or Japanese structural reform stalls further and the "
            "valuation gap with US narrows toward historical median."
        ),
        "invalidation_conditions": (
            "International developed CAPE reaches within 5 points of US CAPE and structural "
            "dollar support persists indefinitely."
        ),
        "expected_return_scenario": (
            "Valuation gap closure plus potential currency tailwind yields ~3-5% incremental "
            "return over US equities across a 7-10 year horizon."
        ),
        "themes": ["Factor tilt", "Valuation-driven", "Regime change"],
    },
    "Emerging Markets": {
        "exit_conditions": (
            "Would reduce if China governance risk materially worsens or if EM index "
            "construction concentrates further into a single country."
        ),
        "invalidation_conditions": (
            "China ADR delisting materializes legislatively, or EM valuations reach parity "
            "with developed markets removing the structural discount."
        ),
        "expected_return_scenario": (
            "EM structural discount (~10x P/E vs US ~22x) implies significant valuation "
            "tailwind; demographic dividend adds 1-2% long-run growth premium."
        ),
        "themes": ["Valuation-driven", "Regime change"],
    },
    "Core Fixed Income": {
        "exit_conditions": (
            "Would reduce if inflation regime persists and nominal duration stops providing "
            "drawdown protection in equity tail scenarios."
        ),
        "invalidation_conditions": (
            "Inflation regime exceeds 4% CPI for 3+ consecutive years with Treasuries "
            "providing zero or negative correlation to equity drawdowns."
        ),
        "expected_return_scenario": (
            "Drawdown buffer in recessionary scenarios; negative correlation to equities "
            "provides ~5-10% portfolio protection during equity selloffs."
        ),
        "themes": ["Drawdown protection"],
    },
    "TIPS": {
        "exit_conditions": (
            "Would reduce if horizon shortens or if confidence in sustained disinflation "
            "grows to the point that the inflation hedge becomes unnecessary."
        ),
        "invalidation_conditions": (
            "Real yields sustain below 0% for 5+ years with CPI consistently below 2%, "
            "making TIPS negative carry with minimal inflation protection expected."
        ),
        "expected_return_scenario": (
            "Real return equal to real yield (~2%) plus inflation linkage; insurance value "
            "against a decade of silent real-return erosion dominates return generation."
        ),
        "themes": ["Drawdown protection", "Regime change"],
    },
    "Real Assets": {
        "exit_conditions": (
            "Would reduce in deflationary regimes where REITs and commodities stop "
            "earning their diversification benefit."
        ),
        "invalidation_conditions": (
            "Sustained deflation (CPI below -1% for 2+ consecutive years) or both REITs "
            "and commodities exhibit correlation >0.8 with equities for 5+ years."
        ),
        "expected_return_scenario": (
            "Inflation protection via commodity price linkage and REIT income; "
            "diversification benefit realized in inflationary or stagflationary regimes."
        ),
        "themes": ["Regime change", "Drawdown protection"],
    },
    "Cash / SPAXX": {
        "exit_conditions": (
            "Would reduce toward 1-2% if cash yields collapse below 2% for 12+ consecutive "
            "months, increasing deployment into equity sleeves."
        ),
        "invalidation_conditions": (
            "Operational liquidity thesis never invalidated — size adjusts but a zero-cash "
            "policy is never appropriate in a taxable rebalancing portfolio."
        ),
        "expected_return_scenario": (
            "Money market yield (~4-5% currently) with zero principal risk; drag offset "
            "by rebalancing optionality value and avoidance of forced selling."
        ),
        "themes": [],
        "conviction_override": 5,
    },
}


def _require_meta(sleeve_name: str) -> dict:
    """SLEEVE_META for a DB sleeve, or raise. Deliberately NO {} default.

    SLEEVE_META is a hardcoded map keyed by asset_classes.name, and the name is
    read from the DB — so a renamed or newly-split sleeve misses every entry and
    `.get(name, {})` returns an empty dict SILENTLY. The thesis is then seeded
    with no exit conditions, no invalidation conditions and no themes, and
    _conviction falls through to its weight-based default (which happens to give
    the same answer for a >15% sleeve, so nothing even looks different).

    It also ORPHANS: the title is f"{sleeve_name} — Strategic Allocation", so a
    renamed sleeve produces a NEW title, the already-seeded guard below does not
    match, and a second thesis is written while the old one stays behind.

    Raising here is the only thing that makes either failure visible.
    """
    if sleeve_name not in SLEEVE_META:
        raise ValueError(
            f"No SLEEVE_META entry for DB sleeve {sleeve_name!r}. Present: "
            f"{sorted(SLEEVE_META)}. If a sleeve was renamed or split, add its "
            "meta (exit_conditions, invalidation_conditions, themes, "
            "expected_return_scenario) — do NOT fall back to an empty dict: it "
            "seeds a thesis with no conditions and orphans the old one under its "
            "previous title."
        )
    return SLEEVE_META[sleeve_name]


def _conviction(target_weight: float, sleeve_name: str) -> int:
    meta = _require_meta(sleeve_name)
    if "conviction_override" in meta:
        return meta["conviction_override"]
    return 4 if target_weight > 0.15 else 3


def seed():
    initialize_db()
    seeded = 0
    with get_connection() as conn:
        theme_map = {
            r["name"]: r["theme_id"]
            for r in conn.execute("SELECT theme_id, name FROM themes").fetchall()
        }

        sleeves = conn.execute("""
            SELECT name, target_weight, rationale
            FROM asset_classes
            WHERE parent_id IS NOT NULL
            ORDER BY sort_order
        """).fetchall()

        for sleeve in sleeves:
            sleeve_name    = sleeve["name"]
            target_weight  = sleeve["target_weight"]
            view_summary   = sleeve["rationale"] or ""
            meta           = _require_meta(sleeve_name)
            title          = f"{sleeve_name} — Strategic Allocation"

            if conn.execute(
                "SELECT thesis_id FROM theses WHERE title = ?", (title,)
            ).fetchone():
                continue

            thesis_id_row = conn.execute(
                """INSERT INTO theses
                   (title, macro_view, view_summary, conviction, level, status,
                    horizon_months, exit_conditions, invalidation_conditions,
                    expected_return_scenario, target_sleeves)
                   VALUES (?, ?, ?, ?, 'investment', 'active', 60, ?, ?, ?, ?)""",
                (
                    title,
                    view_summary,
                    view_summary,
                    _conviction(target_weight, sleeve_name),
                    meta.get("exit_conditions", ""),
                    meta.get("invalidation_conditions", ""),
                    meta.get("expected_return_scenario", ""),
                    json.dumps([sleeve_name]),
                ),
            )
            thesis_id = thesis_id_row.lastrowid

            for theme_name in meta.get("themes", []):
                tid = theme_map.get(theme_name)
                if tid:
                    conn.execute(
                        "INSERT OR IGNORE INTO thesis_themes (thesis_id, theme_id) VALUES (?, ?)",
                        (thesis_id, tid),
                    )

            seeded += 1

    if seeded == 0:
        print("Investment theses already seeded, skipping.")
    else:
        print(f"Seeded {seeded} investment theses.")


if __name__ == "__main__":
    seed()
