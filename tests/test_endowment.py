"""Tests for src/endowment_benchmarks.py."""
import sys
import pathlib
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.endowment_benchmarks import (
    CATEGORIES,
    ENTITIES,
    YALE_FY2024,
    PRINCO_FY2024,
    THIS_PORTFOLIO,
    get_endowment_data,
    sanity_check,
)


def test_all_entities_sum_to_100():
    """Every entity's category weights must sum to exactly 100%."""
    sums = sanity_check()
    for name, total in sums.items():
        assert abs(total - 100.0) < 0.01, (
            f"{name} sums to {total:.2f}%, not 100%"
        )


def test_categories_exhaustive_coverage():
    """Every entity must have an entry for every category in CATEGORIES."""
    for entity_name, alloc in ENTITIES.items():
        for cat in CATEGORIES:
            assert cat in alloc, (
                f"Entity '{entity_name}' is missing category '{cat}'"
            )


def test_this_portfolio_no_alternatives():
    """This Portfolio must have 0% Private Equity and 0% Absolute Return."""
    assert THIS_PORTFOLIO["Private Equity / VC"] == 0.0
    assert THIS_PORTFOLIO["Absolute Return / HF"] == 0.0


def test_this_portfolio_high_public_equity():
    """This Portfolio must be predominantly public equity (>60%)."""
    assert THIS_PORTFOLIO["Public Equity"] > 60.0


def test_yale_low_public_equity():
    """Yale's public equity should be well below 20%."""
    assert YALE_FY2024["Public Equity"] < 20.0


def test_princo_has_private_equity():
    """PRINCO must have meaningful private equity (>20%)."""
    assert PRINCO_FY2024["Private Equity / VC"] > 20.0


def test_get_endowment_data_returns_records():
    """get_endowment_data must return one record per (entity × category)."""
    records = get_endowment_data()
    n_entities   = len(ENTITIES)
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
