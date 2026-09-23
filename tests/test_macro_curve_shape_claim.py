"""#239 — the yield-curve shape sentence claims only what its test reads.

The shape is decided from three tenors (3M, 2Y, 10Y) while the chart plots eight, so
no branch may describe the whole curve. Read through `ast`, not a text search: the
assigned strings are what render.
"""
import ast
import pathlib

PAGE = pathlib.Path(__file__).resolve().parent.parent / "pages" / "3_Macro.py"


def _shape_strings():
    tree = ast.parse(PAGE.read_text(encoding="utf-8"))
    return [n.value.value for n in ast.walk(tree)
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_yc_shape" for t in n.targets)
            and isinstance(n.value, ast.Constant)]


def test_the_shape_branches_are_found():
    assert len(_shape_strings()) == 5, _shape_strings()


def test_no_shape_claim_covers_maturities_the_test_does_not_read():
    for s in _shape_strings():
        assert "all maturities" not in s and "entire curve" not in s, s


def test_the_upward_branch_names_its_three_points():
    assert "upward-sloping across those three maturities" in _shape_strings()
