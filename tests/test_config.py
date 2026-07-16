"""Tests for src/config.py — is_write_enabled, mode detection, fail-closed resolution."""
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def test_is_write_enabled_returns_false_in_demo_mode(monkeypatch):
    """Write is disabled when TRACKER_MODE=demo (public Cloud deployment)."""
    import src.config as cfg
    monkeypatch.setattr(cfg, "_resolved_mode", "demo")
    assert cfg.is_write_enabled() is False


def test_is_write_enabled_returns_true_locally(monkeypatch):
    """Write is enabled when running in personal mode (local dev)."""
    import src.config as cfg
    monkeypatch.setattr(cfg, "_resolved_mode", "personal")
    assert cfg.is_write_enabled() is True


def test_is_write_enabled_consistent(monkeypatch):
    """Multiple calls return the same result (no random or stateful flipping)."""
    import src.config as cfg
    monkeypatch.setattr(cfg, "_resolved_mode", "demo")
    results = [cfg.is_write_enabled() for _ in range(5)]
    assert all(r is False for r in results)

    monkeypatch.setattr(cfg, "_resolved_mode", "personal")
    results = [cfg.is_write_enabled() for _ in range(5)]
    assert all(r is True for r in results)


# ── Fail-closed mode resolution ────────────────────────────────────────────────
# The invariant: personal mode must be asked for EXPLICITLY. Anything else — unset,
# blank, garbage, wrong type, or an unreadable secrets store — resolves to demo.
# Getting this wrong puts the Household View and write access on the public Cloud
# app, so each unsafe input is pinned individually rather than as one loop.

@pytest.mark.parametrize(
    "raw",
    [
        None,               # unset — the Cloud-secrets-missing case
        "",                 # set but empty
        "   ",              # whitespace only
        "persnal",          # typo'd personal
        "PERSONAL_",        # near-miss with a trailing char
        "garbage",          # arbitrary junk
        "prod",             # plausible-but-wrong value
        "1",                # numeric-ish
        0,                  # wrong type entirely
        True,               # wrong type, truthy
        ["personal"],       # wrong type, contains the unsafe word
        {"mode": "personal"},
    ],
)
def test_unsafe_mode_input_resolves_to_demo(raw):
    """No unset/garbage/wrong-type input may ever resolve to personal."""
    import src.config as cfg
    with pytest.warns(RuntimeWarning) if _warns(raw) else _noop():
        resolved = cfg._resolve_mode(raw)
    assert resolved == "demo"
    assert resolved != "personal"


def _warns(raw):
    """An unrecognised NON-BLANK value warns; unset/blank is a silent default."""
    return raw is not None and str(raw).strip() != ""


class _noop:
    def __enter__(self): return self
    def __exit__(self, *a): return False


@pytest.mark.parametrize("raw,expected", [
    ("personal", "personal"),
    ("demo", "demo"),
    ("PERSONAL", "personal"),   # case-insensitive
    ("  demo  ", "demo"),       # surrounding whitespace tolerated
])
def test_valid_mode_input_is_honoured(raw, expected):
    """Fail-closed must not break the explicit, correct values."""
    import src.config as cfg
    assert cfg._resolve_mode(raw) == expected


def test_default_mode_is_demo():
    """The constant itself — the whole fix rests on this not being 'personal'."""
    import src.config as cfg
    assert cfg.DEFAULT_MODE == "demo"


def test_raising_secrets_store_does_not_yield_personal(monkeypatch):
    """A secrets store that raises the expected miss falls through to the env var,
    and with no env var set that means demo — not personal."""
    import src.config as cfg
    monkeypatch.delenv("TRACKER_MODE", raising=False)
    monkeypatch.setattr(cfg, "_read_secret", lambda key: None)
    assert cfg._resolve_mode(cfg._read_secret("TRACKER_MODE") or None) == "demo"


def test_unexpected_secret_error_propagates(monkeypatch):
    """An UNEXPECTED failure while reading secrets must raise, not be swallowed
    into a silent default. Only the missing-secrets-file case is caught."""
    import src.config as cfg

    class _Boom(Exception):
        pass

    class _ExplodingSecrets:
        def get(self, key):
            raise _Boom("disk on fire")

    import streamlit as st
    monkeypatch.setattr(st, "secrets", _ExplodingSecrets())
    with pytest.raises(_Boom):
        cfg._read_secret("TRACKER_MODE")


def test_get_mode_fails_closed_on_corrupted_state(monkeypatch):
    """Even if _resolved_mode is somehow invalid at read time, get_mode returns demo."""
    import src.config as cfg
    monkeypatch.setattr(cfg, "_resolved_mode", "garbage")
    assert cfg.get_mode() == "demo"
    assert cfg.is_demo() is True
    assert cfg.is_write_enabled() is False


def test_garbage_mode_does_not_enable_writes(monkeypatch):
    """The consequence that matters: garbage must not enable writes on a public app."""
    import src.config as cfg
    monkeypatch.setattr(cfg, "_resolved_mode", cfg._resolve_mode("nonsense"))
    assert cfg.is_write_enabled() is False


def test_garbage_mode_does_not_point_at_personal_db(monkeypatch):
    """Garbage must not resolve the DB path to the real-holdings database."""
    import src.config as cfg
    monkeypatch.setattr(cfg, "_resolved_mode", cfg._resolve_mode(None))
    assert cfg.get_db_path().name == "demo.db"
