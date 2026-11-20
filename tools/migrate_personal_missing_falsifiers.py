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
import hashlib
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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


# A LATER TEXT IS ALREADY SUPERSEDED (#732, decided by the owner on November 19, 2026),
# the rule step 5 takes for the #710 sentence. The seed has been edited since this step
# was written, so its texts are held here as data, never read from today's seed: a
# step that read today's seed went stale the next time the seed was edited, and the
# runner stopped here. LATER holds the SHA-256 of every text the seed held for each
# ticker after this step's commit, read from the seed's git history. A book holding one
# of them has moved past this step, so it is left as it is and nothing is written.
LATER = {
    'VOO': {
        '0ef8bbdd732c4fb214936f36a65eae8f164eab12bcc5d1dc421207ce663a3199',  # seed at fc0a9ca, 2026-09-27
        '620a453c2d28015d62ec4a49790cd972e72af4ba352896586a6948a0b9e9a505',  # seed at e1674e5, 2026-09-28
    },
    'VTV': {
        '460db0e8a22c5bad920d88fc87f62a732d700233b76210f07c31a637f7713359',  # seed at fc0a9ca, 2026-09-27
        'adfc4a51b43e1db248aad18aa295414b2f9423a725f6000013a9242d5fefa488',  # seed at e1674e5, 2026-09-28
        'ae8bdaa4d95d0bed662f60affcbf5ab021250608391458bbdef4e7373f358fbc',  # seed at 4513c47, 2026-10-15
        '3f5d9b79173d10d60ec699d7602d6c4dbb31a063e371cd61d197f50ab887449c',  # seed at da42531, 2026-10-15
    },
    'VEA': {
        '6a3f0ec63f792a6bb9853423350fdd372537ce9400b0ddc7bbccb3f4117cf191',  # seed at fc0a9ca, 2026-09-27
        '19f3b603a7638d1f42171cb336013e1b391d3e8d46616de16526bc5b434ed942',  # seed at e1674e5, 2026-09-28
    },
    'VGIT': {
        'd02ce2e9310bc70edb476604c161ae03d8cd5a6a0dddbd92466086a11507bce2',  # seed at 776e1e6, 2026-08-22
        '21206060bda1652f852b6d09c21e3c750b8608e67deff54fd8621354a4ae458f',  # seed at fc0a9ca, 2026-09-27
        'da67fde28b7da1d18d0273ef03df282e1964bc7c8b1dbdb4e813272885ce145b',  # seed at e1674e5, 2026-09-28
        '6890faaea4d9e1463c5701de1a2f27376e45bc063729111188daf4e6f59cfefb',  # seed at 002a218, 2026-11-17
    },
    'PDBC': {
        '601e267ab19727008980721acb13c543119831b192bd60c4a08281e18c647ffe',  # seed at 624b2c0, 2026-09-26
        'b2329b481d0d7c78218a7a3632689e2f18eb42d30a6d07f509731fc81f233bab',  # seed at fc0a9ca, 2026-09-27
        '1ab0114a3ec55969400cb9c2c7336761095dbd11d93b01b2e14e8027afd720ed',  # seed at e1674e5, 2026-09-28
    },
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _superseded(ticker: str, text) -> bool:
    """A text the seed held for ``ticker`` after this step: the book has moved past it."""
    return text is not None and _sha(text) in LATER.get(ticker, ())


def _current(conn, ticker):
    return conn.execute(
        "SELECT holding_rationale FROM securities WHERE ticker = ?", (ticker,)
    ).fetchone()


def main() -> int:
    if not TRACKER_DB.exists():
        print(f"ABORT: {TRACKER_DB} not found — nothing to migrate on this machine.")
        return 1

    conn = sqlite3.connect(TRACKER_DB)
    try:
        changed, superseded = [], []
        for t, pairs in SUBS.items():
            row = _current(conn, t)
            if row is None or row[0] is None:
                print(f"ABORT: no {t} rationale row in tracker.db securities.")
                return 1
            text = row[0]
            if _superseded(t, text):
                superseded.append(t)
                continue
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

        if superseded:
            print(f"superseded by a later seed text, left as is: {', '.join(superseded)}.")
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
