"""Carry the copy corrections of the 2026-09-25 audit's resumed run (item C) into
data/demo.db, for the cells no other tool derives.

The Trade Log renders thesis prose seeded into demo.db once, so a corrected source
reaches the committed book only through a tool. The sleeve rationales, the fund
rationales and most thesis cells have theirs (tools/migrate_saa_rationale_copy.py,
tools/migrate_markets_copy.py, tools/migrate_operations_copy.py). This one carries the
rest, each from the source that now holds it:

  * theses 24 and 26 (International Quality — IDHQ, International Small Value — AVDV):
    tools/link_intl_split_theses.py's text, where #415's "by a wide margin" and "much
    weaker" are reverted to "materially";
  * thesis 20 (TIPS — SCHP) was carried here from SCHP's fund rationale until #421's
    pass gave it its own text, with the fees read from the data: it is in
    tools/migrate_operations_copy.py now, and renders as the fund rationale does;
  * theses 7 and 17: the sleeves they name. Both were written for the undivided
    International Developed sleeve and now condense International Core's rationale
    (#406 item 4), so 17 names International Core, in its title too, and 7, the parent
    of the four international position theses, names the four sleeves;
  * thesis 17's target_weight (a later pass, #436's): seeded as 0.2, the undivided
    International Developed sleeve's target, and never re-derived. It is now the rule
    src/seed_position_theses.py seeds with, the sleeve's target over its funds:
    International Core's, whose one fund is VEA, as theses 24 to 26 hold theirs.
    Nothing displays the field today (the Trade Log selects it and never shows it); a
    wrong value would be displayed the day something reads it;
  * thesis 23's target_weight (#447): 0.02, Cash's old 2% target, by the same rule now
    0.0, the Cash / SPAXX sleeve's target over its one fund, SPAXX.

Each UPDATE is keyed on the thesis id and the cell's current value, must change exactly
one row, or the run rolls back; a second run changes nothing. Targets data/demo.db
explicitly and runs from main() only. The personal book is not touched.
"""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"
sys.path.insert(0, str(ROOT))

INTL_SLEEVES = ["International Core", "International Quality", "International Large Value",
                "International Small Value"]

# One literal statement per column, so a scan of the tools for UPDATE statements sees
# each one whole.
UPDATES = {
    "title":
        "UPDATE theses SET title = ? WHERE thesis_id = ? AND title = ?",
    "target_sleeves":
        "UPDATE theses SET target_sleeves = ? WHERE thesis_id = ? AND target_sleeves = ?",
    "macro_view":
        "UPDATE theses SET macro_view = ? WHERE thesis_id = ? AND macro_view = ?",
    "view_summary":
        "UPDATE theses SET view_summary = ? WHERE thesis_id = ? AND view_summary = ?",
    "vehicle_rationale":
        "UPDATE theses SET vehicle_rationale = ? WHERE thesis_id = ? AND vehicle_rationale = ?",
    "target_weight":
        "UPDATE theses SET target_weight = ? WHERE thesis_id = ? AND target_weight = ?",
}


def _draft(ticker: str) -> str:
    spec = importlib.util.spec_from_file_location(
        "link_intl_split_theses", ROOT / "tools" / "link_intl_split_theses.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool.DRAFTS[ticker][1]


def _position_target(sleeve: str) -> float:
    """The seed's rule for a position thesis: its sleeve's target over the sleeve's funds.
    International Core holds one fund, VEA. SPAXX is a synthetic holding outside the
    securities table, and src.seed_position_theses gives its thesis the Cash / SPAXX
    sleeve's target itself."""
    from src.seed_saa import SUB_CLASSES
    from src.seed_securities import HOLDINGS
    target = next(s["target_weight"] for s in SUB_CLASSES if s["name"] == sleeve)
    if sleeve == "Cash / SPAXX":
        return target
    funds = [h for h in HOLDINGS if h.get("asset_class") == sleeve]
    return target / len(funds)


def cells() -> "dict[int, dict[str, object]]":
    idhq, avdv = _draft("IDHQ"), _draft("AVDV")
    return {
        7:  {"target_sleeves": json.dumps(INTL_SLEEVES)},
        17: {"title": "International Core — VEA",
             "target_sleeves": json.dumps(["International Core"]),
             "target_weight": _position_target("International Core")},
        23: {"target_weight": _position_target("Cash / SPAXX")},
        24: {"macro_view": idhq, "view_summary": idhq, "vehicle_rationale": idhq},
        26: {"macro_view": avdv, "view_summary": avdv, "vehicle_rationale": avdv},
    }


def main() -> int:
    conn = sqlite3.connect(DEMO_DB)
    changed = 0
    try:
        with conn:
            for thesis_id, wanted in cells().items():
                for col, text in wanted.items():
                    row = conn.execute(f"SELECT {col} FROM theses WHERE thesis_id = ?",
                                       (thesis_id,)).fetchone()
                    if row is None:
                        raise SystemExit(f"ABORT: thesis {thesis_id} is missing")
                    if row[0] == text:
                        continue
                    n = conn.execute(UPDATES[col], (text, thesis_id, row[0])).rowcount
                    if n != 1:
                        raise SystemExit(f"ABORT: thesis {thesis_id}.{col} changed {n} rows")
                    changed += 1
    finally:
        conn.close()
    print(f"{DEMO_DB.name}: {changed} thesis cell(s) changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
