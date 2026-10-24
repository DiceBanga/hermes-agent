"""Private installs are a distinct, operator-approved management route."""
import json
from unittest.mock import patch

from tools.connectors.targets import MCP_ACTIONS, normalize_targets, validate_action
from tools.connectors.tool import MANAGE_CONNECTIONS_SCHEMA, manage_connections


def request(name="local-example"):
    return {"action": "install_custom", "connectors": [{"name": name, "mcp": True}]}


def test_private_install_has_an_explicit_action_not_catalog_relaxation():
    assert "install_custom" in MCP_ACTIONS
    assert "install_custom" in MANAGE_CONNECTIONS_SCHEMA["parameters"]["properties"]["action"]["enum"]
    assert validate_action("install_custom", [], ["local-example"]) is None


def test_private_install_does_not_accept_hosted_targets():
    hosted, names, error = normalize_targets(["local-example"])
    assert error is None
    assert validate_action("install_custom", hosted, names)


def test_model_cannot_supply_private_commands_paths_or_credentials():
    for field, value in [("command", "/bin/sh"), ("url", "http://localhost/mcp"),
                         ("manifest", "/operator.json"), ("env", {"KEY": "secret"})]:
        args = request()
        args["connectors"][0][field] = value
        out = json.loads(manage_connections(args))
        assert "unknown target field" in out["error"]


def test_private_install_has_no_model_disconnect_or_rollback_action():
    for action in ("disable_custom", "detach_custom", "revoke_custom", "rollback_custom"):
        assert action not in MCP_ACTIONS
        assert validate_action(action, [], ["local-example"])


def test_unregistered_private_entry_fails_without_native_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with patch("hermes_cli.mcp_config._save_mcp_server") as save:
        out = json.loads(manage_connections(request(), session_id="private-test"))
    assert "error" in out
    save.assert_not_called()
    assert not (tmp_path / "config.yaml").exists()


def test_catalog_install_still_rejects_unknown_server():
    with patch("tools.connectors.mcp._catalog_names", return_value=[]), \
         patch("tools.connectors.mcp._configured_names", return_value=[]):
        args = request()
        args["action"] = "install"
        out = json.loads(manage_connections(args))
    assert "Install works for catalog entries only" in out["error"]
