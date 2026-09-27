"""Give the international split's three buys their position theses, in data/demo.db
(2026-09-25 audit, item 15e).

The demo split developed international into four sleeves and bought IDHQ, AVIV and AVDV
for the three tilts on its inception day, but no thesis was written for them. So the
Trade Log's claim that every trade documents a position thesis did not hold (item 10
made the claim say so). Each thesis here is condensed from its sleeve's SAA rationale,
as the SAA page renders it, and adds no claim of its own. The fields follow
src/seed_position_theses.py: the text as macro view, summary and vehicle rationale; the
conviction of the parent investment thesis; the sleeve's target weight (one fund per
sleeve); a 60-month horizon. The parent is the International Developed investment
thesis, the view these sleeves were split from.

Targets data/demo.db explicitly and runs from main() only. Inserts are keyed on the
title and skipped when it exists; each trade link must change exactly one row whose
thesis is still empty, or the run rolls back. A second run changes nothing.
"""
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"
PARENT_TITLE = "International Developed"

# ticker -> (sleeve, thesis text)
DRAFTS = {
    "IDHQ": ("International Quality",
             "Quality is the largest tilt in the US book and the largest one abroad, for the "
             "same reason. Nothing in the case for SPHQ is US-specific: its screen (return on "
             "equity, accruals, and leverage) is documented in international data on the same "
             "terms, so holding the tilt only in the US would be home bias. IDHQ tracks S&P's "
             "quality screen of developed ex-US large and mid caps, from the same issuer and "
             "index provider as SPHQ. Its benchmark, IQLT, reproduces the SPHQ-to-QUAL "
             "relationship, so selection measures the gap between two quality methodologies, "
             "not the premium itself. Would revisit if the international quality premium "
             "diverged materially from the domestic one over a full cycle."),
    "AVIV": ("International Large Value",
             "Value abroad, on the same terms VTV expresses it at home. AVIV integrates "
             "profitability into its value screen and VTV does not, so this sleeve holds the "
             "more deliberate instrument; if that asymmetry matters, the resolution is AVLV in "
             "the US rather than EFV abroad. Its benchmark, EFV, is MSCI's EAFE value index, "
             "from a different index family, so selection measures implementation rather than "
             "the premium. Would revisit if AVIV's profitability integration proved to be doing "
             "the work rather than the value screen."),
    "AVDV": ("International Small Value",
             "The small-value interaction, held abroad for the reason AVUV is held at home. It "
             "is the book's smallest sleeve because international is "
             "{{sum:International Core+International Quality+International Large Value+"
             "International Small Value}} of the portfolio and "
             "small value is 8 of 49 in the US structure: the size is arithmetic, not diminished "
             "conviction. Its benchmark, SCZ, is small blend rather than small value, because no "
             "passive international small-value index fund exists, so selection here carries "
             "the value premium itself. That premium comes from the factor tilt, not manager "
             "skill. Would revisit if the small-value interaction proved materially weaker in "
             "developed ex-US than domestically."),
}


def main() -> int:
    conn = sqlite3.connect(DEMO_DB)
    conn.row_factory = sqlite3.Row
    added = linked = 0
    try:
        with conn:
            parent = conn.execute(
                "SELECT thesis_id, conviction FROM theses WHERE title = ? AND level = "
                "'investment'", (PARENT_TITLE,)).fetchall()
            if len(parent) != 1:
                raise SystemExit(f"ABORT: {len(parent)} '{PARENT_TITLE}' investment theses")
            parent_id, conviction = parent[0]["thesis_id"], parent[0]["conviction"]
            for ticker, (sleeve, text) in DRAFTS.items():
                title = f"{sleeve} — {ticker}"
                row = conn.execute("SELECT thesis_id FROM theses WHERE title = ?",
                                   (title,)).fetchone()
                if row is None:
                    (weight,) = conn.execute(
                        "SELECT target_weight FROM asset_classes WHERE name = ? AND "
                        "parent_id IS NOT NULL", (sleeve,)).fetchone()
                    cur = conn.execute(
                        """INSERT INTO theses
                           (title, macro_view, view_summary, conviction, level, status,
                            parent_thesis_id, target_sleeves, target_weight,
                            vehicle_rationale, horizon_months)
                           VALUES (?, ?, ?, ?, 'position', 'active', ?, ?, ?, ?, 60)""",
                        (title, text, text, conviction, parent_id, json.dumps([sleeve]),
                         weight, text))
                    thesis_id = cur.lastrowid
                    added += 1
                else:
                    thesis_id = row["thesis_id"]
                trades = conn.execute(
                    "SELECT trade_id, thesis_id FROM trades WHERE ticker = ? AND "
                    "(lot_source IS NULL OR lot_source != 'drip')", (ticker,)).fetchall()
                if len(trades) != 1:
                    raise SystemExit(f"ABORT: {len(trades)} {ticker} trades, expected 1")
                if trades[0]["thesis_id"] == thesis_id:
                    continue
                n = conn.execute("UPDATE trades SET thesis_id = ? WHERE trade_id = ? AND "
                                 "ticker = ? AND thesis_id IS NULL",
                                 (thesis_id, trades[0]["trade_id"], ticker)).rowcount
                if n != 1:
                    raise SystemExit(f"ABORT: linking the {ticker} trade changed {n} rows")
                linked += 1
    finally:
        conn.close()
    print(f"{DEMO_DB.name}: {added} thesis(es) added, {linked} trade(s) linked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
