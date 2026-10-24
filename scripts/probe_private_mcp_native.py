#!/usr/bin/env python3
"""Isolated native-consumer probe; real Hermes+MC code, synthetic read-only HTTP data.

Never accepts a production profile. All registration/install writes are confined to
new temporary fixture profiles. Approval callback is explicitly synthetic input.
This is not production installation or an LLM-backed resumed-session proof.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def decode(value):
    return json.loads(value) if isinstance(value, str) else value


def is_healthy(value):
    if isinstance(value, str):
        try:
            return is_healthy(json.loads(value))
        except ValueError:
            return False
    if isinstance(value, dict):
        return value.get("healthy") is True or any(is_healthy(item) for item in value.values())
    if isinstance(value, list):
        return any(is_healthy(item) for item in value)
    return False


@contextlib.contextmanager
def profile_scope(home):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from agent.secret_scope import set_secret_scope, reset_secret_scope, set_multiplex_context, reset_multiplex_context
    token = set_hermes_home_override(str(home))
    secrets = set_secret_scope({}, profile_home=str(home))
    multiplex = set_multiplex_context(True)
    try:
        yield
    finally:
        reset_multiplex_context(multiplex)
        reset_secret_scope(secrets)
        reset_hermes_home_override(token)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission-control-source", type=Path, required=True)
    args = parser.parse_args()
    source = args.mission_control_source.resolve()
    bundle = source / "skills/mission-control"
    assert (source / "mission_control/server.py").is_file()
    assert (bundle / "references/read-first.md").is_file()
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    requests = []

    class Fixture(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            requests.append((self.command, self.path))
            data = {"/api/missions": [], "/api/runs": [], "/api/agents": [],
                    "/api/agents/defs": {"agents": []},
                    "/api/capabilities": {"registry": {"skills": [], "mcpServers": []},
                                          "instances": [], "deployment": "isolated-fixture"}}
            self.send_response(200 if self.path in data else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "data": data.get(self.path)}).encode())

        def do_POST(self):
            requests.append((self.command, self.path))
            self.send_error(405)

    http = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    evidence = {"kind": "isolated-native-consumer-with-synthetic-http-data",
                "canonical_mission_control_revision": revision, "profiles": [], "production_verified": False}
    try:
        with tempfile.TemporaryDirectory(prefix="private-mcp-native-", dir=os.environ["TMPDIR"]) as temp:
            temp = Path(temp)
            launcher = temp / "launch_canonical.py"
            # `runpy.run_module(..., run_name='__main__')` breaks FastMCP's deferred pydantic
            # forward-ref rebuild for this server's `from __future__ import annotations` module
            # (PydanticUserError: mc_create_missionArguments is not fully defined) even though
            # the module's own globals contain every referenced name. The canonical launch path
            # — `python -m mission_control.server` — does not hit this, and that is exactly what
            # the project's own tests/test_stdio_fixture.py uses, so mirror it instead of re-deriving
            # a module launch mechanism.
            launcher.write_text(
                "import os,subprocess,sys\n"
                f"os.environ['MC_BASE_URL']='http://127.0.0.1:{http.server_port}'\n"
                "os.environ['MC_ALLOW_WRITES']=''\n"
                "os.environ.pop('MC_API_BEARER_TOKEN',None)\n"
                f"sys.exit(subprocess.call([sys.executable,'-m','mission_control.server'],cwd={str(source)!r}))\n")
            from hermes_cli.private_mcp import register_manifest
            from hermes_cli.private_mcp_skill import tree_digest
            from hermes_cli.private_mcp_lifecycle import disable, restore, detach
            from tools.connectors import live
            from tools.connectors.mcp import apply_answer
            from tools.connectors.tool import manage_connections
            from tools.mcp_tool_discovery import discover_mcp_tools, reconcile_mcp_servers_with_config
            from tools.mcp_tool_lifecycle import shutdown_mcp_servers
            from tools.registry import registry
            from tools.skills_tool import skill_view, clear_skills_cache
            from agent.skill_commands import build_preloaded_skills_prompt, scan_skill_commands

            manifest = temp / "manifest.json"
            manifest.write_text(json.dumps({"schema_version": 1, "name": "mission-control",
                "server": {"command": sys.executable, "args": [str(launcher)]},
                "companion_skill": {"name": "mission-control", "source": str(bundle), "sha256": tree_digest(bundle)}}))
            digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
            for label in ("fixture-profile-a", "fixture-profile-b"):
                home = temp / label
                home.mkdir(mode=0o700)
                with profile_scope(home):
                    scope = registry.current_scope_key()
                    try:
                        register_manifest(manifest, sha256=digest, approve_digest=digest)
                        approved = set()
                        def approve(payload):
                            for target in payload["targets"]:
                                if target["state"] == "pending" and target["name"] not in approved:
                                    assert "NOT Nous-reviewed" in target["instructions"]
                                    approved.add(target["name"])
                                    from tools.connectors.gateway.config import operation_session_key
                                    operation = live.get(operation_session_key(label), payload["op_id"], profile_home=str(home))
                                    assert operation is not None
                                    operation.deadline_at = min(operation.deadline_at, time.time() + 30)
                                    apply_answer(operation, json.dumps({"targets": [{"name": target["name"], "status": "approved"}]}))
                        result = json.loads(manage_connections({"action": "install_custom", "connectors": [
                            {"name": "mission-control", "mcp": True}]}, session_id=label, connection_callback=approve))
                        assert result["targets"][0]["state"] == "connected", result
                        assert result["companion_skills"]["mission-control"]["status"] == "installed", result
                        names = discover_mcp_tools(["mission-control"])
                        health = [name for name in names if name.endswith("_mc_health")]
                        assert len(health) == 1, names
                        health = health[0]
                        response = decode(registry.dispatch(health, {}, scope=scope))
                        assert is_healthy(response), response
                        clear_skills_cache()
                        commands = scan_skill_commands()
                        assert "/mission-control" in commands, commands.keys()
                        view = json.loads(skill_view("mission-control"))
                        assert view["success"] and "mc_health" in view["content"], view
                        resource = json.loads(skill_view("mission-control", file_path="references/read-first.md"))
                        assert resource["success"], resource
                        prompt, loaded, missing = build_preloaded_skills_prompt(["mission-control"])
                        assert loaded == ["mission-control"] and not missing and "mc_health" in prompt
                        disable("mission-control")
                        # Existing native consumer remains live until supported reconciliation.
                        retained = decode(registry.dispatch(health, {}, scope=scope))
                        assert is_healthy(retained), retained
                        reconciled = reconcile_mcp_servers_with_config()
                        assert "mission-control" in reconciled["removed"], reconciled
                        denied = decode(registry.dispatch(health, {}, scope=scope))
                        assert "error" in denied, denied
                        assert not discover_mcp_tools(["mission-control"])
                        restore("mission-control")
                        names = discover_mcp_tools(["mission-control"])
                        assert health in names
                        assert is_healthy(registry.dispatch(health, {}, scope=scope))
                        detach("mission-control")
                        reconcile_mcp_servers_with_config()
                        assert not discover_mcp_tools(["mission-control"])
                        assert (home / "skills/mission-control/references/read-first.md").is_file()
                        evidence["profiles"].append({"label": label, "native_management_install": True,
                            "mcp_tools": len(names), "native_skill_discovery": True, "native_skill_prompt_loading": True,
                            "native_skill_resource_loading": True, "safe_call": "mc_health",
                            "config_only_disable_keeps_existing_consumer_live": True,
                            "supported_reconcile_denies_calls": True, "restore_call_verified": True,
                            "detach_preserves_skill": True})
                    finally:
                        shutdown_mcp_servers(scope=scope)
                        live.reset_for_tests()
    finally:
        http.shutdown()
        http.server_close()
        thread.join(5)
    assert requests and all(method == "GET" for method, _ in requests), requests
    evidence["http_read_requests"] = len(requests)
    evidence["application_mutations"] = 0
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
