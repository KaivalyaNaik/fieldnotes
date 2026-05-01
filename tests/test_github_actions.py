import json
import re

import httpx
import pytest

from github_actions import (
    GitHubActionsBackend,
    _friendly_http_error,
    _load_service_map,
    _map_status,
    _resolve_workflow_stem,
    _to_deployment,
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


@pytest.fixture
def clean_map_env(monkeypatch):
    monkeypatch.delenv("FIELDNOTES_SERVICE_MAP", raising=False)
    monkeypatch.delenv("FIELDNOTES_SERVICE_MAP_FILE", raising=False)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("", {}),
        ("   ", {}),
        ("payments=deploy-payments", {"payments": "deploy-payments"}),
        (
            " payments = deploy-payments , api=release ",
            {"payments": "deploy-payments", "api": "release"},
        ),
        ("payments=deploy-payments,", {"payments": "deploy-payments"}),
        ("my service=deploy-payments", {"my service": "deploy-payments"}),
    ],
)
def test_service_map_env_parsing(monkeypatch, clean_map_env, raw, expected):
    monkeypatch.setenv("FIELDNOTES_SERVICE_MAP", raw)
    assert _load_service_map() == expected


@pytest.mark.parametrize(
    "raw, err_match",
    [
        ("payments", "missing '='"),
        ("=deploy-payments", "empty key"),
        ("payments=", "empty value"),
        ("payments=deploy-payments,payments=other", "duplicate"),
        ("payments=bad name!", r"\[A-Za-z0-9_\.-\]"),
        ("payments=deploy=payments", r"\[A-Za-z0-9_\.-\]"),
    ],
)
def test_service_map_env_parsing_errors(monkeypatch, clean_map_env, raw, err_match):
    monkeypatch.setenv("FIELDNOTES_SERVICE_MAP", raw)
    with pytest.raises(RuntimeError, match=err_match):
        _load_service_map()


def test_service_map_from_json_file(monkeypatch, clean_map_env, tmp_path):
    p = tmp_path / "services.json"
    p.write_text(json.dumps({"payments": "deploy-payments", "api": "release"}))
    monkeypatch.setenv("FIELDNOTES_SERVICE_MAP_FILE", str(p))
    assert _load_service_map() == {"payments": "deploy-payments", "api": "release"}


def test_service_map_file_missing_raises(monkeypatch, clean_map_env, tmp_path):
    missing = tmp_path / "nope.json"
    monkeypatch.setenv("FIELDNOTES_SERVICE_MAP_FILE", str(missing))
    with pytest.raises(RuntimeError, match="nope.json"):
        _load_service_map()


def test_service_map_file_unsupported_suffix_raises(monkeypatch, clean_map_env, tmp_path):
    p = tmp_path / "services.yaml"
    p.write_text("payments: deploy-payments")
    monkeypatch.setenv("FIELDNOTES_SERVICE_MAP_FILE", str(p))
    with pytest.raises(RuntimeError, match="only .json"):
        _load_service_map()


@pytest.mark.parametrize(
    "payload",
    [
        ["payments", "api"],
        "not a dict",
        {"payments": 123},
        {"": "deploy-payments"},
        {"payments": ""},
    ],
)
def test_service_map_file_not_a_mapping_raises(
    monkeypatch, clean_map_env, tmp_path, payload
):
    p = tmp_path / "services.json"
    p.write_text(json.dumps(payload))
    monkeypatch.setenv("FIELDNOTES_SERVICE_MAP_FILE", str(p))
    with pytest.raises(RuntimeError, match="flat object"):
        _load_service_map()


def test_service_map_both_env_and_file_set_raises(monkeypatch, clean_map_env, tmp_path):
    p = tmp_path / "services.json"
    p.write_text("{}")
    monkeypatch.setenv("FIELDNOTES_SERVICE_MAP", "payments=deploy-payments")
    monkeypatch.setenv("FIELDNOTES_SERVICE_MAP_FILE", str(p))
    with pytest.raises(RuntimeError, match="not both"):
        _load_service_map()


def test_service_map_whitespace_only_env_yields_to_file(
    monkeypatch, clean_map_env, tmp_path
):
    p = tmp_path / "services.json"
    p.write_text(json.dumps({"payments": "deploy-payments"}))
    monkeypatch.setenv("FIELDNOTES_SERVICE_MAP", "   ")
    monkeypatch.setenv("FIELDNOTES_SERVICE_MAP_FILE", str(p))
    assert _load_service_map() == {"payments": "deploy-payments"}


def test_service_map_whitespace_only_file_yields_to_env(monkeypatch, clean_map_env):
    """Symmetric to the env→file case: a whitespace-only _FILE value is
    treated as unset, so a valid env map wins (and isn't rejected as a
    both-set conflict)."""
    monkeypatch.setenv("FIELDNOTES_SERVICE_MAP", "payments=deploy-payments")
    monkeypatch.setenv("FIELDNOTES_SERVICE_MAP_FILE", "   ")
    assert _load_service_map() == {"payments": "deploy-payments"}


def test_service_map_file_empty_object_returns_empty(
    monkeypatch, clean_map_env, tmp_path
):
    p = tmp_path / "services.json"
    p.write_text("{}")
    monkeypatch.setenv("FIELDNOTES_SERVICE_MAP_FILE", str(p))
    assert _load_service_map() == {}


def test_service_map_file_whitespace_padded_value_raises(
    monkeypatch, clean_map_env, tmp_path
):
    p = tmp_path / "services.json"
    p.write_text(json.dumps({"payments": "  deploy-payments  "}))
    monkeypatch.setenv("FIELDNOTES_SERVICE_MAP_FILE", str(p))
    with pytest.raises(RuntimeError, match=r"\[A-Za-z0-9_\.-\]"):
        _load_service_map()


def test_service_map_file_empty_file_raises(monkeypatch, clean_map_env, tmp_path):
    p = tmp_path / "services.json"
    p.write_text("")
    monkeypatch.setenv("FIELDNOTES_SERVICE_MAP_FILE", str(p))
    with pytest.raises(RuntimeError, match="invalid JSON"):
        _load_service_map()


def test_service_map_file_directory_path_raises(monkeypatch, clean_map_env, tmp_path):
    d = tmp_path / "services.json"
    d.mkdir()
    monkeypatch.setenv("FIELDNOTES_SERVICE_MAP_FILE", str(d))
    with pytest.raises(RuntimeError, match=re.escape(str(d))):
        _load_service_map()


def test_resolve_workflow_stem_passthrough_when_map_empty():
    assert _resolve_workflow_stem("deploy-payments", {}) == "deploy-payments"


def test_resolve_workflow_stem_uses_mapping_when_present():
    mapping = {"payments": "deploy-payments"}
    assert _resolve_workflow_stem("payments", mapping) == "deploy-payments"


def test_resolve_workflow_stem_identity_mapping():
    """Boundary for the 404 message branch (`logical != stem`)."""
    assert _resolve_workflow_stem("payments", {"payments": "payments"}) == "payments"


def test_resolve_workflow_stem_unknown_strict_raises_with_known_keys():
    mapping = {"payments": "deploy-payments", "api": "release"}
    with pytest.raises(
        ValueError, match=r"unknown service 'paymets'\. known: api, payments"
    ):
        _resolve_workflow_stem("paymets", mapping)


def test_resolve_workflow_stem_strict_rejects_previously_passing_workflow_stems():
    """Once a map is set, names not in the map are rejected even if they look
    like valid workflow stems. Codifies the README's all-or-none semantic."""
    mapping = {"payments": "deploy-payments"}
    with pytest.raises(ValueError, match=r"unknown service 'deploy-web'"):
        _resolve_workflow_stem("deploy-web", mapping)


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


def test_get_recent_deploys_resolves_before_regex_check(
    monkeypatch, clean_map_env, github_creds
):
    """A logical name with a space (regex-illegal) must reach GitHub as the
    resolved stem, never as the unresolved input. Pins resolve→validate
    ordering at github_actions.py:50-55."""
    monkeypatch.setenv("FIELDNOTES_SERVICE_MAP", "my service=deploy-payments")
    backend = GitHubActionsBackend()
    captured: dict[str, str] = {}

    class FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"workflow_runs": []}

    def fake_get(url, params=None):
        captured["url"] = url
        return FakeResp()

    monkeypatch.setattr(backend._client, "get", fake_get)
    backend.get_recent_deploys("my service", limit=10)
    assert "deploy-payments.yml" in captured["url"]
    assert "my service" not in captured["url"]


def test_get_recent_deploys_rejects_bad_stem_even_if_map_loader_was_bypassed(
    monkeypatch, clean_map_env, github_creds
):
    """Last-line-of-defense: hand-craft a malformed _service_map (bypassing
    the loader) and confirm the regex at github_actions.py:53 still rejects
    before any URL is built."""
    backend = GitHubActionsBackend()
    # Intentionally pokes a private attribute to bypass the loader's validation.
    backend._service_map = {"x": "bad;name"}
    with pytest.raises(ValueError, match=r"Invalid workflow stem"):
        backend.get_recent_deploys("x", limit=10)
