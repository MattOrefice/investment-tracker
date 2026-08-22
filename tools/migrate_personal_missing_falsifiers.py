"""Carry the missing-falsifiers batch into tracker.db (personal book).

Follow-up to #156. Five holdings carried no revisit/invalidation clause — VOO,
VTV, VEA, VGIT, PDBC. This batch adds an authored falsifier to each so all
thirteen ETF holdings carry a bold, own-paragraph falsifier
("\\n\\n**Would revisit if** …").

Each edit is a TARGETED substring swap (not a full seed copy) so any
personal-specific wording elsewhere in these rationales is preserved
byte-for-byte — the same discipline as the earlier migration pairs. For four of
the five the swap appends the falsifier onto the closing sentence. PDBC takes
two swaps: its opening sentence is reworded in place (the false "only broad
commodity ETF worth owning" claim -> naming the no-K-1 alternatives COMT/BCI),
and the falsifier is appended. VGIT carries two falsifiers (a second
"\\n\\n**Would also revisit if** …" paragraph).

Targets data/tracker.db EXPLICITLY (never get_db_path) so it cannot touch
demo.db regardless of TRACKER_MODE. Idempotent: re-running after the sync reports
"already migrated" and writes nothing. tracker.db is gitignored, so this sync does
not travel with the repo. Run it together with the other three personal prose
migrations via `python tools/sync_personal_prose.py`, which sequences them in
order and documents why they are manual, not auto-discovered on bootstrap.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.seed_securities import HOLDINGS

TRACKER_DB = Path(__file__).resolve().parent.parent / "data" / "tracker.db"

# In-place substring swaps (OLD must be present verbatim, else abort). For the
# appends, OLD is the closing sentence and NEW is that sentence + the falsifier
# paragraph(s); the idempotency guard checks NEW (repl) before OLD, so an
# already-appended rationale is left untouched even though OLD is a prefix of NEW.
SUBS = {
    "VOO": [
        ("liquidity I don't need.",
         "liquidity I don't need.\n\n"
         "**Would revisit if** the SEC grants ETF share-class relief to competing issuers and a materially "
         "cheaper or more tax-efficient S&P 500 vehicle results. Vanguard's patent expired in May 2023, but "
         "no competitor has launched under the structure because no relief has been granted — the edge is "
         "intact until that changes, and it is a regulatory question rather than a permanent moat."),
    ],
    "VTV": [
        ("methodology difference is intentional and defensible.",
         "methodology difference is intentional and defensible.\n\n"
         "**Would revisit if** the multi-metric value definition stopped distinguishing itself from "
         "single-metric value over a full cycle, or if profitability-integrated construction proved "
         "materially better — in which case this sleeve moves to AVLV, the domestic half of the same "
         "asymmetry AVIV's rationale names."),
    ],
    "VEA": [
        ("distributions historically.",
         "distributions historically.\n\n"
         "**Would revisit if** a cheaper or more tax-efficient developed ex-US core appeared. At 3 bps "
         "against a cap-weighted benchmark there is little else to falsify — the position is a cost choice, "
         "not a view, and the only thing that unseats it is a better instrument for the same exposure."),
    ],
    "VGIT": [
        ("that exists for drawdown buffering and rebalancing optionality, not return.",
         "that exists for drawdown buffering and rebalancing optionality, not return.\n\n"
         "**Would revisit if** nominal Treasuries stopped hedging equity drawdowns across successive "
         "inflationary episodes. 2022 was one such failure; this sleeve's case rests on it being the "
         "exception rather than the regime.\n\n"
         "**Would also revisit if** real yields rose enough to make longer duration compensated for its "
         "additional volatility."),
    ],
    "PDBC": [
        # 1. Rework the false "only …" opening in place (COMT, BCI are no-K-1 too).
        ("PDBC is the only broad commodity ETF worth owning in a taxable account because it avoids "
         "issuing a K-1 tax form.",
         "Among the few broad commodity ETFs that avoid a K-1 (COMT, BCI), PDBC is the one I hold for "
         "its liquidity and track record."),
        # 2. Append the falsifier.
        ("never as a holding.",
         "never as a holding.\n\n"
         "**Would revisit if** a broad no-K-1 commodity fund appeared at materially lower cost or with "
         "better liquidity. The 0.59% is paid for tax structure rather than strategy, so a cheaper "
         "equivalent would make this holding indefensible on its own terms."),
    ],
}


def _current(conn, ticker):
    return conn.execute(
        "SELECT holding_rationale FROM securities WHERE ticker = ?", (ticker,)
    ).fetchone()


def main() -> int:
    if not TRACKER_DB.exists():
        print(f"ABORT: {TRACKER_DB} not found — nothing to migrate on this machine.")
        return 1

    # Consistency guard: every NEW fragment must be present in the shipped seed,
    # so the migration text can't silently diverge from what actually shipped.
    seed = {h["ticker"]: h["holding_rationale"] for h in HOLDINGS}
    for t, pairs in SUBS.items():
        for _old, repl in pairs:
            if repl not in (seed.get(t) or ""):
                print(f"ABORT: {t} target text not found in seed — the migration and "
                      "src/seed_securities.py have diverged. Fix the seed/migration together.")
                return 1

    conn = sqlite3.connect(TRACKER_DB)
    try:
        changed = []
        for t, pairs in SUBS.items():
            row = _current(conn, t)
            if row is None or row[0] is None:
                print(f"ABORT: no {t} rationale row in tracker.db securities.")
                return 1
            text = row[0]
            new = text
            for old, repl in pairs:
                if repl in new:
                    continue  # already applied
                if old not in new:
                    print(f"ABORT: expected text not found in tracker.db {t} rationale — "
                          "the text has diverged; refusing to guess. Inspect manually.")
                    return 1
                new = new.replace(old, repl, 1)
            if new != text:
                conn.execute(
                    "UPDATE securities SET holding_rationale = ? WHERE ticker = ?", (new, t)
                )
                changed.append(t)

        if not changed:
            print("tracker.db missing-falsifier rationales already migrated — nothing to do.")
            return 0
        conn.commit()
        print(f"tracker.db holding_rationale updated for: {', '.join(changed)}.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
