# Project guidance for Claude Code

## Commits
- Do NOT add `Co-Authored-By` trailers to any git commit in this
  project. No AI attribution in commit messages. Commits are
  authored solely by the repository owner.
- Match the existing commit style: concise subject, optional short
  body (e.g. "Phase NN: <change>").

## Verification discipline (non-negotiable)
- The push proof has TWO forms and they are not interchangeable. Run
  the one that matches the stage, and say which ref you checked:
    - pre-merge, on a feature branch:
      `git log origin/<branch>..HEAD` empty means pushed.
    - post-merge, on main:
      `git log origin/main..HEAD` empty means shipped.
  An earlier version of this bullet named only the second and called
  it "the only proof of a push". That is right about merges and wrong
  about branches: run it pre-merge and it PRINTS the commit — correctly,
  since origin/main does not have it yet — and the output reads exactly
  like a failed push. A local commit is still not "shipped."
- After a merge, also confirm the merge tree matches the tip CI passed
  on: `git rev-parse main^{tree}` vs `<verified-tip>^{tree}`. Equal
  trees are what make CI's green a statement about what actually
  landed. Two PRs merged back to back produce a tree no CI run tested.
- For UI changes, rendered-output verification in local Streamlit
  is required; passing tests is necessary but not sufficient.
- `git diff --stat` CANNOT tell you whether a committed SQLite file
  changed. It prints `Bin 28254208 -> 28254208 bytes` whether demo.db
  is clean or carries hundreds of rows of drift — the size is stable
  because sqlite reuses pages. Use `python tools/fingerprint_db.py
  data/demo.db`, which compares per-table row counts and content
  hashes and names both sides of the comparison. Run it before every
  commit that ran the suite, and after every merge. This has bitten
  twice, the second time with the trap already known — knowing it does
  not prevent it, only the mechanical check does.
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
- A TEST-COUNT BASELINE IS MEANINGLESS WITHOUT ITS HARNESS. Quote
  them together, and match the harness on BOTH sides of any
  comparison — including a run made only to confirm nothing moved.
  There are two harnesses and they produce different numbers from the
  same tree:
    - LOCAL, personal mode: plain `python -m pytest -q`. `.env` pins
      TRACKER_MODE=personal, so DB_PATH=data/tracker.db and the
      personal fixtures are present. Guards down (see below).
    - CI, demo mode: `python -m pytest --tb=long -q` with
      TRACKER_MODE=demo and NO data/tracker.db (it is gitignored, so
      tests keyed on the personal cache skip). This is the only
      demo-mode full suite that matters — verify it by SHA rather than
      reproducing it locally.
  No count is written here on purpose: like a tip SHA, a baseline
  figure is false the moment the next PR adds a test. Re-measure and
  quote the harness with it.
- RECORD THE COLLECTION TOTAL ALONGSIDE THE SPLIT, and confirm a
  recorded baseline belongs to the tree it names — re-measure after the
  final commit, not before it. `failed + passed + skipped` must equal
  `python -m pytest --collect-only -q | tail -1`'s selected count; if
  it does not, the figure is incomplete and any delta computed from it
  inherits the gap. The second clause is the one that bites: a suite
  run taken mid-session measures an INTERMEDIATE tree, and a review
  round that adds tests afterwards leaves the recorded number
  attributed to a commit it never described. That happened at #246 —
  1808 accounted for against 1810 collected, because two tests were
  added after the measurement (#247).
- "Guards" means the read-only attribute set on data/demo.db,
  data/tracker.db and every `git ls-files data` entry, to prove a
  diagnostic did not mutate tracked data. GUARDS-UP AND THE FULL SUITE
  ARE MUTUALLY EXCLUSIVE — see Known Issues; do not read a guards-up
  failure count as movement.
- Two red tests are DELIBERATE and load-bearing: #177
  (`test_exactly_the_saa_tickers_are_flagged`, the only live signal
  that the personal book has diverged from its documented taxonomy)
  and the twelve mode-sensitive render tests enumerated in #187. Do
  not "fix" either. Any failure beyond those is real.

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
- A READ-ONLY GUARD AND THE PERSONAL-MODE SUITE CANNOT BOTH HOLD, so a
  guards-up failure count is a different configuration and not movement.
  `_auto_migrate` still performs a DDL write on any DB carrying the
  vestigial `accounts.account_number` column: `_drop_account_number`
  (src/db.py:146-157) runs `DROP INDEX IF EXISTS` + `ALTER TABLE ... DROP
  COLUMN` when it finds the column. With guards up that write fails, and
  tests/test_attribution.py errors during COLLECTION at src/db.py:155
  ("attempt to write a readonly database") — so a guards-up FULL-SUITE
  number does not exist to be quoted. Seven pages/14 render tests fail the
  same way. Nothing is broken: the column was all-NULL where it was found,
  no account numbers, and the migration itself works. Note the condition —
  once a DB has been opened writably the column is gone and the write
  stops happening, so this reproduces only on a DB restored from an older
  copy, a second machine, or a clone seeded pre-migration. It also
  corrects a claim made after PR #175 that the last write-on-touch channel
  had been closed: #175 closed the others and the claim was generalised
  past its evidence. Tracked as #232.
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
  "fixed" by populating demo.db. Verified 2026-07-27 and re-verified
  2026-08-17: fund_compositions is empty (0 rows for any fund) and EVERY
  securities row has sleeve_category NULL (27 of 27 tickers) — VEA is
  merely the first symbol the iteration reaches, not a missing row.
  An earlier version of this bullet said "demo.db has no household tables
  at all". That is not the gap: the table LISTS are identical apart from
  quarter_snapshots (which only demo.db has), and demo.db's accounts table
  carries all five household columns — included_in_household, pseudonym,
  managed_by, display_name, tax_treatment. The difference is ROWS, not
  schema: 1 account in demo.db against 7 in tracker.db. Said the old way it
  sends a reader looking for missing tables. The raise
  is the fail-loud guard from b915f09 working correctly on a publicly
  unreachable path: app.py gates 13/14/15 behind `if not IS_DEMO`, so the
  error only appears when a page module is executed directly, bypassing the
  router's mode gate. Seeding that reference data into demo.db would violate
  both "personal-mode data must NEVER deploy publicly" and "demo.db schema
  changes stay additive and inert". If 13/14/15 ever need executing CI
  coverage, build a personal-mode fixture DB. This is also why the page
  import guard is static/AST-based rather than execute-every-page.
