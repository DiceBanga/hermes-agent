"""Private MCP installs reuse the native approval lifecycle, never the public catalogue.

Only an operator can create the profile-bound, digest-approved registration. This
route accepts names, not manifests or commands, and cannot create that authority.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from tools.connectors.operation import Target
from tools.connectors.run import Kind, run_operation
from tools.connectors.gateway.config import operation_session_key
from tools.registry import tool_error

PRIVATE_NOTE = (
    "Private, operator-approved MCP integration; NOT Nous-reviewed or a catalogue install. "
    "Registration, saved configuration, discovery and effective native-runtime behavior are "
    "separate facts. Inspect target discovery_error before claiming tools work. "
    "Disable, detach, rollback and migration are operator-only lifecycle actions. "
    "A failed combined install can leave receipt-backed MCP configuration without its Skill; "
    "inspect installation_observations and operator status before recovery. "
    "Do not register private manifests or run the operator CLI on the user's behalf."
)


def run_private_mcp_operation(
    names: list[str], *,
    connection_callback: Optional[Callable[[dict[str, Any]], Optional[str]]],
    session_id: Optional[str], tool_call_id: Optional[str] = None,
) -> str:
    from hermes_cli.private_mcp import PrivateBackend
    from tools.connectors.mcp import _no_card_result, open_runner

    try:
        # Snapshots every entry before opening a card. Nothing from a model or
        # renderer can choose a different transport, source, profile or command.
        backend = PrivateBackend(names)
        provenance = backend.provenance()
    except (ValueError, OSError, RuntimeError) as exc:
        return tool_error(str(exc))
    runner = open_runner("install", backend)
    session_key = operation_session_key(session_id)

    def prepare(operation):
        runner.prepare(operation)
        for target in operation.targets:
            target.instructions = backend.instructions(target.name)

    try:
        if connection_callback is None:
            raw = _no_card_result(runner, names, "install", session_key)
        else:
            # Reuse install on the wire: the established UI state machine owns
            # approval/skip/retry, and cannot claim the worker's connected state.
            raw = run_operation(
                [Target(name, "mcp", "install") for name in names],
                Kind(prepare=prepare, observe=runner.observe, note=PRIVATE_NOTE),
                session_key=session_key, tool_call_id=tool_call_id,
                connection_callback=connection_callback, with_urls_in_result=False,
            )
        result = json.loads(raw)
        result.update(source="private", requested_action="install_custom",
                      provenance=provenance, note=PRIVATE_NOTE)
        result["companion_skills"] = getattr(backend, "skill_results", {})
        result["installation_observations"] = getattr(backend, "installation_observations", {})
        return json.dumps(result, ensure_ascii=False)
    finally:
        runner.close()
