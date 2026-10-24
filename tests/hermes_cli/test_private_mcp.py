"""Contracts for the separate operator-approved private MCP registry."""
import hashlib
import json
from pathlib import Path

import pytest

from hermes_cli import private_mcp


@pytest.fixture
def home(tmp_path, monkeypatch):
    value = tmp_path / ".hermes"
    value.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(value))
    return value


def manifest(path: Path, command: str, *, name="local-tool") -> str:
    body = {"schema_version": 1, "name": name, "server": {
        "command": command, "args": [], "env": {"TOKEN": "${PRIVATE_TOKEN}"}}}
    raw = json.dumps(body).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def test_register_binds_immutable_entry_to_active_profile(home, tmp_path):
    executable = tmp_path / "server"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o700)
    path = tmp_path / "manifest.json"
    digest = manifest(path, str(executable))

    entry = private_mcp.register_manifest(path, sha256=digest, approve_digest=digest)

    assert entry.name == "local-tool"
    assert entry.profile_home == home.resolve()
    assert entry.provenance == "operator-approved-custom"
    assert entry.auth_env[0]["name"] == "PRIVATE_TOKEN"
    assert private_mcp.load_private_entry("local-tool").digest == digest


def test_digest_mismatch_and_literal_secret_are_rejected(home, tmp_path):
    executable = tmp_path / "server"
    executable.write_text("server")
    executable.chmod(0o700)
    path = tmp_path / "manifest.json"
    digest = manifest(path, str(executable))
    with pytest.raises(ValueError, match="digest"):
        private_mcp.register_manifest(path, sha256="0" * 64, approve_digest=digest)

    raw = json.loads(path.read_text())
    raw["server"]["env"]["TOKEN"] = "literal-secret"
    path.write_text(json.dumps(raw))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="references"):
        private_mcp.register_manifest(path, sha256=digest, approve_digest=digest)


def test_registry_does_not_cross_profiles(home, tmp_path, monkeypatch):
    executable = tmp_path / "server"
    executable.write_text("server")
    executable.chmod(0o700)
    path = tmp_path / "manifest.json"
    digest = manifest(path, str(executable))
    private_mcp.register_manifest(path, sha256=digest, approve_digest=digest)
    second = tmp_path / "second"
    second.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(second))
    with pytest.raises(ValueError, match="no operator-approved"):
        private_mcp.load_private_entry("local-tool")


def test_invalid_or_http_sensitive_manifest_rejected(tmp_path):
    with pytest.raises(ValueError, match="absolute executable"):
        private_mcp._validate_manifest({"schema_version": 1, "name": "x",
                                        "server": {"command": "sh"}})
    with pytest.raises(ValueError, match="https://"):
        private_mcp._validate_manifest({"schema_version": 1, "name": "x",
                                        "server": {"transport": "http", "url": "http://127.0.0.1"}})
