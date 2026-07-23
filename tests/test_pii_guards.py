"""PII guards — regression tripwires for the account_number removal.

Structural guards (always run in CI):
  * the accounts schema carries no account_number column,
  * parse_fidelity_csv() output carries no account_number column,
  * a freshly-created DB and a migrated DB agree on the accounts schema,
  * no account-number-shaped literal (9-digit run, letter-prefixed account of
    six or more digits, a UUID, or a broker "@" suffix) is in any tracked TEXT
    file,
  * no committed database's accounts table carries such a value in any text
    column — the binary the page renders from, which the text scan cannot see.

Local-only guard (skips when private/account_map.json is absent, e.g. in CI):
  * none of the real account numbers (map keys) appear in any tracked text file.
"""
import importlib.util
import json
import sqlite3
import subprocess
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Single source of truth for the account-number shapes: the committed-DB scanner.
# Importing it here keeps the tracked-text-file guard and the committed-DB guard
# on one regex, so a shape added in one place cannot silently lag the other. The
# ci.yml grep step is the only copy that must be hand-kept in lockstep (POSIX ERE
# — a different engine). Loaded by path so it works regardless of sys.path.
_scan_spec = importlib.util.spec_from_file_location(
    "scan_committed_dbs_pii", ROOT / "scripts" / "scan_committed_dbs_pii.py"
)
_scan_mod = importlib.util.module_from_spec(_scan_spec)
_scan_spec.loader.exec_module(_scan_mod)
_LITERAL_RE = _scan_mod.LITERAL_RE

# Extensions excluded from the text scan (binaries / committed data blobs).
_SKIP_EXT = {".db", ".csv", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf",
             ".xlsx", ".xls", ".parquet", ".zip", ".woff", ".woff2", ".ttf"}

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


def test_fresh_and_migrated_accounts_schema_agree(tmp_path):
    """A DB created from SCHEMA must match one produced by the migration.

    The full healing path is the in-db.py migrations (_drop_account_number,
    _add_included_in_household) FOLLOWED BY the phase-42 flag-default flip that
    run_pending_migrations applies. Since SCHEMA now ships the fail-conservative
    defaults (managed_by 'external', included_in_household 0), the migrated DB
    only reaches the same schema once phase-42 has recreated its accounts table —
    so this asserts the whole sequence converges, not just the first two steps.
    (phase-42 needs a real path, so the migrated DB is a temp file, not :memory:.)
    """
    from src.db import SCHEMA, _drop_account_number, _add_included_in_household

    _spec = importlib.util.spec_from_file_location(
        "m42", ROOT / "tools" / "migrate_accounts_phase42_flags.py")
    m42 = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(m42)

    fresh = sqlite3.connect(":memory:")
    fresh.executescript(SCHEMA)
    fresh_cols, fresh_idx = _accounts_schema(fresh)
    fresh.close()

    mig_path = tmp_path / "migrated.db"
    migrated = sqlite3.connect(str(mig_path))
    migrated.executescript(_PRE_SHAPE)
    _drop_account_number(migrated)
    _add_included_in_household(migrated)
    migrated.commit()
    migrated.close()
    assert m42.migrate_db(mig_path) == 1, "phase-42 should recreate a pre-flip accounts table"

    migrated = sqlite3.connect(str(mig_path))
    mig_cols, mig_idx = _accounts_schema(migrated)
    migrated.close()

    assert fresh_cols == mig_cols, (
        f"accounts columns differ:\n fresh   = {fresh_cols}\n migrated= {mig_cols}"
    )
    assert fresh_idx == mig_idx, (
        f"accounts indexes differ:\n fresh   = {fresh_idx}\n migrated= {mig_idx}"
    )


def test_literal_re_catches_account_shapes():
    """The guard must catch every account-number shape — a bare 9-digit run, a
    letter-prefixed account of six or more digits (generalized from the old fixed
    eight-digit form), a UUID, and a broker "@" suffix — while innocuous shapes
    stay clean. Manual equivalent: planting any of these literals in a tracked file
    makes the file scan below and the ci.yml grep step fail; removing it restores
    green."""
    # Assemble every probe at runtime so no matching literal sits in this file
    # (that would trip the file-scan guard below on this very file).
    nine_digits   = "1" * 9                                  # bare nine-digit run
    letter_acct_8 = "Z" + "1" * 8                            # letter + eight digits (old fixed form)
    letter_acct_6 = "Z" + "1" * 6                            # letter + six digits (newly caught)
    uuid_like     = "-".join(["a" * 8, "b" * 4, "c" * 4, "d" * 4, "e" * 12])
    broker_suffix = "acct" + "@" + "fidelity"                # broker "@" domain suffix
    for probe in (nine_digits, letter_acct_8, letter_acct_6, uuid_like, broker_suffix):
        assert _LITERAL_RE.search(probe), f"account shape must trip the guard: {probe!r}"
    # Below the six-digit floor and non-account tokens must not false-positive.
    letter_acct_5 = "Z" + "1" * 5                            # letter + five digits — under the floor
    for benign in ("Z52", "AVUV", "us_small_value", "A1", "IEMG2026", letter_acct_5):
        assert not _LITERAL_RE.search(benign), f"{benign!r} must not false-positive"


def test_no_account_number_shaped_literals_in_tracked_files():
    offenders = []
    for rel, path in _tracked_text_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for i, line in enumerate(text.splitlines(), 1):
            if _LITERAL_RE.search(line):
                offenders.append(f"{rel}:{i}")  # location only — never the literal
    assert not offenders, (
        "account-number-shaped literals (9-digit / letter-account / UUID / @-suffix) "
        "found in tracked files:\n  " + "\n  ".join(offenders)
    )


def test_committed_databases_have_no_pii_in_accounts():
    """Every committed *.db's accounts table — name, pseudonym, and every other
    text column — must be free of account-number-shaped values. This reads the
    binary the Household View renders from; the tracked-text scan above skips
    *.db, so a value baked into the file is invisible to it. Mirrors the ci.yml
    'committed databases' step — both call scan_committed_dbs()."""
    offenders = _scan_mod.scan_committed_dbs(ROOT)
    assert not offenders, (
        "account-number-shaped values in committed DB accounts tables "
        "(location only — value withheld):\n  " + "\n  ".join(offenders)
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
