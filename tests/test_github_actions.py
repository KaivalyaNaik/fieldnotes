import pytest

from github_actions import _map_status, _to_deployment


@pytest.mark.parametrize(
    "status,conclusion,expected",
    [
        ("completed", "success", "success"),
        ("completed", "failure", "failure"),
        ("completed", "timed_out", "failure"),
        ("completed", "startup_failure", "failure"),
        ("completed", "cancelled", "cancelled"),
        ("completed", "action_required", "action_required"),
        ("completed", "neutral", "neutral"),
        ("completed", "skipped", "skipped"),
        ("completed", "stale", "stale"),
        ("in_progress", None, "in_progress"),
        ("queued", None, "pending"),
        ("waiting", None, "unknown"),
        (None, None, "unknown"),
    ],
)
def test_map_status(status, conclusion, expected):
    assert _map_status(status, conclusion) == expected


def test_to_deployment_happy_path():
    run = {
        "id": 12345,
        "status": "completed",
        "conclusion": "success",
        "actor": {"login": "alice"},
        "run_started_at": "2026-05-01T10:00:00Z",
        "head_sha": "abc123",
        "html_url": "https://github.com/x/y/actions/runs/12345",
    }
    d = _to_deployment(run)
    assert d.id == "12345"
    assert d.status == "success"
    assert d.actor == "alice"
    assert d.commit_sha == "abc123"
    assert d.url == "https://github.com/x/y/actions/runs/12345"


def test_to_deployment_missing_actor_defaults_to_unknown():
    run = {
        "id": 9,
        "status": "in_progress",
        "conclusion": None,
        "actor": None,
        "run_started_at": "2026-05-01T10:00:00Z",
        "head_sha": "deadbeef",
    }
    d = _to_deployment(run)
    assert d.actor == "unknown"
    assert d.status == "in_progress"
    assert d.url is None
