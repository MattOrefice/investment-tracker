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

## Tests
- Live-data tests (FRED etc.) are marked @pytest.mark.live_data
  and excluded from the default suite via pytest.ini. Do not
  un-exclude.

## History baseline (post-2026-06-08 reorg)
- History was reorganized 2026-06-08: 303 commits squashed into 15
  coherent prose-style milestone commits via a commit-tree chain
  (tree byte-identical, 4f3df44… preserved).
- Current main baseline HEAD: bc82d88 (Maintenance: Actions bump +
  Performance drift consolidation).
- v1.0 tag re-pointed to b7fa2c3 (the v1.0-launch milestone commit);
  original annotation preserved.
- All pre-reorg commit SHAs in earlier notes/CHANGELOG history are
  now unreachable on origin — do NOT reference them. The 15-commit
  log is the canonical history.
- origin has only: main + the v1.0 tag (nothing else).
