"""Regression coverage for private manifest authority, not live deployment."""
import hashlib
import json

import pytest

from hermes_cli import private_mcp


@pytest.fixture
def registered(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    executable = tmp_path / "server"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    raw = {"schema_version": 1, "name": "example", "server": {"command": str(executable)}}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(raw))
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    private_mcp.register_manifest(manifest, sha256=digest, approve_digest=digest)
    return home, executable, manifest, digest


def test_modified_registry_server_cannot_keep_old_approval_digest(registered):
    home, _, _, _ = registered
    registry = home / "private_mcp/registry.json"
    data = json.loads(registry.read_text())
    data["entries"]["example"]["server"]["args"] = ["--changed"]
    registry.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="digest|approval|snapshot"):
        private_mcp.read_private_entry("example")


def test_executable_drift_invalidates_registration(registered):
    _, executable, _, _ = registered
    executable.write_text("#!/bin/sh\nexit 1\n")
    with pytest.raises(ValueError, match="executable|artifact"):
        private_mcp.read_private_entry("example")


def test_registry_symlink_is_rejected(registered, tmp_path):
    home, _, _, _ = registered
    registry = home / "private_mcp/registry.json"
    outside = tmp_path / "outside.json"
    registry.rename(outside)
    registry.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink|trusted"):
        private_mcp.read_private_entry("example")


@pytest.mark.parametrize("server", [
    {"command": "/bin/true", "url": "https://example.com/mcp"},
    {"transport": "http", "url": "https://user:password@example.com/mcp"},
    {"transport": "http", "url": "https://example.com/mcp#fragment"},
    {"transport": "http", "url": "https://example.com/mcp", "enabled": "false"},
    {"transport": "http", "url": "https://example.com/mcp", "enabled": False},
])
def test_ambiguous_or_credential_bearing_transport_is_rejected(server):
    with pytest.raises(ValueError):
        private_mcp._validate_manifest({"schema_version": 1, "name": "example", "server": server})


def test_manifest_rejects_unknown_authority_fields():
    with pytest.raises(ValueError):
        private_mcp._validate_manifest({"schema_version": 1, "name": "example", "server": {"command": "/bin/true"},
                                        "other_profile": "/foreign"})
