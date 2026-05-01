import json

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


def test_resolve_workflow_stem_passthrough_when_map_empty():
    assert _resolve_workflow_stem("deploy-payments", {}) == "deploy-payments"


def test_resolve_workflow_stem_uses_mapping_when_present():
    mapping = {"payments": "deploy-payments"}
    assert _resolve_workflow_stem("payments", mapping) == "deploy-payments"


def test_resolve_workflow_stem_unknown_strict_raises_with_known_keys():
    mapping = {"payments": "deploy-payments", "api": "release"}
    with pytest.raises(
        ValueError, match=r"unknown service 'paymets'\. known: api, payments"
    ):
        _resolve_workflow_stem("paymets", mapping)


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://example.test/x")
    response = httpx.Response(status, text="", request=request)
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
