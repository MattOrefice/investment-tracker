"""Rewrite tests/fixtures/locked_figures_golden.json: every figure each committed
quarter lock's report prints, in data/demo.db and the frozen test book (#525).

tests/test_locked_figures_golden.py renders each lock and fails on any difference from
that file. This is the only writer, and it holds the rule a moved figure has to meet
(CLAUDE.md, "Locked figures"):

    A lock's figures may be rewritten only together with a change in that lock's own
    records: a restatement record, a new rule, or an input it was waiting on.

So it REFUSES, and writes nothing, when a lock's figures moved while its records did
not: that is a method change reaching a locked quarter with nothing in the lock, and
so nothing on its cover, to say so. It names every figure that moved. It accepts:

  * a lock the golden does not hold yet (a quarter locked since);
  * a lock whose records changed, whether or not its figures did (a restatement, or a
    pending input completed);
  * no change at all, in which case the file is left as it is.

ONE KNOWN FALSE POSITIVE, and its way through. A number a sentence types in its own
source ("95% CI") is read as a figure, because the render cannot tell it from a
computed one. Rewording such a sentence moves the golden with no figure moving. Run
with --wording and say what was reworded: the writer then accepts a lock whose
RUNNING-TEXT numbers moved, and still refuses if any table cell or tile did.

It renders scratch copies of the two books, offline, and never opens a committed book
for writing. How a figure is told from wording is in tests/locked_figures.py.

    python tools/update_locked_figures_golden.py
    python tools/update_locked_figures_golden.py --wording "the CI sentence names its level"
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ABOUT = ("Every figure each committed quarter lock's report prints (#525): the six sections "
         "built inside the lock and the Performance page's locked tiles. Written only by "
         "tools/update_locked_figures_golden.py; read by tests/test_locked_figures_golden.py. "
         "A figure here moves only with a change in its lock's own records (CLAUDE.md, "
         "'Locked figures').")


def _only_text(moved: "list[str]") -> bool:
    """Whether every line of differences is a section's running text: no table cell,
    no tile, no table added or removed."""
    return all(", text: " in line and ", table " not in line for line in moved)


def updated(golden: dict, rendered: dict, wording: "str | None" = None
            ) -> "tuple[dict, list[str]]":
    """(the golden for these renders, one line per lock that changed). Raises SystemExit,
    naming every figure, when a lock's figures moved and its records did not, or when a
    lock the golden holds is gone from its book. ``wording`` says what was reworded,
    and lets through a lock whose running-text numbers alone moved."""
    from tests import locked_figures as lf
    new, report, refused = {}, [], []
    for book, locks in rendered.items():
        new[book] = {}
        gone = sorted(set(golden.get(book) or {}) - set(locks))
        if gone:
            raise SystemExit(f"REFUSED: {book} no longer holds the lock(s) {gone} the golden "
                             "holds. A committed lock is not removed.")
        for quarter, lock in locks.items():
            entry = lf.golden_entry(lock)
            new[book][quarter] = entry
            was = (golden.get(book) or {}).get(quarter)
            if was is None:
                report.append(f"{book} {quarter}: new lock, {lf.count_figures(entry)} figures")
                continue
            moved = lf.differences(was, entry)
            if moved and was["state"] == entry["state"] and wording and _only_text(moved):
                report.append(f"{book} {quarter}: {len(moved)} line(s) of running-text "
                              f"numbers moved by rewording ({wording})")
            elif moved and was["state"] == entry["state"]:
                refused.append((book, quarter, moved))
            elif moved:
                report.append(f"{book} {quarter}: {len(moved)} line(s) of figures moved, "
                              "with its records")
            elif was["state"] != entry["state"]:
                report.append(f"{book} {quarter}: its records changed and no figure moved")
    if refused:
        lines = [f"REFUSED: figures moved in {len(refused)} lock(s) whose own records did "
                 "not change. A moved locked figure needs a restatement record in the lock "
                 "and a sentence on its cover, or a new rule recorded in the locks taken "
                 "after it (CLAUDE.md, 'Locked figures'). Nothing was written."]
        for book, quarter, moved in refused:
            lines.append(f"  {book} {quarter}:")
            lines.extend(f"    {line}" for line in moved)
        raise SystemExit("\n".join(lines))
    return new, report


def write(locks: dict, path: "Path | None" = None) -> None:
    from tests import locked_figures as lf
    text = json.dumps({"about": ABOUT, "locks": locks}, indent=1, ensure_ascii=False) + "\n"
    (path or lf.GOLDEN).write_bytes(text.encode("utf-8"))


def main(argv: "list[str] | None" = None) -> int:
    import argparse
    from tests import locked_figures as lf
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wording", metavar="WHAT", help="what was reworded, when the "
                        "only numbers that moved are ones a sentence types")
    args = parser.parse_args(argv)
    golden = lf.load_golden() if lf.GOLDEN.exists() else {}
    with tempfile.TemporaryDirectory() as scratch:
        rendered = lf.render_all(Path(scratch))
    new, report = updated(golden, rendered, wording=args.wording)
    if not report and new == golden:
        print(f"{lf.GOLDEN.name}: every figure is as recorded. Nothing was written.")
        return 0
    write(new)
    for line in report:
        print(line)
    total = sum(lf.count_figures(e) for locks in new.values() for e in locks.values())
    print(f"{lf.GOLDEN.name}: {sum(len(v) for v in new.values())} locks, {total} figures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
