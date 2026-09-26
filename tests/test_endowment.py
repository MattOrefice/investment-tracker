"""Tests for src/endowment_benchmarks.py."""
import sys
import pathlib
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.endowment_benchmarks import (
    CATEGORIES,
    PORTFOLIO_LABEL,
    YALE_FY2024,
    PRINCO_FY2024,
    entities,
    get_endowment_data,
    sanity_check,
    this_portfolio_targets,
)

# The portfolio row reads asset_classes, so every test that reaches it is pinned
# to the demo book.
pytestmark = pytest.mark.usefixtures("use_demo_db")


def test_all_entities_sum_to_100():
    """Every entity's category weights must sum to exactly 100%."""
    sums = sanity_check()
    for name, total in sums.items():
        assert abs(total - 100.0) < 0.01, (
            f"{name} sums to {total:.2f}%, not 100%"
        )


def test_categories_exhaustive_coverage():
    """Every entity must have an entry for every category in CATEGORIES."""
    for entity_name, alloc in entities().items():
        for cat in CATEGORIES:
            assert cat in alloc, (
                f"Entity '{entity_name}' is missing category '{cat}'"
            )


def test_this_portfolio_no_alternatives():
    """This Portfolio must have 0% Private Equity and 0% Absolute Return."""
    assert this_portfolio_targets()["Private Equity / VC"] == 0.0
    assert this_portfolio_targets()["Absolute Return / HF"] == 0.0


def test_this_portfolio_high_public_equity():
    """This Portfolio must be predominantly public equity (>60%)."""
    assert this_portfolio_targets()["Public Equity"] > 60.0


def test_this_portfolio_is_the_saa_targets_not_a_typed_allocation():
    """The row was a typed-in 78 / 10 / 10 / 2-cash beside an SAA of 80 / 10 / 10."""
    from src.db import get_connection
    with get_connection() as conn:
        parents = {r["name"]: float(r["target_weight"]) * 100 for r in conn.execute(
            "SELECT name, target_weight FROM asset_classes WHERE parent_id IS NULL")}
    row = this_portfolio_targets()
    assert row["Public Equity"] == pytest.approx(parents["Equity"])
    assert row["Fixed Income"] == pytest.approx(parents["Income"])
    assert row["Real Assets"] == pytest.approx(parents["Real Assets"])
    assert row["Cash"] == pytest.approx(parents["Cash"]) == 0.0
    assert PORTFOLIO_LABEL == "This Portfolio (SAA targets)"
    assert list(entities())[-1] == PORTFOLIO_LABEL


def test_an_unmapped_parent_with_a_target_raises(monkeypatch):
    import src.endowment_benchmarks as eb
    monkeypatch.setattr(eb, "PARENT_TO_CATEGORY",
                        {k: v for k, v in eb.PARENT_TO_CATEGORY.items() if k != "Income"})
    with pytest.raises(ValueError, match="'Income'"):
        eb.this_portfolio_targets()


def test_yale_low_public_equity():
    """Yale's public equity should be well below 20%."""
    assert YALE_FY2024["Public Equity"] < 20.0


def test_princo_has_private_equity():
    """PRINCO must have meaningful private equity (>20%)."""
    assert PRINCO_FY2024["Private Equity / VC"] > 20.0


def test_get_endowment_data_returns_records():
    """get_endowment_data must return one record per (entity × category)."""
    records = get_endowment_data()
    n_entities   = len(entities())
    n_categories = len(CATEGORIES)
    assert len(records) == n_entities * n_categories


def test_get_endowment_data_has_required_keys():
    """Each record must have 'entity', 'category', and 'weight' keys."""
    for rec in get_endowment_data():
        assert "entity"   in rec
        assert "category" in rec
        assert "weight"   in rec


def test_all_weights_non_negative():
    """No weight should be negative."""
    for rec in get_endowment_data():
        assert rec["weight"] >= 0.0, (
            f"Negative weight for {rec['entity']} / {rec['category']}: {rec['weight']}"
        )
