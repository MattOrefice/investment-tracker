"""Structural checks — assets and files that README references must exist on disk."""
import pathlib

_REPO = pathlib.Path(__file__).resolve().parent.parent


def test_architecture_doc_exists():
    """docs/architecture.md must exist and be non-trivial.

    README.md links to this file; a missing file produces a broken link on GitHub.
    """
    arch = _REPO / "docs" / "architecture.md"
    assert arch.exists(), f"Architecture doc missing at {arch}."
    assert arch.stat().st_size > 2_000, (
        f"Architecture doc is suspiciously small ({arch.stat().st_size} bytes)."
    )


def test_hero_image_exists():
    """docs/images/hero_macro.png must exist.

    README.md embeds this image; a missing file renders a broken image on GitHub.
    Re-run scripts/capture_hero_screenshot.py to regenerate if it is deleted.
    """
    img = _REPO / "docs" / "images" / "hero_macro.png"
    assert img.exists(), (
        f"Hero image missing at {img}. "
        "Run: python scripts/capture_hero_screenshot.py"
    )
    assert img.stat().st_size > 50_000, (
        f"Hero image is suspiciously small ({img.stat().st_size} bytes). "
        "Page may not have rendered — re-run the capture script."
    )


def test_no_yahoo_finance_in_page_or_module_source():
    """No page or module source file may contain 'yahoo' (case-insensitive).

    src/prices.py is excluded — it holds the actual upstream URL and HTTP error
    messages that must correctly identify the vendor. All other pages/*.py and
    src/*.py are checked. Phase 44.1 guard.
    """
    pages_dir  = _REPO / "pages"
    src_dir    = _REPO / "src"
    skip_files = {"prices.py"}

    hits = []
    for py_file in sorted(pages_dir.glob("*.py")):
        if "yahoo" in py_file.read_text(encoding="utf-8").lower():
            hits.append(py_file)
    for py_file in sorted(src_dir.glob("*.py")):
        if py_file.name in skip_files:
            continue
        if "yahoo" in py_file.read_text(encoding="utf-8").lower():
            hits.append(py_file)

    assert not hits, (
        f"'yahoo' found in {len(hits)} source file(s):\n"
        + "\n".join(f"  {f.relative_to(_REPO)}" for f in hits)
        + "\n\nRemove vendor references from user-facing pages and modules. "
        "src/prices.py is excluded (upstream URL and error messages)."
    )
