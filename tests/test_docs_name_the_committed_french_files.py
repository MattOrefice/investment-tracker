"""The docs and the Factor Profile caption name the committed Ken French files (#527).

Four statements had outlived the code they described:

  * README.md said the French factors are "Downloaded and cached per regression run";
  * docs/architecture.md gave them a 7-day TTL at data/ff_factors_*.csv and said the
    ZIPs are downloaded on demand;
  * the Factor Profile page's caption named data/ff_factors_us.csv and two more paths
    under data/ that hold no committed file (.gitignore lists them);
  * docs/operational_checks.md cited _SAA_US and _FI_WEIGHTS by line number.

The three files are committed inputs under data/cache/ with no TTL: src/factors.py reads
them and never fetches, and tools/refresh_market_data.py is their only writer
(tests/test_market_data_immutability.py holds the runtime to that). The caption now
reads the paths the loaders read, so it cannot name a file they do not. It names the
writer by its path outside the public demo only: the demo's pages print no maintainer
tool's path (the 2026-09-25 audit's item 4).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from src import factors
from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers

ROOT = Path(__file__).resolve().parent.parent
DOCS = ("README.md", "docs/architecture.md")
PAGE = "pages/4_Factor_Profile.py"
WRITER = "tools/refresh_market_data.py"
# What each place said before, and the path prefix that held no committed file.
GONE = ("7-day TTL", "per regression run", "downloaded on demand", "older than 7 days",
        "35-day", "refreshed by hand", "data/ff_")


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _statements(rel: str) -> "list[str]":
    """The doc as statements: a table row or a diagram line each, and prose by sentence
    (a sentence wraps where its file wraps it)."""
    out: "list[str]" = []
    for block in re.split(r"\n\s*\n", _read(rel)):
        lines = block.splitlines()
        if any(line.lstrip().startswith("|") or "→" in line for line in lines):
            out.extend(lines)
        else:
            out.extend(re.split(r"(?<=\.)\s+(?=[A-Z`])", " ".join(block.split())))
    return out


# ── the files ────────────────────────────────────────────────────────────────

def test_the_named_files_are_the_ones_the_loaders_read():
    files = factors.committed_factor_files()
    assert files == {
        "US factors": "data/cache/ff_factors_us.csv",
        "Developed ex-US": "data/cache/ff_factors_developed_exus.csv",
        "Momentum (UMD)": "data/cache/ff_umd_us.csv",
    }
    assert [ROOT / p for p in files.values()] == [
        factors._FACTOR_CONFIG["us"]["cache"],
        factors._FACTOR_CONFIG["developed_exus"]["cache"],
        factors._UMD_CACHE,
    ]
    assert len(factors._FACTOR_CONFIG) == 2, "a third region's file would need naming too"


def test_each_named_file_is_committed_and_no_other_french_file_is():
    tracked = subprocess.run(["git", "ls-files", "data"], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.split()
    french = sorted(p for p in tracked if Path(p).name.startswith("ff_"))
    assert french == sorted(factors.committed_factor_files().values())
    for path in french:
        assert (ROOT / path).is_file(), path


def test_the_refresh_tool_writes_exactly_these_files():
    """The writer the docs name. That nothing else writes them is held by
    tests/test_market_data_immutability.py, on the runtime itself."""
    from tools import refresh_market_data as tool
    targets = {key: Path(target[0]) for key, target in tool._TARGETS.items()
               if key.startswith("ff_")}
    assert sorted(p.relative_to(ROOT).as_posix() for p in targets.values()) == sorted(
        factors.committed_factor_files().values())


def test_a_path_outside_the_repository_is_named_whole(tmp_path, monkeypatch):
    """A test that points a loader at its own copy must still get a caption."""
    monkeypatch.setattr(factors, "_UMD_CACHE", tmp_path / "umd.csv")
    assert factors.committed_factor_files()["Momentum (UMD)"] == (tmp_path / "umd.csv").as_posix()
    assert (tmp_path / "umd.csv").as_posix() in factors.factor_data_caption()


# ── the two docs ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("doc", DOCS)
def test_the_doc_names_each_committed_file_under_data_cache(doc):
    text = _read(doc)
    assert "`data/cache/`" in text, f"{doc} does not name the directory"
    for path in factors.committed_factor_files().values():
        assert f"`{Path(path).name}`" in text, f"{doc} does not name {Path(path).name}"


@pytest.mark.parametrize("doc", DOCS)
def test_the_doc_says_no_ttl_and_names_the_only_writer_where_it_names_the_files(doc):
    naming = [s for s in _statements(doc) if "`ff_factors_us.csv`" in s]
    assert len(naming) == 1, f"premise: {doc} names the files in one statement: {naming}"
    statement = naming[0]
    assert "no TTL" in statement, statement
    assert f"`{WRITER}`" in statement, statement
    assert re.search(r"only `tools/refresh_market_data\.py` writes them|"
                     r"`tools/refresh_market_data\.py` is their only writer", statement), statement
    assert (ROOT / WRITER).is_file()


@pytest.mark.parametrize("where", DOCS + (PAGE,))
def test_what_it_said_before_is_gone(where):
    text = " ".join(_read(where).split())
    for phrase in GONE:
        assert phrase not in text, f"{where} still says {phrase!r}"


def test_the_architecture_diagram_gives_the_french_files_no_ttl():
    line = [s for s in _read("docs/architecture.md").splitlines()
            if s.startswith("Ken French Data Library")]
    assert len(line) == 1, line
    assert line[0].endswith("data/cache/ff_factors_*.csv, ff_umd_us.csv (committed inputs, no TTL)")
    # The same form as the line below it, which #482 corrected for CAPE.
    assert "data/shiller_cape.csv (committed input, no TTL)" in _read("docs/architecture.md")


def test_the_architecture_doc_no_longer_says_every_external_read_is_cached_locally():
    """It did, in the sentence before the French one. Two of the four sources are
    committed inputs that nothing fetches on read."""
    text = " ".join(_read("docs/architecture.md").split())
    assert "All reads from external sources are cached locally" not in text
    assert "Prices and FRED series are cached locally as they are read." in text
    assert "`src/factors.py` reads them and never fetches" in text


# ── the caption ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("demo", [False, True])
def test_the_caption_names_each_path_no_ttl_and_one_only_writer(demo):
    caption = factors.factor_data_caption(demo=demo)
    for what, path in factors.committed_factor_files().items():
        assert f"{what} at {path}" in caption, caption
        assert path.startswith("data/cache/")
    assert "Committed files with no TTL: " in caption
    assert caption.endswith(" is their only writer, and nothing fetches them on read.")
    assert caption.startswith("Data: Ken French Data Library, Dartmouth")


def test_the_caption_names_the_writer_by_its_path_outside_the_demo_only(monkeypatch):
    """The public demo's pages print no maintainer tool's path: a visitor can do nothing
    with one (the 2026-09-25 audit's item 4, tests/render/test_market_data_wording.py).
    So there the caption says one tool writes the files, and names it everywhere else."""
    import src.config as config
    named = f". {WRITER} is their only writer, and nothing fetches them on read."
    unnamed = ". A maintainer's refresh tool is their only writer, and nothing fetches them on read."
    assert factors.factor_data_caption(demo=False).endswith(named)
    assert factors.factor_data_caption(demo=True).endswith(unnamed)
    assert "tools/" not in factors.factor_data_caption(demo=True)
    # With no argument it follows the running mode, read when it is called.
    monkeypatch.setattr(config, "IS_DEMO", True)
    assert factors.factor_data_caption() == factors.factor_data_caption(demo=True)
    monkeypatch.setattr(config, "IS_DEMO", False)
    assert factors.factor_data_caption() == factors.factor_data_caption(demo=False)


def test_the_page_prints_that_caption_and_types_no_path():
    source = _read(PAGE)
    assert source.count("st.caption(factor_data_caption())") == 1
    assert "ff_factors" not in source and "ff_umd" not in source, (
        "the page types a French file's path; it is read from factors.committed_factor_files")


@pytest.fixture
def book(tmp_path, monkeypatch):
    import streamlit as st
    pin_today(monkeypatch)
    path = point_at_frozen_book(monkeypatch, tmp_path)
    st.cache_data.clear()
    yield path
    st.cache_data.clear()
    unpin_leftovers()


@pytest.mark.parametrize("demo", [True, False])
def test_the_rendered_page_carries_it_in_each_mode(book, monkeypatch, demo):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    import src.config as config
    monkeypatch.setattr(config, "IS_DEMO", demo)
    st.cache_data.clear()
    at = AppTest.from_file(str(ROOT / PAGE), default_timeout=300).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    captions = [str(c.value) for c in at.caption]
    french = [c for c in captions if "Ken French Data Library, Dartmouth" in c]
    assert french == [factors.factor_data_caption(demo=demo)], french
    assert (WRITER in french[0]) == (not demo)
    assert not any("data/ff_" in c for c in captions)


# ── the operational check ────────────────────────────────────────────────────

def test_the_operational_check_cites_the_two_constants_by_name_not_by_line():
    """It cited _SAA_US and _FI_WEIGHTS by line number (#527), then by name. #525 removed
    both: the weights read the SAA targets. The check now names what they come from, by
    name, and no constant that is gone."""
    text = _read("docs/operational_checks.md")
    assert not re.search(r"\(line\s*~?\s*\d+\)|\bline ~", text), (
        "a line number in a doc is false after the next edit above it")
    for name in ("target_weights", "_US_FUNDS", "_FI_FUNDS"):
        assert f"`factors.{name}`" in text, name
        assert hasattr(factors, name), f"factors.{name} is gone"
    for gone in ("_SAA_US", "_FI_WEIGHTS"):
        assert gone not in text, gone
        assert not hasattr(factors, gone), f"factors.{gone} is back"


def test_the_tests_the_operational_check_names_exist():
    text = _read("docs/operational_checks.md")
    consistency = _read("tests/test_prose_consistency.py")
    for name in ("test_prose_us_weights_come_from_the_db_targets",
                 "test_prose_fi_weights_come_from_the_db_targets"):
        assert f"`{name}`" in text
        assert f"def {name}(" in consistency, f"{name} is named in the doc and is not a test"
