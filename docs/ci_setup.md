# CI Setup — GitHub Actions, Branch Protection, and Secrets

## Overview

CI runs on every push and PR to `main` via `.github/workflows/ci.yml`.  
Fast tests (no `@pytest.mark.slow`) run on `ubuntu-latest` / Python 3.11 with `TRACKER_MODE=demo`.

---

## Branch protection (GitHub → Settings → Branches)

1. Go to **Settings → Branches → Add branch protection rule**
2. Branch name pattern: `main`
3. Enable:
   - ✅ **Require status checks to pass before merging**
     - Add status check: `test` (the job name in ci.yml)
   - ✅ **Require branches to be up to date before merging**
   - ✅ **Do not allow bypassing the above settings** (prevents admins from merging red)
4. Leave "Require a pull request before merging" **off** — this is a solo project; direct push to main is intentional.

With these rules, GitHub will block merging any PR whose CI run is red.  
The pre-push test gate in `tools/push-and-verify.sh` provides the same protection for direct pushes.

---

## Secrets (for Streamlit Cloud, not GitHub Actions)

GitHub Actions tests run in `TRACKER_MODE=demo` and do not need a FRED API key.  
The only secret required by CI is none — the workflow sets `TRACKER_MODE: demo` inline.

For **Streamlit Cloud** deployment secrets (set in the Cloud dashboard, not committed):

| Secret key | Purpose |
|---|---|
| `TRACKER_MODE` | Set to `demo` |
| `FRED_API_KEY` | FRED macro data (Series: T10Y2Y, DFF, BAMLH0A0HYM2, USREC) |
| `password` | Streamlit built-in auth (single shared password) |

These go in **App settings → Secrets** on share.streamlit.io, not in `.streamlit/secrets.toml` (which is gitignored anyway). The `.streamlit/secrets.toml.example` file documents the expected structure.

### Secret resolution order in src/config.py

```
st.secrets  →  os.getenv / .env  →  hardcoded default ("personal")
```

The `try/except Exception` wrapper in `src/config.py` ensures any Streamlit version's secrets exception falls back to env vars — the fix applied in Phase 12.2 Section 1+2.

---

## Email / notification suppression

By default GitHub sends an email for every failed CI run. To suppress:

1. **Account-level**: github.com → Settings → Notifications → Email → uncheck "Failed workflows only on your default branch" or set to "Don't notify me"
2. **Per-repo**: Repository → Watch → Custom → uncheck "Actions"

For solo projects, suppressing all Actions emails and relying on the red ✗ badge on commits is cleaner than inbox noise on every push.

---

## Pre-push test gate

`tools/push-and-verify.sh` runs `python -m pytest -m "not slow"` before every `git push`.  
This catches failures before they reach CI, keeping the commit history clean.

```bash
# Normal push (runs tests first):
bash tools/push-and-verify.sh main

# Emergency bypass (creates a CI risk — use only for non-code commits like docs):
SKIP_TESTS=1 bash tools/push-and-verify.sh main
```

**Do not use `SKIP_TESTS=1` for commits that touch `src/`, `tests/`, `pages/`, or `templates/`.**

---

## Keep-awake workflow

`.github/workflows/keep-awake.yml` runs every 3 hours (plus manual dispatch) to keep the
public Streamlit Cloud demo from sleeping. Community Cloud's free tier sleeps an app after
~12h of inactivity, and a plain HTTP ping does not wake it — the request gets a static shell
while the Python backend stays asleep. The workflow instead opens the app in headless
Chromium via Playwright (`scripts/keep_awake.py`), which runs the page's JS, opens the
WebSocket, and clicks the wake button if the app has gone to sleep.

---

## Diagnosing CI failures

If a CI run shows red:

1. Go to **Actions → the failing run → test job → expand the failing step**
2. The `--tb=long` flag in ci.yml shows full tracebacks, not one-liners
3. The "Show key package versions" step shows which Streamlit/WeasyPrint/kaleido versions CI resolved — useful for environment-mismatch debugging
4. Common failure modes:
   - `st.secrets` exception escaping the `except` block → `src/config.py` version guard
   - WeasyPrint missing system libs → the `apt-get install` step in ci.yml should cover this
   - `kaleido==0.2.1` install failure on Ubuntu 24.04 → no test calls kaleido, but pip failure blocks the suite

See `docs/phase_12_2_ci_diagnostic.md` for the Phase 12 root-cause analysis and applied fixes.
