"""#268 — Page 11's scope caption states scope, not existence.

It renders before the data load, so it cannot know whether figures follow. The old
present tense ("Current allocation and drift ARE the X book") read as a promise the
empty-book states then withdrew. The caption must stay true in every state.
"""
from streamlit.testing.v1 import AppTest


def test_page11_scope_caption_is_conditional_not_a_promise():
    at = AppTest.from_file("pages/11_Capital_Deployment.py", default_timeout=90).run()
    assert not at.exception, f"page 11 raised: {at.exception}"
    # Keyed on the conditional phrase itself, which both modes share: the account
    # it names differs (the demo's is the paper-trade portfolio).
    caps = [c.value for c in at.caption
            if "figures on this page, when shown, are for the" in c.value]
    assert caps, "the scope caption did not render"
    assert "figures on this page, when shown, are for the" in caps[0], caps[0]
    assert "Current allocation and drift are the" not in caps[0], caps[0]
