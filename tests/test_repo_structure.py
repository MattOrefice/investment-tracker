"""Structural checks — assets and files that README references must exist on disk."""
import pathlib

_REPO = pathlib.Path(__file__).resolve().parent.parent


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
