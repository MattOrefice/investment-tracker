"""Carry the international-tilt prose batch into tracker.db (personal book).

Three authored tilt rationales (IDHQ, AVIV, AVDV) go from NULL -> the step's text, and
two shipped rationales are edited in place to drop false/colliding superlatives:
  - AVUV: "highest-conviction factor pick in the portfolio" -> "in the US book"
          (resolves the collision with AVDV's own highest-conviction claim);
          ER sentence "is the highest in the portfolio" -> "is a real cost …
          rather than index replication".
  - IEMG: "the most egregious benchmark-vs-holding gap in the portfolio" ->
          "the largest same-index fee gap in the portfolio at 61 bps".

The two in-place edits are TARGETED substring swaps (not a full seed copy) so any
personal-specific wording elsewhere in those rationales is preserved byte-for-byte
— the same discipline as tools/migrate_personal_vea_rationale.py. The three NULL
tilt rationales are set to TARGET below, the seed's text when this step shipped
(authored prose, identical in both books), held as data since #732.

Targets data/tracker.db EXPLICITLY (never get_db_path) so it cannot touch
demo.db regardless of TRACKER_MODE. Idempotent: re-running after the sync reports
"already migrated" and writes nothing. tracker.db is gitignored, so this sync does
not travel with the repo. Run it together with the other three personal prose
migrations via `python tools/sync_personal_prose.py`, which sequences them in
order and documents why they are manual, not auto-discovered on bootstrap.
"""
import hashlib
import sqlite3
from pathlib import Path

TRACKER_DB = Path(__file__).resolve().parent.parent / "data" / "tracker.db"

# NULL -> TARGET (authored prose, no personal-specific content): the seed's text at
# 4abdb40, when this step shipped.
SET_FROM_TARGET = ("IDHQ", "AVIV", "AVDV")

# A LATER TEXT IS ALREADY SUPERSEDED (#732, decided by the owner on November 19, 2026),
# the rule step 5 takes for the #710 sentence. The seed has been edited since this step
# was written, so its texts are held here as data, never read from today's seed: a
# step that read today's seed went stale the next time the seed was edited, and the
# runner stopped here. LATER holds the SHA-256 of every text the seed held for each
# ticker after this step's commit, read from the seed's git history. A book holding one
# of them has moved past this step, so it is left as it is and nothing is written.
TARGET = {
    'IDHQ': "IDHQ tracks S&P's quality screen of developed ex-US large and mid caps: the same issuer as SPHQ, the same index provider, the same three fundamental measures — return on equity, accruals, and leverage. That symmetry is the point. The US quality sleeve and the international one are the same screen applied to different regions, so a divergence in their results is a fact about the regions rather than about two vendors' definitions of quality.\n\nThe benchmark is IQLT, iShares' MSCI quality index abroad, which reproduces the SPHQ-to-QUAL relationship exactly. Cost is a wash — 0.29% against IQLT's 0.30% — so this is not a fee decision; it is a methodology one, and it is the same methodology decision already made domestically.\n\nThe known asymmetry: S&P classifies South Korea as developed where MSCI does not, so IDHQ holds Korea and IQLT doesn't. That is a real universe difference, disclosed on the Factor Profile page, and it moves with each semi-annual reconstitution rather than sitting at a fixed weight.\n\n**Would revisit if** S&P's quality screen diverged materially from MSCI's over a full cycle, which would mean the two are measuring different things and the SPHQ-IDHQ symmetry is cosmetic.",
    'AVIV': "Value abroad, from the same shop that runs the US small-value sleeve. Avantis integrates profitability into its value screen rather than sorting on price alone — the same construction as AVUV, applied to international large caps.\n\nThat is worth naming as an asymmetry rather than hiding: VTV, the US large-value holding, tracks a plain cap-weighted value index and does not integrate profitability. So the international sleeve holds the more deliberate instrument. If that asymmetry matters, the resolution is AVLV in the US rather than EFV abroad — the domestic sleeve is the less considered one, not this one.\n\nThe benchmark is EFV, MSCI's EAFE value index. Holding and benchmark come from different index families, exactly as VTV and IWD do, so selection effect measures implementation rather than the value premium itself. At 0.25% against EFV's 0.33%, the profitability integration costs nothing relative to the passive alternative.\n\n**Would revisit if** the profitability integration proved to be doing the work rather than the value screen — in which case the US sleeve moves to AVLV rather than this one moving to EFV.",
    'AVDV': "The small-value interaction abroad, held for the reason AVUV is held at home: the size premium concentrates almost entirely in profitable small-value firms, and unprofitable small caps are what made naive small-cap exposure look weak for fifteen years.\n\nThe benchmark comparison here is weaker than the other two, deliberately. SCZ is iShares' EAFE small-cap index — small blend, not small value — because no passive international small-value index fund exists at acceptable cost. So the benchmark captures the size dimension and not the value tilt, and selection effect for this sleeve carries the value premium itself rather than measuring implementation. This is the same compromise the US sleeve already makes against IWM, with the same consequence: read the factor exhibit, not attribution, for whether the tilt is being paid.\n\nAt 0.36% it is among the portfolio's costliest positions, and its highest-conviction one — the fee buys genuine factor exposure rather than index replication. AVDV holds no South Korea — verified at the position level — so the universe mismatch that inflates the developed-core residual doesn't apply here.\n\n**Would revisit if** the small-value interaction proved materially weaker in developed ex-US than domestically, or if a passive international small-value fund appeared at materially lower cost.",
}

LATER = {
    'IDHQ': {
        '042337a98cb208dc2367642a3dd2252f089f97888cfe5f2cc691e1e4703f1f6a',  # seed at fc0a9ca, 2026-09-27
        'c936bfd52588218b677f5fed254b4c764112179b985685388552d96a33d5106f',  # seed at e1674e5, 2026-09-28
        'fb4e5f804cd503ae6271cb15ab5215edc0d4f7205d7869ce6eb29de783d9831a',  # seed at da42531, 2026-10-15
    },
    'AVIV': {
        'd8d3d3133f08562d16b0da80f054a211d765c14bef5551664bd792ff9038c41d',  # seed at fc0a9ca, 2026-09-27
        '26e158b278d044ad35969bf056191a403fc6b14d283c3c75cade3b8dfa0f6d43',  # seed at e1674e5, 2026-09-28
    },
    'AVDV': {
        'a993aa9d54eea478f0f5025a7b77ee0da729826d72249efef320713289cafb1a',  # seed at fc0a9ca, 2026-09-27
        'd31b59bd152b79fe208c98f5751603ef846731bfa7a96eac859bfcb81a2b6fb8',  # seed at e1674e5, 2026-09-28
        'ec2e0863bfeb19234145eb5df2d590310819da3929d61effa1c675d552d9226c',  # seed at 4513c47, 2026-10-15
    },
    'AVUV': {
        '81c4501a9cb514d880bfdea948d3be4cfb071aca5ac5607b4bfdd8ea39e9fef2',  # seed at 06dd929, 2026-07-26
        '501e1ec1fe54e28ed0f512c6bad23ccdb85e27bde0d35012b4c5563d22c18ee8',  # seed at fc0a9ca, 2026-09-27
        '3f4b113e065db43fe811c2dfff92b67488a09f17bb16c16a717533259df22cef',  # seed at e1674e5, 2026-09-28
    },
    'IEMG': {
        'b3b7575223cbde4cc3ff8e9d1e41bfc1aef4e6e97c32cc5f6d7fce2f36a1d713',  # seed at 06dd929, 2026-07-26
        '483a2e87ec6cc74739ad792d468f4def6dd98d7c049ff9170448f469ea43891e',  # seed at 624b2c0, 2026-09-26
        '1f3e3bbdb44fc6103408ab426b63b9ed08b82fb35d2e3913ddeec96867aa1c9a',  # seed at fc0a9ca, 2026-09-27
        '52560b364c60e6564ce69a1733394922d5520cdcb1e6abfc8d950f69fe62b33e',  # seed at e1674e5, 2026-09-28
    },
}

# In-place substring swaps (OLD must be present verbatim, else abort).
SUBS = {
    "AVUV": [
        ("AVUV is the highest-conviction factor pick in the portfolio.",
         "AVUV is the highest-conviction factor pick in the US book."),
        ("The 0.25% ER is the highest in the portfolio but buys genuine factor exposure.",
         "The 0.25% ER is a real cost but buys genuine factor exposure rather than index replication."),
    ],
    "IEMG": [
        ("seven times more expensive and the most egregious benchmark-vs-holding gap in the portfolio.",
         "seven times more expensive, and the largest same-index fee gap in the portfolio at 61 bps."),
    ],
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _superseded(ticker: str, text) -> bool:
    """A text the seed held for ``ticker`` after this step: the book has moved past it."""
    return text is not None and _sha(text) in LATER.get(ticker, ())


def _current(conn, ticker):
    row = conn.execute(
        "SELECT holding_rationale FROM securities WHERE ticker = ?", (ticker,)
    ).fetchone()
    return row  # None if no row; (value,) otherwise


def main() -> int:
    if not TRACKER_DB.exists():
        print(f"ABORT: {TRACKER_DB} not found — nothing to migrate on this machine.")
        return 1

    conn = sqlite3.connect(TRACKER_DB)
    try:
        changed, superseded = [], []

        # 1. NULL tilt rationales -> TARGET.
        for t in SET_FROM_TARGET:
            row = _current(conn, t)
            if row is None:
                print(f"ABORT: no {t} row in tracker.db securities.")
                return 1
            cur = row[0]
            if cur == TARGET[t]:
                continue
            if _superseded(t, cur):
                superseded.append(t)
                continue
            if cur is not None:
                print(f"ABORT: {t} already has a rationale in tracker.db that is neither this "
                      "step's nor a later seed text — "
                      "the text has diverged; refusing to overwrite. Inspect manually.")
                return 1
            conn.execute(
                "UPDATE securities SET holding_rationale = ? WHERE ticker = ?", (TARGET[t], t)
            )
            changed.append(t)

        # 2. In-place substring swaps on the two shipped rationales.
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
                    print(f"ABORT: expected sentence not found in tracker.db {t} rationale — "
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
            print("tracker.db international-tilt rationales already migrated — nothing to do.")
            return 0
        conn.commit()
        print(f"tracker.db holding_rationale updated for: {', '.join(changed)}.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
