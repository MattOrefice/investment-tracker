"""The Roth deploy's developed-international exclusion, disclosed in rendered prose.

#228, unblocked when #221 decided (2026-08-17, da2e590) that `emerging_markets`
STAYS in `_ROTH_PRIORITY` — the code was right and the rationale was wrong.

THE GAP THIS CLOSES. Page 14's Roth deploy card buys IEMG while `intl_developed`
and `intl_all_exus` are absent from the map, and nothing rendered said why. A reader
who happens to know the foreign-tax-credit argument saw the table contradict it; a
reader who did not was never told the argument existed.

THE TRAP IT AVOIDS, which is why this file exists rather than a one-line clause.
#228's own body drafted the disclosure as "international sits in taxable because
only a taxable holder can credit the foreign tax withheld, so the Roth deploy skips
it." That is verbatim the blanket-FTC framing da2e590 DELETED from three places for
being "too coarse in both directions" — it would have restored, on the one surface a
reader actually sees, the claim just removed from three they do not. The clause must
therefore RULE THE FTC OUT, not merely omit it.

The two halves a later edit would drop first are the two pinned hardest: the FTC
rebuttal, and the stated expiry (#230).
"""
import pathlib
import re

import pytest

import src.location_actions as la

PAGE = (pathlib.Path(la.__file__).resolve().parent.parent
        / "pages" / "14_Asset_Location.py")


def _prose() -> str:
    """The clause as a READER sees it, decoded — not as the source spells it.

    Read through `ast` rather than by grepping the file, for two independent
    reasons and both have bitten this repo:

    1. The comment block above the clause QUOTES the retired blanket-FTC wording
       and names the figures the clause withholds. A raw substring search would
       find this test's subject inside its own documentation and pass on prose
       that says the opposite of what it asserts. The AST has no comments in it.
    2. The clause is ~20 adjacent string literals. In the source, every phrase is
       cut by `" \\n            "` seams, so a reader-facing sentence matches
       nothing — and `target-\\\\$0` on disk is `target-\\$0` to a reader. Testing
       the source would test the line wrapping.

    The first version of this helper did grep the source, and all seven
    prose assertions failed at once — loudly, which is the good failure.
    """
    import ast
    tree = ast.parse(PAGE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and "**Why international is split across wrappers.**" in node.value):
            i = node.value.index("**Why international is split across wrappers.**")
            return re.sub(r"\s+", " ", node.value[i:])
    raise AssertionError(
        "the clause is not present as a string constant in "
        f"{PAGE.name} — it was removed, or split by an f-string that broke the "
        "literal into pieces this helper cannot see")


# ── the half that must not become an omission ─────────────────────────────────

def test_the_clause_rules_the_ftc_out_rather_than_omitting_it():
    """THE POINT. An omission leaves the target reader — one who knows the FTC
    argument and is looking at IEMG in the deploy table — holding a contradiction
    with nothing to resolve it. The clause has to say the credit is NOT the reason."""
    p = _prose()
    assert "the reason is *not* the foreign tax credit" in p, (
        "the clause no longer rebuts the FTC — if it now merely omits it, the "
        "reader this was written for is back where #228 found them")


def test_the_rebuttal_gives_the_fact_that_makes_it_decisive():
    """Saying "not the FTC" without WHY is an assertion. The decisive fact is that
    emerging markets forfeits MORE credit than the developed sleeves and is kept
    anyway — so the credit cannot be what separates them."""
    p = _prose()
    assert "forfeits that on emerging markets too, and by more" in p


def test_the_clause_states_the_direction_without_the_authored_figures():
    """~20-25% vs ~15% are authored country-mix estimates, not measurements. The
    DIRECTION survives them being loose; the FIGURES would read as measured on a
    page promising live-or-refuse for every dollar shown."""
    p = _prose()
    assert "withholding runs higher than developed's" in p
    for n in ("20-25%", "20–25%", "~15%", "15%"):
        assert n not in p, f"{n} reached the rendered clause as though measured"


def test_the_reasoning_for_withholding_direction_is_findable_in_source():
    """The next person tempted to add the numbers must meet the argument, not a
    bare absence. Read from the COMMENTS this time — that is where it lives."""
    src = PAGE.read_text(encoding="utf-8")
    i = src.index("**Why international is split across wrappers.**")
    above = src[max(0, i - 2600):i]
    assert "DIRECTION WITHOUT NUMBERS" in above
    assert "AUTHORED estimates" in above


# ── the half that expires ─────────────────────────────────────────────────────

def test_the_clause_states_its_own_expiry():
    """#230. One of the three reasons — that the developed sleeves render off-SAA
    as target-$0 rows — stops being true when the 9->12 split lands. Stated, the
    clause becomes a scheduled revision; implied, it goes quietly false."""
    p = _prose()
    assert "expires when the international split lands" in p
    assert "rests on scarcity alone" in p


def test_the_clause_gives_all_three_reasons_not_just_the_durable_ones():
    """A clause that dropped the expiring reason would need no expiry note and
    would be wrong today — the exclusion does not rest on scarcity yet."""
    p = _prose()
    assert "off the SAA as target-\\$0 rows" in p
    assert "counts carrier positions in the taxable book only" in p
    assert "scarcest shelter" in p


def test_the_clause_says_why_emerging_markets_is_not_an_exception():
    """The asymmetry a reader is owed: EM is kept, and not because it escapes the
    cost. #221 called this a deliberate trade, not an exemption."""
    p = _prose()
    assert "targeted SAA sleeve and currently underweight" in p
    assert "accepted rather than avoided" in p


# ── no live figures smuggled in ───────────────────────────────────────────────

def test_the_clause_hardcodes_no_dollar_figure():
    """The expander four paragraphs above promises every dollar is computed or the
    page refuses to render. A hardcoded rollover balance here would contradict it,
    and a reader could not tell it from the templated ones beside it."""
    p = _prose()
    # `\$0` is allowed and deliberate — "target-$0 rows" is a CATEGORY of row, not
    # a computed balance. What must never appear is an amount: a leading non-zero
    # digit is what separates the two.
    assert not re.search(r"\\\$[1-9][\d,]*", p), "a dollar amount was hardcoded"
    assert "401(k) rollover still inbound" in p, "the rollover is named without a figure"


# ── the fourth copy ───────────────────────────────────────────────────────────

def test_no_blanket_ftc_claim_survives_outside_the_config():
    """THE SWEEP, as an assertion rather than a one-off grep.

    da2e590 corrected the blanket-FTC claim in three places and its own message said
    "three places". There was a fourth, in the module that builds the deploy table,
    written 2026-07-11 and predating both. `location_config` is excluded because that
    is where the single CORRECT statement lives — a correction that must exist once,
    not nowhere; it also quotes the retired wording as history, which is fine there
    and is why the exclusion is by file rather than by phrase.

    WHITESPACE IS COLLAPSED FIRST, and that is not tidiness. The first version of
    this test searched the raw text for a contiguous phrase, and a mutant that
    reintroduced the claim WRAPPED ACROSS TWO LINES — which is how it would actually
    come back, in a docstring or comment — SURVIVED. The sweep was measuring line
    wrapping, not content. Same trap as the #250 mutation harness and the
    `real_assets_commodities` evidence check, which both hit it before this did.
    """
    root = pathlib.Path(la.__file__).resolve().parent.parent
    offenders = []
    for f in sorted(root.rglob("*.py")):
        if "location_config" in f.name or ".venv" in f.parts or "test_" in f.name:
            continue
        raw = f.read_text(encoding="utf-8", errors="replace")
        # Comment prefixes stripped as well as whitespace: the claim reappearing in a
        # `#` block is the same defect as in a docstring, and `#` breaks the phrase.
        t = re.sub(r"\s+", " ", re.sub(r"^\s*#\s?", "", raw, flags=re.M))
        for phrase in ("a Roth forfeits the foreign tax credit",
                       "Roth forfeits the FTC"):
            if phrase in t:
                offenders.append(f"{f.relative_to(root)}: {phrase}")
    assert not offenders, (
        "a blanket-FTC claim reappeared outside location_config.py — emerging "
        f"markets is in the map and forfeits MORE credit, so it cannot be the "
        f"rule: {offenders}")


def test_the_corrected_docstring_points_rather_than_restates():
    """Restating is the mechanism that produced four copies of the wrong claim; a
    fifth restatement of the RIGHT one is the same behaviour with better luck. The
    fix must redirect to the single source, not reproduce it."""
    doc = la.household_deploy_gaps.__doc__ or ""
    assert "src/location_config.py" in doc, "the correction does not point anywhere"
    assert "deliberately not restated here" in doc
    # ...and it must not have quietly become a fifth copy of the three reasons.
    for reason in ("phase-46", "76,147", "9.18%"):
        assert reason not in doc, (
            f"{reason!r} was restated into this docstring — that is the copying "
            "behaviour the comment itself warns against")


def test_the_docstring_says_why_the_ftc_cannot_be_the_rule():
    """A pointer with no reason invites someone to 'restore' the deleted sentence."""
    doc = la.household_deploy_gaps.__doc__ or ""
    assert "forfeits MORE credit" in doc


def test_the_eligibility_rule_itself_is_unchanged():
    """Prose-only, asserted rather than claimed: the structural rule that actually
    excludes these sleeves is their absence from the map, and this PR does not
    touch it."""
    from src.location_config import SLEEVE_PRIORITY_BY_ACCOUNT_TYPE
    roth = SLEEVE_PRIORITY_BY_ACCOUNT_TYPE["roth_ira"]
    assert "emerging_markets" in roth
    for s in ("intl_developed", "intl_all_exus",
              "intl_quality", "intl_large_value", "intl_small_value"):
        assert s not in roth, f"{s} entered the Roth map — this PR is prose-only"
