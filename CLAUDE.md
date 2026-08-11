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
- 2026-07-16: the security-audit work landed at d3f5c7e (PRs #118–#122 —
  fail-closed mode resolution, ticker validation, income moved to the runtime
  profile, ignore/pin hygiene). Stated as a landmark, NOT as "the current tip":
  a tip SHA written here is false the moment the next merge lands, including the
  merge that writes it. For the tip, run `git rev-parse main`.
- 2026-07-08: a `git filter-repo` rewrite purged raw Fidelity account
  numbers from history (account-number PII removal). The LEAK COMMIT is
  431fc8f94ae51c62a752537246f24064ad42103c. It and everything after it were
  rewritten; the pre-rewrite SHAs (including the old tips 3d221fd, 554ae5f,
  1f652f5) are unreachable — do NOT reference any pre-rewrite SHA. b7fa2c3
  (v1.0) is UNCHANGED because it predates the leak; it is the reliable
  pre-leak landmark, verified an ancestor of main.
- An earlier version of this note named `fe71234` as the rewrite boundary.
  That SHA is WRONG — `git rev-list --all` finds zero commits with that
  prefix (checked 2026-07-16). Use 431fc8f… for the leak and b7fa2c3 for the
  last known-good pre-leak point. Do not resurrect fe71234.
- The purge is CONFIRMED and the ticket is CLOSED (2026-07-16). GitHub Support
  completed it; the owner verified `git fetch origin 431fc8f…` returns "not our
  ref". Independently checked 2026-07-16 that 431fc8f… is absent from this
  clone's object store (`git cat-file -e` → absent) and that no unreachable
  object here carries account data (the only `[0-9]{9,}` hits are Fama-French
  decimal tails in data/cache/*.csv stashes). Do not re-open this.
- Local mirrors: BOTH ARE GONE, verified 2026-07-16.
  ../investment-tracker-BACKUP-20260708.git and
  ../investment-tracker-backup-pre-phase26.git are both absent, and a sweep of
  C:\Users\jenni found no other bare/mirror repo. The pre-phase26 mirror
  (created 2026-05-14) DID exist and was deleted 2026-07-16 on the finding that
  it held the leak commit. Do NOT describe it as clean or as still on disk —
  both were claimed here before and both were wrong.
- Caveat on that finding, recorded so it is neither over- nor under-trusted:
  whether the mirror truly held 431fc8f… is now UNVERIFIABLE. The check used was
  a bare `git rev-parse <40-hex>`, which echoes ANY well-formed SHA back without
  testing existence — it returns a freshly-invented SHA identically (demonstrated
  2026-07-16) — and the mirror no longer exists to re-check. Deletion was correct
  regardless: a mirror spanning the leak window, unprovable either way, is not
  worth keeping. Treat it as if it held the commit.
- To test whether an object EXISTS use `git cat-file -e <sha>` or
  `git rev-parse --verify <sha>^{commit}`. NEVER bare `git rev-parse <sha>` —
  it validates the string's shape, not the object's presence, and reading it as
  proof is what produced the two wrong claims above. Creation dates prove
  nothing either: a mirror can hold commits authored long after it was made,
  because fetches keep updating it.
- v1.0 tag points to b7fa2c3 (the v1.0-launch milestone commit;
  original annotation preserved) — unchanged through both reorgs.
- All pre-reorg commit SHAs in earlier notes/CHANGELOG history are
  now unreachable on origin — do NOT reference them. The 22-commit
  log is the canonical history.
- origin has only: main + the v1.0 tag (nothing else).

## Known Issues
- holdings.py is account-scoped since PR #139 (0b2a26d, 2026-07-23): base
  reads take a required keyword-only account_id (raise on None), and
  get_portfolio_account() resolves exactly one active taxable+self
  trade-bearing account or raises. Two-account CI fixtures exist
  (tests/test_account_scoping.py; tests/test_attribution.py:1276). An
  earlier version of this bullet called holdings.py account-blind and
  demanded read-time scoping before multi-account work — both fixed by
  #139 (note: #152 scoped the PDF on top of it; #139 itself has no
  CHANGELOG entry, which is why the paper trail misleads). Surviving
  multi-account risks are UI defaults: pages/11 Execute-and-Log defaults
  to the most-trades account, pages/10's manual form to the
  first-alphabetical account, and page 10's trade table shows all
  accounts with no Account column.
- Fidelity rotates workplace-plan account identifiers. The Jul-2026 export
  changed the plan UUID vs May-2026. private/account_map.json must be
  updated when this happens; parse_fidelity_csv raises on an unmapped
  account, which is the intended behavior. The map is gitignored — carry
  new entries manually between machines.
- A running Streamlit server survives merges and hot-reloads partially,
  rendering a mix of old and new module state. Restart it after any PR
  before trusting a personal-mode render. A visual "bug" in a long-running
  session is a stale-cache artifact until proven otherwise.
- The SAME staleness applies to the deployed Streamlit Cloud demo, and there
  it presents as a page-level ImportError. Streamlit re-executes a page
  script from disk on every rerun but does NOT re-import modules already in
  sys.modules, so a process warm from before a merge runs the NEW page file
  against the OLD module object. Fired 2026-07-27 on the Performance page:
  `ImportError: cannot import name 'quarter_staleness_note' from 'src.asof'`
  at pages/2_Performance.py:12, because PR #159 (f07cfce) added that function
  to src/asof.py and to the page's import list in ONE commit — so only a
  split-brain process can see the name missing. Reboot the app from the
  Cloud dashboard; there is nothing to fix in the code. Before bisecting a
  reported page-import bug, first confirm the names actually fail to resolve
  at HEAD — `tests/test_page_imports_resolve.py` answers that in 0.24s.
- pages/14_Asset_Location.py and 13_Household_View.py raise
  `look_through_position: no composition or sleeve_category for symbol 'VEA'`
  when executed in demo mode. This is NOT a demo-data gap and must NOT be
  "fixed" by populating demo.db. Verified 2026-07-27: demo.db has no
  household tables at all, fund_compositions is empty (0 rows for any fund),
  and EVERY securities row has sleeve_category NULL (all 27 tickers) — VEA is
  merely the first symbol the iteration reaches, not a missing row. The raise
  is the fail-loud guard from b915f09 working correctly on a publicly
  unreachable path: app.py gates 13/14/15 behind `if not IS_DEMO`, so the
  error only appears when a page module is executed directly, bypassing the
  router's mode gate. Seeding that reference data into demo.db would violate
  both "personal-mode data must NEVER deploy publicly" and "demo.db schema
  changes stay additive and inert". If 13/14/15 ever need executing CI
  coverage, build a personal-mode fixture DB. This is also why the page
  import guard is static/AST-based rather than execute-every-page.
