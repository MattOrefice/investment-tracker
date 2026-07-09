"""PII guards — regression tripwires for the account_number removal.

Structural guards (always run in CI):
  * the accounts schema carries no account_number column,
  * parse_fidelity_csv() output carries no account_number column,
  * a freshly-created DB and a migrated DB agree on the accounts schema,
  * no account-number-shaped literal (9-digit run, letter-prefixed Z-account, or
    UUID) is in any tracked TEXT file.

Local-only guard (skips when private/account_map.json is absent, e.g. in CI):
  * none of the real account numbers (map keys) appear in any tracked text file.
"""
import json
import re
import sqlite3
import subprocess
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Extensions excluded from the text scan (binaries / committed data blobs).
_SKIP_EXT = {".db", ".csv", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf",
             ".xlsx", ".xls", ".parquet", ".zip", ".woff", ".woff2", ".ttf"}

# 9-digit run, a letter-prefixed account (one uppercase letter + 8 digits — the
# Fidelity "Z-account" shape, also 9 chars), OR a canonical UUID. Deliberately not
# 5-digit (would false-positive on prices/dates). Kept in lockstep with the ci.yml
# grep step of the same name.
_LITERAL_RE = re.compile(
    r"\b\d{9}\b|\b[A-Z]\d{8}\b|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# Pre-migration accounts shape (account_number present) — mirrors the old live DB.
_PRE_SHAPE = """
    CREATE TABLE accounts (
        account_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        name           TEXT NOT NULL UNIQUE,
        type           TEXT NOT NULL,
        custodian      TEXT,
        is_active      INTEGER DEFAULT 1,
        created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
        account_number TEXT,
        tax_treatment  TEXT DEFAULT 'other',
        pseudonym      TEXT,
        display_name   TEXT,
        managed_by     TEXT DEFAULT 'self'
    );
    CREATE UNIQUE INDEX ux_accounts_account_number ON accounts (account_number);
    CREATE UNIQUE INDEX ux_accounts_pseudonym      ON accounts (pseudonym);
"""


def _tracked_text_files():
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    for rel in out:
        p = ROOT / rel
        if p.suffix.lower() in _SKIP_EXT or not p.is_file():
            continue
        yield rel, p


def _accounts_schema(conn):
    """Return (columns, indexes) for the accounts table in a comparable form."""
    columns = [tuple(row) for row in conn.execute("PRAGMA table_info(accounts)")]
    indexes = {}
    for row in conn.execute("PRAGMA index_list(accounts)"):
        name, unique = row[1], row[2]
        cols = tuple(r[2] for r in conn.execute(f"PRAGMA index_info({name})"))
        indexes[name] = (unique, cols)
    return columns, indexes


# ── Structural guards ──────────────────────────────────────────────────────────

def test_accounts_schema_has_no_account_number_column():
    from src.db import SCHEMA
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(accounts)")}
    conn.close()
    assert "account_number" not in cols, "SCHEMA still declares an account_number column"
    assert {"pseudonym", "tax_treatment", "display_name", "managed_by"} <= cols


def test_parse_fidelity_output_has_no_account_number_column(tmp_path):
    from src.ingestion.fidelity import parse_fidelity_csv
    header = (
        "Account Number,Account Name,Symbol,Description,Quantity,"
        "Current Value,Cost Basis Total,Total Gain/Loss Dollar,Type"
    )
    csv = tmp_path / "p.csv"
    csv.write_text(
        header + "\n01234567,Test,VOO,VANGUARD,10,5000,4000,1000,Cash\n", encoding="utf-8"
    )
    amap = tmp_path / "m.json"
    amap.write_text(json.dumps({"01234567": "TEST_ACCT_01"}), encoding="utf-8")
    df = parse_fidelity_csv(str(csv), account_map_path=str(amap))
    assert "account_number" not in df.columns


def test_fresh_and_migrated_accounts_schema_agree():
    """A DB created from SCHEMA must match one produced by the migration."""
    from src.db import SCHEMA, _drop_account_number

    fresh = sqlite3.connect(":memory:")
    fresh.executescript(SCHEMA)
    fresh_cols, fresh_idx = _accounts_schema(fresh)
    fresh.close()

    migrated = sqlite3.connect(":memory:")
    migrated.executescript(_PRE_SHAPE)
    _drop_account_number(migrated)
    mig_cols, mig_idx = _accounts_schema(migrated)
    migrated.close()

    assert fresh_cols == mig_cols, (
        f"accounts columns differ:\n fresh   = {fresh_cols}\n migrated= {mig_cols}"
    )
    assert fresh_idx == mig_idx, (
        f"accounts indexes differ:\n fresh   = {fresh_idx}\n migrated= {mig_idx}"
    )


def test_literal_re_catches_letter_prefixed_account():
    """The guard must catch a letter-prefixed account (e.g. a Fidelity Z-account),
    not just bare 9-digit runs — a planted literal trips it, innocuous shapes don't.
    Manual equivalent: planting a one-letter-plus-eight-digit literal in a tracked
    file makes the file scan below and the ci.yml grep step fail; removing it
    restores green."""
    # Assemble the account-shaped probes at runtime so no literal account-shaped run
    # sits in this file (that would trip the file-scan guard below on this very file).
    letter_acct = "Z" + "1" * 8   # one uppercase letter + eight digits
    nine_digits = "1" * 9         # a bare nine-digit run
    assert _LITERAL_RE.search(letter_acct), "letter-prefixed account shape must trip the guard"
    assert _LITERAL_RE.search(nine_digits), "bare 9-digit run must still trip the guard"
    for benign in ("Z52", "AVUV", "us_small_value", "A1", "IEMG2026"):
        assert not _LITERAL_RE.search(benign), f"{benign!r} must not false-positive"


def test_no_account_number_shaped_literals_in_tracked_files():
    offenders = []
    for rel, path in _tracked_text_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for i, line in enumerate(text.splitlines(), 1):
            if _LITERAL_RE.search(line):
                offenders.append(f"{rel}:{i}")  # location only — never the literal
    assert not offenders, (
        "account-number-shaped literals (9-digit / UUID) found in tracked files:\n  "
        + "\n  ".join(offenders)
    )


# ── Local-only guard (real map present) ────────────────────────────────────────

def test_account_map_keys_absent_from_tracked_files():
    map_path = ROOT / "private" / "account_map.json"
    if not map_path.exists():
        pytest.skip("private/account_map.json absent (CI / no personal map)")
    keys = list(json.loads(map_path.read_text(encoding="utf-8")).keys())
    offenders = []
    for rel, path in _tracked_text_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for idx, key in enumerate(keys):
            if key in text:
                offenders.append(f"{rel} (map key #{idx})")  # never the key itself
    assert not offenders, "real account numbers leaked into tracked files:\n  " + "\n  ".join(offenders)
