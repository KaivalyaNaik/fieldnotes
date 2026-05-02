import re

import main


def test_get_recent_deploys_docstring_does_not_leak_internal_terms():
    """The user-facing docstring must use operator-friendly phrasing and
    avoid internal jargon. `\\bstem\\b` prevents false-positive matches on
    words like "system" or "stemmed"."""
    doc = main.get_recent_deploys.__doc__ or ""
    assert "service identifier configured by your operator" in doc
    assert not re.search(r"\bstem\b", doc, re.IGNORECASE)


def test_tail_logs_docstring_does_not_leak_backend_jargon():
    """Same contract as `get_recent_deploys`: the docstring is what the
    operator sees in their MCP client, so it must not name the concrete
    log backend or its query language. Negative-only — positive phrasing
    is intentionally not pinned, to avoid brittle copy-edit failures."""
    doc = main.tail_logs.__doc__ or ""
    assert "Loki" not in doc
    assert "LogQL" not in doc
