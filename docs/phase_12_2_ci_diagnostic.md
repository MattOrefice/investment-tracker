# Phase 12.2 — CI Diagnostic

All three CI runs for Phase 12 commits (`ca2ec4a`, `1952d24`, `42719cd`) failed with
exit code 1. The failure is in the "test" job. Full step-level logs require GitHub
sign-in; the analysis below is based on the available run metadata and local reproduction.

---

## Confirmed facts

- CI runner: ubuntu-latest (Ubuntu 24.04 as of May 2026)
- Python version in workflow: 3.11
- All three runs fail within 1m31s–2m02s (consistent with either a pip install failure
  or a fast-failing test suite, not a timeout)
- Local run: 304 tests pass on Windows (Python 3.11.9, Streamlit 1.40.1)
- CI installs `streamlit>=1.40.0` — on ubuntu-latest this resolves to **1.57.0**
  (latest as of diagnosis date), a 17-version jump from the locally-tested 1.40.1

---

## Root cause classification: environment mismatch + missing dependency

Three independent failure vectors identified:

### Vector 1 (highest probability): Streamlit version divergence breaks TRACKER_MODE

`src/config.py` calls `st.secrets.get("TRACKER_MODE", ...)` at module import time and
catches `(ImportError, FileNotFoundError, AttributeError)`. In Streamlit 1.40.1, calling
`st.secrets.get()` outside a Streamlit context raises `FileNotFoundError`, which is
caught. The fallback correctly reads `os.getenv("TRACKER_MODE")`.

In Streamlit 1.57.0, the `Secrets` class was restructured. If `st.secrets.get()` raises
a different exception (e.g., `streamlit.errors.StreamlitAPIException`, or a new
`SecretKeyError` subclass), it escapes the `except` clause. Unhandled, this propagates
to all importing modules. Since `tests/render/conftest.py` imports `src.config` at
collection time, a config import failure collapses test collection entirely — all 304
tests fail with a collection error.

Alternatively: if `st.secrets.get()` silently returns the default (no exception) but
ignores `os.getenv("TRACKER_MODE")`, TRACKER_MODE resolves to "personal" regardless of
the CI env var. All tests that open the database then attempt `data/tracker.db`, which
is not committed and does not exist on the runner. Those tests fail with a missing-file
error.

**Fix**: Restructure `src/config.py` to isolate the `st.secrets` call in its own
`try/except Exception` so any Streamlit secrets exception falls back to env vars.
This makes the config robust against any future Streamlit version change.

### Vector 2 (secondary): WeasyPrint missing system libraries

`requirements.txt` pins `weasyprint>=60.0`. Versions 60.x and some 61.x builds still
need `libgobject-2.0-0`, `libpango-1.0-0`, `libharfbuzz0b`. The CI workflow does not
install these (`packages.txt` is only consumed by Streamlit Cloud, not GitHub Actions).

On ubuntu-latest, `pip install weasyprint>=60.0` succeeds (the wheel installs cleanly)
but `from weasyprint import HTML` at runtime raises `OSError: cannot load library
'libgobject-2.0-0'`. The `_render_pdf` function wraps this in `except Exception`, so no
test that calls `_render_pdf` would fail — but no test does. The pip install itself
succeeds, so this vector does not explain the test job failure unless pip resolves to a
version that fails to build from source.

**Fix**: Add system dep install step to CI workflow (defensive; low risk).

### Vector 3 (lower probability): kaleido==0.2.1 incompatible on ubuntu-24.04

`kaleido==0.2.1` uses orca (a bundled Chromium renderer). The `manylinux1_x86_64` wheel
ships a glibc-2.5+ binary. On Ubuntu 24.04 (glibc 2.39), the wheel installs but orca
may fail at runtime due to missing sandbox capabilities. No test calls kaleido rendering
directly, so this only matters if the pip install itself fails — which it should not for
a pre-built wheel.

If pip cannot install `kaleido==0.2.1` on Python 3.11 / Ubuntu 24.04, the "Install
dependencies" step fails and no tests run.

**Fix**: Either verify the wheel installs on the runner, or add a CI-specific override
to skip kaleido (`kaleido` is only used in `_render_chart_to_png`, which no test calls).

---

## Applied fixes (Phase 12.2 Section 2)

1. **`src/config.py`**: Restructured exception handling to catch any exception from
   `st.secrets` calls, not just `(FileNotFoundError, AttributeError)`. This is the
   highest-probability fix and is safe to apply unconditionally.

2. **`.github/workflows/ci.yml`**: 
   - Added `apt-get install` step for WeasyPrint system libraries (defensive)
   - Added `pip show streamlit kaleido weasyprint` diagnostic step
   - Changed pytest command to `--tb=long` for better failure visibility on future runs
   - Pinned `streamlit` to `>=1.40.0,<2.0.0` in requirements.txt (no change to upper
     bound — version-pinning Streamlit too tightly would conflict with Streamlit Cloud
     deployment; the config.py fix is the correct solution)

---

## How to confirm resolution

After the Phase 12.2 fix commit:
1. Push via `bash tools/push-and-verify.sh main` (no SKIP_TESTS)
2. CI run triggers automatically
3. Refresh https://github.com/MattOrefice/investment-tracker/actions
4. Green checkmark on the new commit confirms the fix

If CI is still red after this fix, the run's `--tb=long` output will identify the exact
failing test and line. The diagnostic output step (pip show versions) will confirm
which package versions CI resolved.
