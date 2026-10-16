"""The Asset Evaluation page as it renders on a copy of the demo book, offline, after
#425 and #601 (decided by the owner on October 15, 2026). The helpers' zero and missing
cases are in tests/test_asset_evaluation_425.py; the 2022 paragraph's figures are pinned in
tests/render/test_typed_figures_render.py.
"""
from __future__ import annotations

import re

import pytest

from tests.render.test_typed_in_figures import _render


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    at, _ = _render("5_Asset_Evaluation.py", tmp_path_factory)
    return at, [str(e.value) for e in at.markdown], [str(e.value) for e in at.caption]


def test_sections_in_order_is_the_order_the_page_renders(page):
    at, markdown, _ = page
    rendered = [m.group(1) for s in at.subheader
                if (m := re.match(r"(5[a-j])\. ", str(s.value)))]
    assert rendered == [f"5{c}" for c in "abcdefghij"], rendered
    listed = next(t for t in markdown if t.startswith("This page applies a five-step"))
    order = listed[listed.index("**Sections in order:**"):].split("\n\n")[0]
    assert re.findall(r"\((5[a-j])\)", order) == rendered, order


def test_the_framing_says_what_the_sections_do(page):
    _, markdown, captions = page
    text = " ".join(markdown + captions)
    assert "**Correlation behavior** is evaluated over two windows." in text
    assert "**Decision framework:** the page ends with its conclusion (5j)." in text
    assert ("The decision framework in Section 5j weighs the first three; it does not "
            "model shrinkage.") in text
    assert "This result is driven by BTC's high sample return." in text
    for gone in ("two frequencies", "surfaces tradeoffs", "not a single recommendation",
                 "addresses all of them", "high expected return"):
        assert gone not in text, gone


def test_the_page_ends_with_its_conclusion(page):
    """The conclusion is 5j's last markdown, after the arguments and the allocator-side
    considerations; only the methodology expander follows it."""
    _, markdown, _ = page
    conclusion = next(i for i, t in enumerate(markdown) if t.startswith("**Conclusion:**"))
    allocator = next(i for i, t in enumerate(markdown)
                     if t.startswith("**Allocator-side considerations:**"))
    assert allocator < conclusion
    assert all(t.startswith("**") and ":**" in t[:40] for t in markdown[conclusion + 1:]), (
        markdown[conclusion + 1:conclusion + 3])


def test_5b_states_the_bond_correlation_without_a_reading(page):
    _, markdown, _ = page
    text = " ".join(markdown)
    assert re.search(r"The Core Fixed Income correlation is -?\d\.\d\d\. As a full-sample "
                     r"figure, it says nothing about behavior during equity stress\.", text)
    assert "which is consistent with Bitcoin offering some diversification" not in text


def test_5c_and_the_conclusion_print_the_same_two_averages(page):
    _, markdown, _ = page
    text = " ".join(markdown)
    stated = re.search(r"Before 2020, BTC's rolling 60-day correlation with SPY averaged "
                       r"(-?\d\.\d\d)\. From 2020 on, it has averaged (-?\d\.\d\d)\.", text)
    cited = re.search(r"- The rolling 60-day correlation with SPY, on average from 2020 on, "
                      r"falls to its pre-2020 average: it averaged (-?\d\.\d\d) before 2020 "
                      r"and (-?\d\.\d\d) from 2020 through", text)
    assert stated and cited
    assert stated.groups() == cited.groups()
    paragraph = next(t for t in markdown if t.startswith("Before 2020, BTC's rolling"))
    assert paragraph == stated.group(0), "the averages and nothing after them"


def test_5d_names_the_only_positive_difference_the_table_shows(page):
    at, markdown, _ = page
    table = next(df.value for df in at.dataframe if "Difference" in df.value.columns)
    positive = table.loc[table["Difference"].round(2) > 0, "Sleeve"].tolist()
    sentences = [t for t in markdown if "daily-to-weekly difference" in t]
    if len(positive) == 1:
        assert sentences == [f"{positive[0]} shows the only positive daily-to-weekly "
                             f"difference ({table.loc[table['Sleeve'] == positive[0], 'Difference'].iloc[0]:+.2f})."]
    else:
        assert sentences == []
    assert len(positive) == 1, "premise: the demo book has one positive difference"


def test_5j_states_one_tax_treatment_and_no_2022_figure_of_bitcoins_own(page):
    _, markdown, _ = page
    text = " ".join(markdown)
    # Since #625 the Notice carries its bulletin's date.
    assert text.count("the IRS treats bitcoin as property (Notice 2014-21, ") == 1
    assert "commodity classification" not in text
    assert "Its 2022 drawdown of" not in text
    assert "Bitcoin's own maximum drawdown is" in text, "premise: the bullet renders"
