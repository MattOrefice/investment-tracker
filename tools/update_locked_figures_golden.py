"""Rewrite tests/fixtures/locked_figures_golden.json: every figure each committed
quarter lock's report prints, and the class every label in it states, in data/demo.db
and the frozen test book (#525).

tests/test_locked_figures_golden.py renders each lock and fails on any difference from
that file. This is the only writer, and it holds the rule a moved figure has to meet
(CLAUDE.md, "Locked figures"):

    A lock's figures may be rewritten only together with a change in that lock's own
    records: a restatement record, a new rule, or an input it was waiting on.

So it REFUSES, and writes nothing, when a lock's figures moved while its records did
not: that is a method change reaching a locked quarter with nothing in the lock, and
so nothing on its cover, to say so. It names every figure that moved. A label's class
is held the same way: a significance level, a duration's side of its benchmark, a
factor sentence's reading. A label's WORDS are not in the golden, so rewording one
changes nothing here. It accepts:

  * a lock the golden does not hold yet (a quarter locked since);
  * a lock whose records changed, whether or not its figures did (a restatement, or a
    pending input completed);
  * an entry written before labels were read, or before figures printed in words were
    (an ordinal, a date's month, a number spelled out: #541), or before quarter labels
    were ("Q2 2026": #553), which gains them with no figure moved;
  * no change at all, in which case the file is left as it is.

ONE KNOWN FALSE POSITIVE, and its way through. A number a sentence types in its own
source ("95% CI", or spelled out, "the one equity region") is read as a figure, because
the render cannot tell it from a computed one. Rewording such a sentence moves the
golden with no figure moving. Run with --wording and say what was reworded: the writer
then accepts a lock whose RUNNING-TEXT numbers, figures in words or quarter labels
moved, and still refuses if any table cell, tile or label's class did.

THE PERSONAL BOOK (#418, decided by the owner on October 17, 2026). When data/tracker.db
exists, its locks are rendered too and written, under the same rule, to
data/private/locked_figures_golden.json, which is gitignored and never committed. Both
files are checked before either is written, so a refusal in one writes neither. Where
tracker.db is absent (CI, a fresh clone) the personal golden is left as it is.

It renders scratch copies of the books, offline, and never opens a book for writing.
How a figure is told from wording is in tests/locked_figures.py.

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
         "built inside the lock and the Performance page's locked tiles, the class "
         "each label in those sections states, each figure they print in words "
         "(#541) and each quarter's label they print (#553). Written only by "
         "tools/update_locked_figures_golden.py; read by tests/test_locked_figures_golden.py. "
         "A figure here moves only with a change in its lock's own records (CLAUDE.md, "
         "'Locked figures').")
PERSONAL_ABOUT = ("Every figure each quarter lock in the personal book (data/tracker.db) "
                  "prints, read as tests/fixtures/locked_figures_golden.json reads the "
                  "committed books' (#418). NEVER COMMITTED: data/private/ is gitignored. "
                  "Written only by tools/update_locked_figures_golden.py; read by "
                  "tests/test_locked_figures_golden.py. A figure here moves only with a "
                  "change in its lock's own records (CLAUDE.md, 'Locked figures').")


def _only_text(moved: "list[str]") -> bool:
    """Whether every line of differences is a section's running text, its numbers or
    its figures in words: no table cell, no tile, no table added or removed, no label's
    class."""
    return all((", text: " in line or ", in words: " in line or ", quarters: " in line)
               and ", table " not in line for line in moved)


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
            elif not lf.holds_labels(was):
                report.append(f"{book} {quarter}: {lf.count_labels(entry)} labels recorded "
                              "for the first time, no figure moved")
            elif not lf.holds_in_words(was):
                report.append(f"{book} {quarter}: {lf.count_in_words(entry)} figures in words "
                              "recorded for the first time, no figure moved")
            elif not lf.holds_quarters(was):
                report.append(f"{book} {quarter}: {lf.count_quarters(entry)} quarter labels "
                              "recorded for the first time, no figure moved")
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


def write(locks: dict, path: "Path | None" = None, about: str = ABOUT) -> None:
    from tests import locked_figures as lf
    text = json.dumps({"about": about, "locks": locks}, indent=1, ensure_ascii=False) + "\n"
    (path or lf.GOLDEN).write_bytes(text.encode("utf-8"))


def _shown(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _finish(path: Path, about: str, golden: dict, new: dict, report: "list[str]") -> None:
    """Write one golden if anything in it changed, and say what did."""
    from tests import locked_figures as lf
    if not report and new == golden:
        print(f"{_shown(path)}: every figure is as recorded. Nothing was written.")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    write(new, path, about)
    for line in report:
        print(line)
    total = sum(lf.count_figures(e) for locks in new.values() for e in locks.values())
    labels = sum(lf.count_labels(e) for locks in new.values() for e in locks.values())
    in_words = sum(lf.count_in_words(e) for locks in new.values() for e in locks.values())
    quarters = sum(lf.count_quarters(e) for locks in new.values() for e in locks.values())
    print(f"{_shown(path)}: {sum(len(v) for v in new.values())} locks, {total} figures, "
          f"{labels} labels, {in_words} figures in words, {quarters} quarter labels.")


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
        personal = lf.render_personal(Path(scratch))
    new, report = updated(golden, rendered, wording=args.wording)
    if personal is not None:
        mine = lf.load_golden(lf.PERSONAL_GOLDEN) if lf.PERSONAL_GOLDEN.exists() else {}
        new_mine, report_mine = updated(mine, personal, wording=args.wording)
    _finish(lf.GOLDEN, ABOUT, golden, new, report)
    if personal is None:
        print(f"{_shown(lf.PERSONAL_BOOK)} is absent: the personal golden was not rendered "
              "and is left as it is.")
    else:
        _finish(lf.PERSONAL_GOLDEN, PERSONAL_ABOUT, mine, new_mine, report_mine)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
