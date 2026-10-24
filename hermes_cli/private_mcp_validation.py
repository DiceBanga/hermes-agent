"""Strict transport validation and local artifact fingerprints for private approval."""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}\Z")


def validate_server(server):
    if not isinstance(server, dict) or set(server) - {
        "command", "args", "env", "url", "headers", "transport", "enabled"
    }:
        raise ValueError("server must contain only supported MCP configuration fields")
    if server.get("enabled", True) is not True:
        raise ValueError("install requires enabled=true; disable through operator lifecycle afterwards")
    transport = server.get("transport", "stdio")
    if transport not in {"stdio", "http"}:
        raise ValueError("transport must be stdio or http")
    if transport == "stdio":
        command = server.get("command")
        if ("url" in server or "headers" in server or not isinstance(command, str)
                or not Path(command).is_absolute() or not Path(command).is_file()):
            raise ValueError("stdio requires one absolute executable and no HTTP fields")
        if not os.access(command, os.X_OK):
            raise ValueError("stdio command is not executable")
    else:
        if any(key in server for key in ("command", "args", "env")):
            raise ValueError("HTTP transport cannot contain stdio fields")
        url = server.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ValueError("custom HTTP MCP endpoints require an explicit https:// URL")
        try:
            parsed = urlsplit(url)
            valid = (parsed.hostname and parsed.scheme == "https" and not parsed.username
                     and not parsed.password and not parsed.fragment and not parsed.query)
            parsed.port
        except ValueError as exc:
            raise ValueError("invalid HTTP endpoint") from exc
        if not valid or any(ord(char) < 33 for char in url):
            raise ValueError("HTTP endpoint must not embed credentials, query, fragment or controls")
    args = server.get("args", [])
    if (not isinstance(args, list) or len(args) > 128
            or not all(isinstance(arg, str) and "\x00" not in arg and len(arg) <= 8192 for arg in args)):
        raise ValueError("args must be a bounded list of NUL-free strings")
    refs = set()
    for kind in ("env", "headers"):
        mapping = server.get(kind, {})
        if not isinstance(mapping, dict) or len(mapping) > 64:
            raise ValueError(f"{kind} must be a bounded mapping")
        for key, value in mapping.items():
            key_ok = ENV_NAME.fullmatch(key) if kind == "env" and isinstance(key, str) else (
                isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9-]{1,128}", key))
            match = ENV_REF.fullmatch(value) if isinstance(value, str) else None
            if not key_ok or match is None:
                raise ValueError(f"{kind} entries must be named ${{ENV_VAR}} references; literals forbidden")
            refs.add(match.group(1))
    return tuple({"name": key, "prompt": key, "required": True, "secret": True, "default": ""}
                 for key in sorted(refs))


def fingerprint_artifacts(server):
    """Pin executable and existing absolute file arguments, not their dependency closure.

    Operator review must still establish package/dependency provenance. Hashing the
    interpreter alone is not proof that a module loaded with -m is immutable.
    """
    paths = [server["command"]] if "command" in server else []
    paths += [arg for arg in server.get("args", []) if Path(arg).is_absolute() and Path(arg).is_file()]
    result = {}
    for value in sorted(set(paths)):
        path = Path(value)
        if path.stat().st_size > 256 * 1024 * 1024:
            raise ValueError("executable/artifact exceeds fingerprint limit")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        result[value] = {"resolved": str(path.resolve()), "sha256": digest}
    return result


def reject_symlinks(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("symlink in trusted private MCP state path")
    if path.exists() and hasattr(os, "getuid"):
        mode = path.stat()
        if mode.st_uid != os.getuid() or mode.st_mode & 0o022:
            raise ValueError("trusted private MCP state must be owned and not group/world writable")
