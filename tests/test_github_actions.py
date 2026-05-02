import httpx
import pytest

from github_actions import (
    GitHubActionsBackend,
    _friendly_http_error,
    _map_status,
    _to_deployment,
    _validate_workflow_stem,
)


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


@pytest.mark.parametrize(
    "stem", ["deploy-payments", "release", "ci.yml.disabled", "a_b.c-d"]
)
def test_validate_workflow_stem_accepts(stem):
    _validate_workflow_stem(stem)


@pytest.mark.parametrize("stem", ["bad name!", "deploy=payments", "a/b", ""])
def test_validate_workflow_stem_rejects(stem):
    with pytest.raises(ValueError, match=r"\[A-Za-z0-9_\.-\]"):
        _validate_workflow_stem(stem)


def _http_status_error(
    status: int, headers: dict | None = None, text: str = ""
) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test/x")
    response = httpx.Response(
        status, headers=headers or {}, text=text, request=request
    )
    return httpx.HTTPStatusError(f"{status}", request=request, response=response)


def test_friendly_http_error_404_names_both_when_logical_differs_from_stem():
    msg = _friendly_http_error(
        _http_status_error(404), "payments", "deploy-payments", "owner/repo"
    )
    assert "'payments'" in msg
    assert "deploy-payments.yml" in msg
    assert "owner/repo" in msg


def test_friendly_http_error_404_compact_when_logical_equals_stem():
    msg = _friendly_http_error(_http_status_error(404), "ci", "ci", "owner/repo")
    assert "workflow ci.yml not found" in msg
    assert "resolved to" not in msg


def test_friendly_http_error_403_includes_repo():
    msg = _friendly_http_error(
        _http_status_error(403), "payments", "deploy-payments", "owner/repo"
    )
    assert "owner/repo" in msg


def test_friendly_http_error_403_rate_limit_branch():
    """403 has two paths: the rate-limit branch (X-RateLimit-Remaining: 0)
    and the forbidden branch. Cover the rate-limit branch and pin that the
    new logical/stem args do not leak in."""
    err = _http_status_error(
        403,
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1700000000"},
    )
    msg = _friendly_http_error(err, "payments", "deploy-payments", "owner/repo")
    assert "rate limit exceeded" in msg
    assert "1700000000" in msg
    assert "payments" not in msg
    assert "deploy-payments" not in msg


@pytest.mark.parametrize("code", [401, 403, 429, 500])
def test_friendly_http_error_non_404_branches_do_not_leak_service_names(code):
    """The new `logical` and `stem` parameters must not bleed into branches
    that are network-level errors keyed off `repo` only."""
    msg = _friendly_http_error(
        _http_status_error(code), "payments", "deploy-payments", "owner/repo"
    )
    assert "payments" not in msg
    assert "deploy-payments" not in msg


def test_friendly_http_error_500_truncates_long_body():
    """The 500 branch interpolates `err.response.text[:200]`. Pin the cap so
    a future change to the slice is caught."""
    err = _http_status_error(500, text="x" * 300)
    msg = _friendly_http_error(err, "payments", "deploy-payments", "owner/repo")
    assert "x" * 200 in msg
    assert "x" * 201 not in msg


@pytest.fixture
def github_creds(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")


@pytest.fixture
def clean_map_env(monkeypatch):
    monkeypatch.delenv("FIELDNOTES_SERVICE_MAP", raising=False)
    monkeypatch.delenv("FIELDNOTES_SERVICE_MAP_FILE", raising=False)


def test_backend_init_wires_service_map(monkeypatch, clean_map_env, github_creds):
    monkeypatch.setenv("FIELDNOTES_SERVICE_MAP", "payments=deploy-payments")
    backend = GitHubActionsBackend()
    assert backend._service_map == {"payments": "deploy-payments"}


def test_backend_init_raises_on_malformed_service_map(
    monkeypatch, clean_map_env, github_creds
):
    monkeypatch.setenv("FIELDNOTES_SERVICE_MAP", "missing-equals")
    with pytest.raises(RuntimeError, match="missing '='"):
        GitHubActionsBackend()


def test_backend_init_rejects_invalid_workflow_stem(
    monkeypatch, clean_map_env, github_creds
):
    """The validator passed to load_flat_map must reject malformed stems
    at load time. Error must name the env var so the operator knows what
    to fix."""
    monkeypatch.setenv("FIELDNOTES_SERVICE_MAP", "payments=bad name!")
    with pytest.raises(
        RuntimeError, match=r"FIELDNOTES_SERVICE_MAP.*\[A-Za-z0-9_\.-\]"
    ):
        GitHubActionsBackend()


class _FakeResp:
    def __init__(self, payload: dict | None = None):
        self._payload = payload or {"workflow_runs": []}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_get_recent_deploys_resolves_before_regex_check(
    monkeypatch, clean_map_env, github_creds
):
    """A logical name with a space (regex-illegal) must reach GitHub as the
    resolved stem, never as the unresolved input. Pins resolve→validate
    ordering at the backend's call site."""
    monkeypatch.setenv("FIELDNOTES_SERVICE_MAP", "my service=deploy-payments")
    backend = GitHubActionsBackend()
    captured: dict[str, str] = {}

    def fake_get(url, params=None):
        captured["url"] = url
        return _FakeResp()

    monkeypatch.setattr(backend._client, "get", fake_get)
    backend.get_recent_deploys("my service", limit=10)
    assert "deploy-payments.yml" in captured["url"]
    assert "my service" not in captured["url"]


def test_get_recent_deploys_passthrough_when_map_empty(
    monkeypatch, clean_map_env, github_creds
):
    backend = GitHubActionsBackend()
    captured: dict[str, str] = {}

    def fake_get(url, params=None):
        captured["url"] = url
        return _FakeResp()

    monkeypatch.setattr(backend._client, "get", fake_get)
    backend.get_recent_deploys("deploy-payments", limit=10)
    assert "deploy-payments.yml" in captured["url"]


def test_get_recent_deploys_strict_unknown_when_map_set(
    monkeypatch, clean_map_env, github_creds
):
    """Once a map is set, names not in the map are rejected with a useful
    error listing the known keys. Codifies the README's all-or-none semantic."""
    monkeypatch.setenv(
        "FIELDNOTES_SERVICE_MAP", "payments=deploy-payments,api=release"
    )
    backend = GitHubActionsBackend()
    with pytest.raises(
        ValueError, match=r"unknown service 'paymets'\. known: api, payments"
    ):
        backend.get_recent_deploys("paymets", limit=10)


def test_get_recent_deploys_strict_rejects_workflow_stems_not_in_map(
    monkeypatch, clean_map_env, github_creds
):
    """Once a map is set, even a name that looks like a valid workflow stem
    is rejected unless it's a key. Codifies all-or-none."""
    monkeypatch.setenv("FIELDNOTES_SERVICE_MAP", "payments=deploy-payments")
    backend = GitHubActionsBackend()
    with pytest.raises(ValueError, match=r"unknown service 'deploy-web'"):
        backend.get_recent_deploys("deploy-web", limit=10)


def test_get_recent_deploys_rejects_bad_stem_even_if_map_loader_was_bypassed(
    monkeypatch, clean_map_env, github_creds
):
    """Last-line-of-defense: hand-craft a malformed _service_map (bypassing
    the loader) and confirm the regex still rejects before any URL is built."""
    backend = GitHubActionsBackend()
    # Intentionally pokes a private attribute to bypass the loader's validation.
    backend._service_map = {"x": "bad;name"}
    with pytest.raises(ValueError, match=r"Invalid workflow stem"):
        backend.get_recent_deploys("x", limit=10)
