"""Approval lifecycle tests. This backend is deliberately a unit-test fake."""
import json

import pytest

from tools.connectors import live
from tools.connectors.mcp import apply_answer
from tools.connectors.tool import manage_connections


class ApprovedFixture:
    def __init__(self, names):
        self.names = names
        self.calls = []

    def provenance(self):
        return {"approval": "operator", "profile": "isolated-test"}

    def instructions(self, name):
        return "Private; operator-approved, NOT Nous-reviewed."

    def required_env(self, name):
        return []

    def installs_with_oauth(self, name):
        return False

    def install(self, name, env):
        self.calls.append((name, env))
        if env:
            raise ValueError("private installs do not accept card credentials")
        return []


@pytest.mark.parametrize("answer, installs", [("approved", 1), ("skipped", 0)])
def test_private_card_is_the_native_approval_state_machine(monkeypatch, answer, installs):
    from hermes_cli import private_mcp

    fixture = ApprovedFixture(["example"])
    monkeypatch.setattr(private_mcp, "PrivateBackend", lambda names: fixture)
    monkeypatch.setattr("tools.connectors.mcp._register_connected", lambda *args: ([], ""))
    live.reset_for_tests()

    def callback(payload):
        assert "NOT Nous-reviewed" in payload["targets"][0]["instructions"]
        operation = live.get("private-test", payload["op_id"])
        apply_answer(operation, json.dumps({"targets": [{"name": "example", "status": answer}]}))

    try:
        result = json.loads(manage_connections(
            {"action": "install_custom", "connectors": [{"name": "example", "mcp": True}]},
            session_id="private-test", connection_callback=callback,
        ))
        assert result["source"] == "private"
        assert result["requested_action"] == "install_custom"
        assert "NOT Nous-reviewed" in result["note"]
        assert len(fixture.calls) == installs
    finally:
        live.reset_for_tests()
