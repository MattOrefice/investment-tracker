"""Tests for src/config.py — is_write_enabled and mode detection."""
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
