"""packages.txt was reviewed against WeasyPrint's REQUIRED shared libraries.

## THIS IS NOT COVERAGE FOR #255. DO NOT CITE IT AS SUCH.

It asserts a relationship between two things in this repo — WeasyPrint's declared
library requirements and the fact that packages.txt was reviewed against them. It
makes **no claim whatsoever about any environment**: not Streamlit Cloud, not CI, not
this machine. It cannot tell you the PDF builds anywhere.

Whether the deployed environment actually provides those libraries is answerable only
by rendering a PDF from the deployed app. A CI job installing packages.txt on
ubuntu-latest was considered and deliberately NOT built: it would prove sufficiency on
the GitHub runner, whose base image is not Cloud's, and its failure mode is
green-here-broken-there — the class of claim this project has repeatedly had to
retract. #255 tracks the real question.

## What it does assert, and why that is worth something

WeasyPrint dlopens its libraries at import (weasyprint/text/ffi.py). If a future
version adds a REQUIRED one, packages.txt silently stops covering the set it was
reviewed against, and nothing anywhere would say so — the first symptom would be
`RuntimeError("No PDF renderer available")` in front of a user, because
`_render_pdf`'s xhtml2pdf fallback is pinned `sys_platform == 'win32'` and is absent
on Linux (see requirements.txt).

So this test pins the REVIEWED SET. When WeasyPrint's requirements change it goes red,
and a human re-reads packages.txt against the new set. That is the whole contract.

## Derived, not hardcoded — and why

The required set is parsed out of WeasyPrint's own source with `ast` rather than
written down here, so a version that adds a library is detected rather than assumed
away. Hardcoding six names would make this test agree with itself forever.

The derivation reads a third-party module's private layout, which is genuinely
fragile — so it is guarded: if the parse finds nothing recognisable, the test FAILS
loudly rather than passing on an empty set. A silent empty derivation would be the
"assert collections are complete" defect in its purest form. Red on a WeasyPrint
restructure is the correct failure direction: it costs one re-derivation and cannot
produce a false green.
"""
import ast
import importlib.metadata
import importlib.util
import pathlib

import pytest

# The version the set was read from. RECORDED, NOT ASSERTED — and that distinction
# was learned the hard way: an equality check here failed CI immediately, because
# requirements.txt says `weasyprint>=60.0`, so CI resolves the latest (69.0) while
# this machine had 68.1. Nothing was wrong; the assertion was measuring the wrong
# thing — an environment's resolved version rather than the library's requirements.
#
# The SET is the contract, and it is environment-independent. A version difference
# matters only if it changes the set, which the set test catches directly — so the
# version belongs in that test's FAILURE MESSAGE, where a reader needs it, and not
# in an assertion of its own.
#
# The accident was informative: the required set is IDENTICAL at 68.1 (this machine)
# and 69.0 (CI), which is evidence the derivation survives a minor version bump —
# a second data point no local run could have produced.
REVIEWED_AGAINST_WEASYPRINT = "68.1"
REVIEWED_REQUIRED = {
    "libgobject-2.0-0",
    "libpango-1.0-0",
    "libharfbuzz-0",
    "libfontconfig-1",
    "libpangoft2-1.0-0",
}
# Declared with allow_fail=True by WeasyPrint — absence degrades, it does not break.
REVIEWED_OPTIONAL = {"libharfbuzz-subset-0"}

# Why three apt packages were judged to cover five libraries. RECORDED, NOT ASSERTED:
# it is a claim about Debian's dependency graph, which cannot be checked from here.
#
#   libpango-1.0-0    -> libpango-1.0.so.0      (direct)
#   libpangoft2-1.0-0 -> libpangoft2-1.0.so.0   (direct)
#   libharfbuzz0b     -> libharfbuzz.so.0       (direct)
#   libgobject-2.0.so.0   expected transitively via libglib2.0-0, a dependency of pango
#   libfontconfig.so.1    expected transitively via libfontconfig1, a dependency of pango
#
# The two transitive ones are named by NEITHER packages.txt NOR the CI apt step that
# was removed — see #254, whose three-versus-five framing predates this derivation.
PACKAGES_TXT = pathlib.Path(__file__).resolve().parent.parent / "packages.txt"


def _weasyprint_dlopen_sets():
    """(required, optional) first-choice library names, parsed from WeasyPrint.

    Returns the FIRST name of each `_dlopen(ffi, 'name', ...)` call — WeasyPrint lists
    per-platform aliases for the same library and the first is its canonical one.
    """
    spec = importlib.util.find_spec("weasyprint")
    if spec is None or not spec.submodule_search_locations:
        pytest.skip("weasyprint not installed")
    root = pathlib.Path(list(spec.submodule_search_locations)[0])
    ffi = root / "text" / "ffi.py"
    if not ffi.exists():
        pytest.fail(
            f"weasyprint/text/ffi.py not found under {root} — WeasyPrint moved its "
            "FFI bindings. Re-derive the required library set by hand and update "
            "REVIEWED_REQUIRED, then re-read packages.txt against it.")

    required, optional = set(), set()
    for node in ast.walk(ast.parse(ffi.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "_dlopen"):
            continue
        names = [a.value for a in node.args
                 if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        if not names:
            continue
        allow_fail = any(k.arg == "allow_fail"
                         and isinstance(k.value, ast.Constant) and k.value.value
                         for k in node.keywords)
        (optional if allow_fail else required).add(names[0])
    return required, optional


def test_the_derivation_is_not_vacuous():
    """FIRST, because every assertion below is worthless if the parse found nothing.
    An empty derivation would make the comparison trivially pass."""
    required, optional = _weasyprint_dlopen_sets()
    assert len(required) >= 3, (
        f"parsed only {len(required)} required libraries from weasyprint — the "
        "derivation is not finding the _dlopen calls, so it is not measuring "
        "anything. Re-derive by hand rather than trusting this file.")
    assert optional, (
        "parsed no OPTIONAL library — WeasyPrint declares at least one with "
        "allow_fail=True, so an empty optional set means allow_fail is no longer "
        "being detected and required/optional may be conflated")


def test_weasyprints_required_libraries_are_the_reviewed_set():
    """THE CONTRACT. If WeasyPrint adds a required library, packages.txt silently
    stops covering the set it was reviewed against — and the first symptom would
    otherwise be a RuntimeError in front of a user.

    Environment-independent by construction: it compares the library's own
    declarations against a reviewed set, so it says the same thing on any machine
    whatever version resolves there. An earlier version of this file ALSO asserted
    the installed version equalled the reviewed one, and that failed CI on sight —
    see the note at REVIEWED_AGAINST_WEASYPRINT.
    """
    required, _ = _weasyprint_dlopen_sets()
    installed = importlib.metadata.version("weasyprint")
    assert required == REVIEWED_REQUIRED, (
        f"WeasyPrint {installed}'s required libraries differ from the set reviewed "
        f"at {REVIEWED_AGAINST_WEASYPRINT}.\n"
        f"  added:   {sorted(required - REVIEWED_REQUIRED)}\n"
        f"  removed: {sorted(REVIEWED_REQUIRED - required)}\n"
        f"Re-read packages.txt against the new set, update REVIEWED_REQUIRED and "
        f"REVIEWED_AGAINST_WEASYPRINT, and note in #254 whether the deployed list "
        f"still covers it.")


def test_the_optional_library_stays_optional():
    """harfbuzz-subset is allow_fail=True: absent, WeasyPrint degrades rather than
    failing. If it ever becomes required it must move into the reviewed set, and the
    equality above would not catch a required->optional move on its own."""
    _, optional = _weasyprint_dlopen_sets()
    assert optional == REVIEWED_OPTIONAL, (
        f"optional set changed: {sorted(optional)} vs {sorted(REVIEWED_OPTIONAL)} — "
        "a library moving between required and optional changes what packages.txt "
        "must guarantee")


def test_packages_txt_still_names_the_three_direct_libraries():
    """The direct mappings only. The two transitive ones are deliberately NOT asserted
    — that is a claim about Debian's dependency graph, recorded above as reasoning and
    uncheckable from here."""
    declared = {ln.strip() for ln in PACKAGES_TXT.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.strip().startswith("#")}
    for pkg in ("libpango-1.0-0", "libpangoft2-1.0-0", "libharfbuzz0b"):
        assert pkg in declared, (
            f"{pkg} left packages.txt — WeasyPrint dlopens the library it provides")


def test_this_file_disclaims_being_pdf_build_coverage():
    """Guards the disclaimer itself. The risk this file carries is not being wrong —
    it is being CITED as proof the PDF builds, which it cannot show. If the docstring
    loses the disclaimer the next reader has nothing to stop them.

    READS __doc__, NOT THE FILE. The first version searched the whole source for the
    phrase — and the assertion line itself contains that phrase, so the check
    satisfied itself and could never fail. A mutant deleting the disclaimer from the
    docstring passed. Module __doc__ excludes this function's body, so the checker is
    no longer part of what it checks.
    """
    doc = __doc__ or ""
    assert "NOT COVERAGE FOR #255" in doc, (
        "the scope disclaimer left the module docstring — without it this file reads "
        "like evidence that the PDF builds, which it cannot show")
    assert "no claim whatsoever about any environment" in doc
