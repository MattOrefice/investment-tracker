# Project guidance for Claude Code

## Commits
- Do NOT add `Co-Authored-By` trailers to any git commit in this
  project. No AI attribution in commit messages. Commits are
  authored solely by the repository owner.
- Match the existing commit style: concise subject, optional short
  body (e.g. "Phase NN: <change>").

## Verification discipline (non-negotiable)
- `git log origin/main..HEAD` returning empty is the only proof of
  a push. A local commit is not "shipped."
- For UI changes, rendered-output verification in local Streamlit
  is required; passing tests is necessary but not sufficient.
- State root cause in plain English before changes that touch
  multiple files or git history.

## Modes
- TRACKER_MODE controls demo (Streamlit Cloud, public, paper-trade)
  vs personal (local, real household data in data/tracker.db).
- Personal-mode data and the Household View page must NEVER deploy
  publicly. demo.db schema changes stay additive and inert.
- data/uploads/*.csv and data/tracker.db are gitignored — never
  commit them.
- private/account_map.json is required for personal-mode Fidelity ingest
  (maps raw account numbers to pseudonyms). Gitignored — copy manually
  between machines, never commit.

## Tests
- Live-data tests (FRED etc.) are marked @pytest.mark.live_data
  and excluded from the default suite via pytest.ini. Do not
  un-exclude.

## History baseline (post-2026-06-08 reorg)
- History was reorganized twice on 2026-06-08, both as
  tree-preserving commit-tree rebuilds (final tree byte-identical):
  first the pre-v1.0 history into 15 prose milestone commits, then
  the post-v1.0 phase work folded into prose product commits
  (34 → 22 commits; tree f16d472… preserved).
- Main tip as of 2026-07-16: d3f5c7e (the security-audit PRs #118–#122:
  fail-closed mode resolution, ticker validation, income moved to the runtime
  profile, ignore/pin hygiene). This line is a dated snapshot and goes stale on
  every merge — `git rev-parse main` is the authority, not this note.
- 2026-07-08: a `git filter-repo` rewrite purged raw Fidelity account
  numbers from history (account-number PII removal). Every commit after
  fe71234 was rewritten; their pre-rewrite SHAs (including the old tips
  3d221fd, 554ae5f, and 1f652f5) are now unreachable — do NOT reference
  any pre-rewrite SHA. b7fa2c3 (v1.0) is UNCHANGED because it predates
  the leak commit.
- The purge is CONFIRMED and the ticket is CLOSED (2026-07-16). GitHub Support
  completed it and the owner verified independently: fetching a pre-rewrite SHA
  returns "not our ref", so the objects are gone from GitHub's object store. The
  retention condition is discharged and the
  ../investment-tracker-BACKUP-20260708.git mirror is no longer on disk. Nothing
  further is owed here — do not re-open this as an open question.
- A different, older mirror DOES exist at
  ../investment-tracker-backup-pre-phase26.git (2026-05-14, 210 commits). It is
  NOT the PII backup and is not a leak: it predates the leak commit fe71234
  entirely and contains none of the pre-rewrite SHAs. Left in place; no action.
- v1.0 tag points to b7fa2c3 (the v1.0-launch milestone commit;
  original annotation preserved) — unchanged through both reorgs.
- All pre-reorg commit SHAs in earlier notes/CHANGELOG history are
  now unreachable on origin — do NOT reference them. The 22-commit
  log is the canonical history.
- origin has only: main + the v1.0 tag (nothing else).

## Known Issues
- holdings.py reads trades with no account_id filter; account_id is
  write-only metadata. Verified 2026-07-08: tracker.db has 1 account
  with trades, so the bug is latent, not active. It becomes live the
  moment a second account has trades. Any multi-account work must fix
  read-time scoping first. demo.db has one account, so CI cannot catch
  this — a two-account fixture with overlapping tickers is required.
- Fidelity rotates workplace-plan account identifiers. The Jul-2026 export
  changed the plan UUID vs May-2026. private/account_map.json must be
  updated when this happens; parse_fidelity_csv raises on an unmapped
  account, which is the intended behavior. The map is gitignored — carry
  new entries manually between machines.
- A running Streamlit server survives merges and hot-reloads partially,
  rendering a mix of old and new module state. Restart it after any PR
  before trusting a personal-mode render. A visual "bug" in a long-running
  session is a stale-cache artifact until proven otherwise.
