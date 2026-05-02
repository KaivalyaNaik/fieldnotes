import json
import re

import pytest

from service_map import load_flat_map


def _accept_all(_value: str) -> None:
    return None


def _reject_with_bang(value: str) -> None:
    if "!" in value:
        raise ValueError(f"value {value!r} must not contain '!'")


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("MAP_ENV", raising=False)
    monkeypatch.delenv("MAP_FILE", raising=False)


def _load() -> dict[str, str]:
    return load_flat_map("MAP_ENV", "MAP_FILE", _accept_all)


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
        # README:61 — keys are free-form. Spaces, slashes, dots are fine.
        ("payments/v2=deploy-payments", {"payments/v2": "deploy-payments"}),
        ("billing.api=deploy-billing", {"billing.api": "deploy-billing"}),
    ],
)
def test_env_parsing(monkeypatch, clean_env, raw, expected):
    monkeypatch.setenv("MAP_ENV", raw)
    assert _load() == expected


@pytest.mark.parametrize(
    "raw, err_match",
    [
        ("payments", "missing '='"),
        ("=deploy-payments", "empty key"),
        ("payments=", "empty value"),
        ("payments=deploy-payments,payments=other", "duplicate"),
    ],
)
def test_env_parsing_errors(monkeypatch, clean_env, raw, err_match):
    monkeypatch.setenv("MAP_ENV", raw)
    with pytest.raises(RuntimeError, match=err_match):
        _load()


def test_validator_rejects_value(monkeypatch, clean_env):
    monkeypatch.setenv("MAP_ENV", "payments=bad!name")
    with pytest.raises(RuntimeError, match="must not contain '!'"):
        load_flat_map("MAP_ENV", "MAP_FILE", _reject_with_bang)


def test_from_json_file(monkeypatch, clean_env, tmp_path):
    p = tmp_path / "map.json"
    p.write_text(json.dumps({"payments": "deploy-payments", "api": "release"}))
    monkeypatch.setenv("MAP_FILE", str(p))
    assert _load() == {"payments": "deploy-payments", "api": "release"}


def test_file_missing_raises(monkeypatch, clean_env, tmp_path):
    missing = tmp_path / "nope.json"
    monkeypatch.setenv("MAP_FILE", str(missing))
    with pytest.raises(RuntimeError, match="nope.json"):
        _load()


def test_file_unsupported_suffix_raises(monkeypatch, clean_env, tmp_path):
    p = tmp_path / "map.yaml"
    p.write_text("payments: deploy-payments")
    monkeypatch.setenv("MAP_FILE", str(p))
    with pytest.raises(RuntimeError, match="only .json"):
        _load()


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
def test_file_not_a_mapping_raises(monkeypatch, clean_env, tmp_path, payload):
    p = tmp_path / "map.json"
    p.write_text(json.dumps(payload))
    monkeypatch.setenv("MAP_FILE", str(p))
    with pytest.raises(RuntimeError, match="flat object"):
        _load()


def test_file_validator_rejects_value(monkeypatch, clean_env, tmp_path):
    p = tmp_path / "map.json"
    p.write_text(json.dumps({"payments": "bad!name"}))
    monkeypatch.setenv("MAP_FILE", str(p))
    with pytest.raises(RuntimeError, match="must not contain '!'"):
        load_flat_map("MAP_ENV", "MAP_FILE", _reject_with_bang)


def test_both_env_and_file_set_raises(monkeypatch, clean_env, tmp_path):
    p = tmp_path / "map.json"
    p.write_text("{}")
    monkeypatch.setenv("MAP_ENV", "payments=deploy-payments")
    monkeypatch.setenv("MAP_FILE", str(p))
    with pytest.raises(RuntimeError, match="not both"):
        _load()


def test_whitespace_only_env_yields_to_file(monkeypatch, clean_env, tmp_path):
    p = tmp_path / "map.json"
    p.write_text(json.dumps({"payments": "deploy-payments"}))
    monkeypatch.setenv("MAP_ENV", "   ")
    monkeypatch.setenv("MAP_FILE", str(p))
    assert _load() == {"payments": "deploy-payments"}


def test_whitespace_only_file_yields_to_env(monkeypatch, clean_env):
    """Symmetric to the env→file case: a whitespace-only file env var is
    treated as unset, so a valid env map wins (and isn't rejected as a
    both-set conflict)."""
    monkeypatch.setenv("MAP_ENV", "payments=deploy-payments")
    monkeypatch.setenv("MAP_FILE", "   ")
    assert _load() == {"payments": "deploy-payments"}


def test_file_empty_object_returns_empty(monkeypatch, clean_env, tmp_path):
    p = tmp_path / "map.json"
    p.write_text("{}")
    monkeypatch.setenv("MAP_FILE", str(p))
    assert _load() == {}


def test_file_empty_file_raises(monkeypatch, clean_env, tmp_path):
    p = tmp_path / "map.json"
    p.write_text("")
    monkeypatch.setenv("MAP_FILE", str(p))
    with pytest.raises(RuntimeError, match="invalid JSON"):
        _load()


def test_file_directory_path_raises(monkeypatch, clean_env, tmp_path):
    d = tmp_path / "map.json"
    d.mkdir()
    monkeypatch.setenv("MAP_FILE", str(d))
    with pytest.raises(RuntimeError, match=re.escape(str(d))):
        _load()
