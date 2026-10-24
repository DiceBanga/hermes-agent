"""Operator-approved, profile-local MCP entries, separate from the Nous catalogue."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home
from hermes_cli.private_mcp_validation import NAME, fingerprint_artifacts, reject_symlinks, validate_server

_SCHEMA_VERSION = 1
_MAX_MANIFEST = 256 * 1024


@dataclass(frozen=True)
class PrivateEntry:
    name: str
    profile_home: Path
    digest: str
    server_json: str
    skill_json: str = "null"
    provenance: str = "operator-approved-custom"
    schema_version: int = _SCHEMA_VERSION

    @property
    def server(self):
        return json.loads(self.server_json)

    @property
    def companion_skill(self):
        return json.loads(self.skill_json)

    @property
    def auth_env(self):
        return validate_server(self.server)


def _root() -> Path:
    path = Path(get_hermes_home()) / "private_mcp"
    reject_symlinks(path)
    return path


def _registry_path() -> Path:
    path = _root() / "registry.json"
    reject_symlinks(path)
    return path


def _read_registry() -> dict[str, Any]:
    path = _registry_path()
    if not path.exists():
        return {"schema_version": _SCHEMA_VERSION, "entries": {}}
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("private MCP registry exceeds size limit")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("private MCP registry unreadable") from exc
    if (not isinstance(data, dict) or data.get("schema_version") != _SCHEMA_VERSION
            or not isinstance(data.get("entries"), dict)):
        raise ValueError("private MCP registry has an unsupported or invalid schema")
    return data


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    reject_symlinks(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".private-mcp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _validate_manifest(raw):
    if (not isinstance(raw, dict) or raw.get("schema_version") != _SCHEMA_VERSION
            or set(raw) - {"schema_version", "name", "server", "companion_skill"}):
        raise ValueError("manifest must contain only schema_version, name, server and optional companion_skill")
    name, server = raw.get("name"), raw.get("server")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("invalid private MCP name; use an exact lowercase slug")
    fields = validate_server(server)
    if "companion_skill" in raw:
        from hermes_cli.private_mcp_skill import validate_skill_spec
        validate_skill_spec(raw["companion_skill"])
    return name, json.loads(json.dumps(server)), fields


def register_manifest(path, *, sha256, approve_digest=None):
    """Operator-only registration: approval is exact-byte and bound to this profile."""
    from hermes_cli.private_mcp_lifecycle import transaction_scope

    source = Path(path)
    if source.stat().st_size > _MAX_MANIFEST:
        raise ValueError("private MCP manifest exceeds size limit")
    raw_bytes = source.read_bytes()
    digest = hashlib.sha256(raw_bytes).hexdigest()
    if digest != sha256 or (approve_digest is not None and approve_digest != digest):
        raise ValueError("manifest digest does not match the supplied approval digest")
    if not sys.stdin.isatty() and approve_digest is None:
        raise ValueError("registration requires a TTY confirmation or --approve-digest <sha256>")
    raw = json.loads(raw_bytes)
    name, server, _ = _validate_manifest(raw)
    artifacts = fingerprint_artifacts(server)
    if approve_digest is None:
        print(f"Operator-approved private MCP: {name}; profile: {Path(get_hermes_home()).resolve()}")
        print(f"Manifest SHA-256: {digest}. NOT Nous-reviewed. Review code and dependency provenance.")
        if input("Type the full SHA-256 digest to approve: ").strip() != digest:
            raise ValueError("approval declined")
    with transaction_scope():
        data = _read_registry()
        if name in data["entries"]:
            raise ValueError(f"private MCP entry already registered: {name}")
        data["entries"][name] = {
            "digest": digest, "manifest_hex": raw_bytes.hex(), "server": server,
            "profile_home": str(Path(get_hermes_home()).resolve()), "artifacts": artifacts,
            "provenance": "operator-approved-custom",
        }
        _atomic_write(_registry_path(), data)
        return load_private_entry(name)


def load_private_entry(name):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("invalid private MCP name")
    item = _read_registry()["entries"].get(name)
    if not isinstance(item, dict) or item.get("provenance") != "operator-approved-custom":
        raise ValueError(f"no operator-approved private MCP entry named {name!r}")
    home = Path(get_hermes_home()).resolve()
    if item.get("profile_home") != str(home):
        raise ValueError("private MCP registry profile binding mismatch")
    try:
        raw_bytes = bytes.fromhex(item.get("manifest_hex", ""))
        digest = hashlib.sha256(raw_bytes).hexdigest()
        raw = json.loads(raw_bytes)
    except (TypeError, ValueError) as exc:
        raise ValueError("private MCP approval snapshot missing or invalid; operator reapproval required") from exc
    n, server, _ = _validate_manifest(raw)
    if digest != item.get("digest") or n != name or server != item.get("server"):
        raise ValueError("private MCP approval digest/snapshot mismatch")
    if fingerprint_artifacts(server) != item.get("artifacts"):
        raise ValueError("private MCP executable/artifact changed; operator reapproval required")
    return PrivateEntry(name, home, digest, json.dumps(server), json.dumps(raw.get("companion_skill")))


def read_private_entry(name):
    return load_private_entry(name)


class PrivateBackend:
    """Snapshots approved entries; native management alone calls install, not an operator CLI."""
    def __init__(self, names):
        from hermes_cli.mcp_catalog import list_catalog
        if not isinstance(names, list) or not names or len(names) > 16 or len(set(names)) != len(names):
            raise ValueError("private MCP operation requires distinct registered names (maximum 16)")
        self.profile_home = Path(get_hermes_home()).resolve()
        catalog = {entry.name for entry in list_catalog()}
        self.entries = {}
        for name in names:
            if name in catalog:
                raise ValueError(f"private MCP name collides with catalogue entry: {name}; migrate explicitly")
            self.entries[name] = load_private_entry(name)
            if self.entries[name].profile_home != self.profile_home:
                raise ValueError("private MCP profile changed during setup")
        self.skill_results = {}
        self.installation_observations = {}

    def _entry(self, name):
        if name not in self.entries:
            raise ValueError("private MCP name not approved for this operation")
        current, snapshot = load_private_entry(name), self.entries[name]
        if current != snapshot or current.profile_home != self.profile_home:
            raise ValueError("private MCP manifest/profile changed during operation")
        return snapshot

    def provenance(self):
        return {"source": "private", "trust": "operator-approved-custom", "profile_home": str(self.profile_home),
                "entries": {name: {"digest": entry.digest, "provenance": entry.provenance}
                            for name, entry in self.entries.items()}}

    def instructions(self, name):
        entry = self._entry(name)
        return (f"Private; operator-approved, NOT Nous-reviewed: {name} ({entry.digest}). "
                f"Profile: {entry.profile_home}. "
                "Preconfigured credentials only. Cancellation after work starts may not undo a committed install.")

    def required_env(self, name):
        from hermes_cli.private_mcp_secrets import profile_secrets
        names = [spec["name"] for spec in self._entry(name).auth_env]
        missing = sorted(set(names) - set(profile_secrets(names)))
        if missing:
            raise ValueError(f"private MCP requires preconfigured profile references: {', '.join(missing)}")
        return []

    def installs_with_oauth(self, name):
        self._entry(name)
        return False

    def install(self, name, env):
        from hermes_cli.mcp_config import _probe_single_server
        from hermes_cli.private_mcp_lifecycle import install_owned
        if env:
            raise ValueError("private installation uses only preconfigured profile credentials")
        self.required_env(name)
        entry = self._entry(name)
        cfg = entry.server
        cfg.pop("transport", None)
        cfg["type"] = "http" if "url" in cfg else "stdio"
        cfg.setdefault("enabled", True)

        def probe(probe_name, probe_cfg):
            from hermes_cli.private_mcp_secrets import install_secret_scope
            if probe_name != name or probe_cfg != cfg:
                raise ValueError("probe differs from approved configuration")
            self._entry(name)
            with install_secret_scope([spec["name"] for spec in entry.auth_env]):
                tools = [str(tool[0]) for tool in (_probe_single_server(name, cfg) or [])]
            self._entry(name)  # Do not commit approval/source drift during network discovery.
            return tools

        tools = install_owned(name, cfg, entry.digest, probe)
        self.installation_observations[name] = {"mcp_configuration": "saved-and-probed", "skill": "not-requested"}
        if entry.companion_skill is not None:
            from hermes_cli.private_mcp_skill import install_skill
            self.installation_observations[name]["skill"] = "pending-or-failed"
            self.skill_results[name] = install_skill(entry.companion_skill)
            if self.skill_results[name].get("status") not in {"installed", "owned", "preexisting"}:
                raise ValueError("companion Skill installation is uncertain; operator recovery required")
            self.installation_observations[name]["skill"] = "bundle-readback"
        return tools


def main(argv=None):
    parser = argparse.ArgumentParser(description="Operator-only private MCP registration and lifecycle")
    sub = parser.add_subparsers(dest="action", required=True)
    register = sub.add_parser("register")
    register.add_argument("manifest")
    register.add_argument("--sha256", required=True)
    register.add_argument("--approve-digest")
    for action in ("status", "disable", "detach", "restore", "release", "recover", "unregister",
                   "skill-backup", "skill-restore", "skill-recover"):
        command = sub.add_parser(action)
        command.add_argument("name")
        if action != "status":
            command.add_argument("--confirm-profile", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "register":
            entry = register_manifest(args.manifest, sha256=args.sha256, approve_digest=args.approve_digest)
            print(f"Registered {entry.name} as operator-approved custom MCP ({entry.profile_home}).")
        else:
            from hermes_cli import private_mcp_lifecycle as lifecycle
            if args.action != "status" and args.confirm_profile != str(Path(get_hermes_home()).resolve()):
                raise ValueError("confirmation does not match active profile")
            if args.action.startswith("skill-"):
                from hermes_cli.private_mcp_skill import remove_owned_skill, restore_owned_skill, recover_owned_skill
                operation = {"skill-backup": remove_owned_skill, "skill-restore": restore_owned_skill,
                             "skill-recover": recover_owned_skill}[args.action]
                result = operation(args.name)
                if isinstance(result, Path):
                    result = str(result)
            elif args.action == "unregister":
                from hermes_cli.private_mcp_operator import unregister
                result = unregister(args.name)
            else:
                result = getattr(lifecycle, args.action)(args.name)
            print(json.dumps(result))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"private MCP operation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
