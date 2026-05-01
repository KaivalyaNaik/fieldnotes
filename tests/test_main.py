import re

import main


def test_get_recent_deploys_docstring_does_not_leak_internal_terms():
    """The user-facing docstring must use operator-friendly phrasing and
    avoid internal jargon. `\\bstem\\b` prevents false-positive matches on
    words like "system" or "stemmed"."""
    doc = main.get_recent_deploys.__doc__ or ""
    assert "service identifier configured by your operator" in doc
    assert not re.search(r"\bstem\b", doc, re.IGNORECASE)
