"""Receipt-backed ownership of one private MCP entry; no credentials or process control.

The private lock coordinates private writers across processes. Native config_write_scope
also serializes this route with native in-process writers. Other-process native/manual
writers do not share this lock: do not claim a distributed config CAS.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

from hermes_constants import get_hermes_home
from hermes_cli.private_mcp_validation import NAME, reject_symlinks

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
_LOCAL = threading.local()


def _home():
    home = Path(get_hermes_home()).absolute()
    reject_symlinks(home)
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    return home.resolve()


def _paths():
    root = _home() / "private_mcp"
    reject_symlinks(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in ("lifecycle.json", "lifecycle.lock"):
        reject_symlinks(root / name)
    return root / "lifecycle.json", root / "lifecycle.lock"


@contextlib.contextmanager
def transaction_scope(timeout=10.0):
    """Bounded, reentrant thread/process lock. POSIX only, deliberately fail closed."""
    if os.name != "posix":
        raise RuntimeError("private MCP lifecycle currently requires POSIX fcntl")
    _, path = _paths()
    home, key = _home(), str(_home())
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.RLock())
    if not lock.acquire(timeout=timeout):
        raise TimeoutError("timed out waiting for private MCP thread lock")
    fd = None
    held = getattr(_LOCAL, "held", {})
    try:
        if key in held:
            yield home
            return
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out waiting for private MCP process lock")
                time.sleep(0.02)
        held[key] = fd
        _LOCAL.held = held
        yield home
    finally:
        if fd is not None:
            held.pop(key, None)
            os.close(fd)
        lock.release()


def _read_receipts(path):
    reject_symlinks(path)
    if not path.exists():
        return {"schema_version": 1, "profile_home": str(_home()), "entries": {}}
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("private MCP receipt exceeds size limit")
    data = json.loads(path.read_text())
    if (not isinstance(data, dict) or data.get("schema_version") != 1
            or data.get("profile_home") != str(_home()) or not isinstance(data.get("entries"), dict)):
        raise ValueError("private MCP receipt schema/profile mismatch")
    return data


def _write_receipts(path, data):
    from hermes_cli.private_mcp import _atomic_write
    _atomic_write(path, data)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _validate_name(name):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("invalid private MCP name")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _current(name):
    from hermes_cli.config import read_raw_config
    servers = read_raw_config().get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise ValueError("native mcp_servers is not a mapping")
    return servers.get(name)


def _write_server(name, value, expected):
    from hermes_cli.config import atomic_config_replace, atomic_config_write, read_raw_config, get_config_path
    from hermes_cli.web_routers._common import config_write_scope
    with config_write_scope(None):
        raw = read_raw_config()
        servers = raw.get("mcp_servers", {})
        if not isinstance(servers, dict) or servers.get(name) != expected:
            raise ValueError("native MCP ownership changed before commit")
        servers = dict(servers)
        if value is None:
            servers.pop(name, None)
        else:
            servers[name] = value
        raw["mcp_servers"] = servers
        writer = atomic_config_replace if value is None else atomic_config_write
        writer(get_config_path(), raw)
        if _current(name) != value:
            raise ValueError("native MCP readback mismatch; pending operator recovery required")


def _tools(value):
    # Native CLI probes return pairs; management backends may already return names.
    return [str(item if isinstance(item, str) else item[0]) for item in (value or [])]


def install_owned(name, cfg, manifest_digest, probe):
    _validate_name(name)
    if not isinstance(cfg, dict) or not re.fullmatch(r"[0-9a-f]{64}", manifest_digest):
        raise ValueError("invalid config or manifest digest")
    with transaction_scope():
        path, _ = _paths()
        data = _read_receipts(path)
        prior, current = data["entries"].get(name), _current(name)
        if prior:
            if (prior.get("state") != "committed" or current != cfg
                    or _digest(current) != prior.get("owned_digest")
                    or prior.get("manifest_digest") != manifest_digest):
                raise ValueError("private MCP pending/disabled/released/ownership drift; operator action required")
            tools = _tools(probe(name, dict(cfg)))
            if _current(name) != current:
                raise ValueError("native MCP ownership changed during repeat probe")
            return tools
        if current is not None:
            raise ValueError("MCP name collision; refusing foreign overwrite")
        receipt = {"state": "pending", "manifest_digest": manifest_digest, "before": None,
                   "planned": json.loads(json.dumps(cfg)), "target": cfg, "next_state": "committed"}
        data["entries"][name] = receipt
        _write_receipts(path, data)
        tools = _tools(probe(name, dict(cfg)))
        _write_server(name, cfg, None)
        receipt.update(state="committed", owned_digest=_digest(cfg), tools=tools)
        _write_receipts(path, data)
        return tools


def status(name=None):
    with transaction_scope():
        entries = _read_receipts(_paths()[0])["entries"]
        if name is None:
            return {key: {"state": value["state"]} for key, value in entries.items()}
        _validate_name(name)
        item = entries.get(name)
        return {"state": item["state"] if item else "absent"}


def _mutate(name, action):
    _validate_name(name)
    with transaction_scope():
        path, _ = _paths()
        data = _read_receipts(path)
        receipt = data["entries"].get(name)
        if not receipt or receipt.get("state") not in {"committed", "disabled", "detached"}:
            raise ValueError("no settled owned MCP receipt; operator recovery required")
        current = _current(name)
        expected = receipt.get("disabled_digest") if receipt["state"] == "disabled" else receipt.get("owned_digest")
        if receipt["state"] == "detached":
            if action != "restore" or current is not None:
                raise ValueError("detached ownership permits only explicit restore into an absent name")
        elif current is None or _digest(current) != expected:
            raise ValueError("private MCP ownership drift; refusing mutation")
        if action == "disable":
            target, state = {**current, "enabled": False}, "disabled"
        elif action == "restore":
            target, state = receipt["planned"], "committed"
        elif action in {"detach", "release"}:
            target, state = None, "detached" if action == "detach" else "released"
        else:
            raise ValueError("unknown lifecycle action")
        before_state = receipt["state"]
        receipt.update(state="pending", before=current, target=target, next_state=state, before_state=before_state)
        _write_receipts(path, data)
        _write_server(name, target, current)
        receipt["state"] = state
        if state == "disabled":
            receipt["disabled_digest"] = _digest(target)
        elif state == "committed":
            receipt["owned_digest"] = _digest(target)
        _write_receipts(path, data)
        return {"state": state, "runtime_refresh_required": True, "skill_preserved": True}


def recover(name):
    """Operator-only reconcile of exact before/target states; never overwrite native drift."""
    _validate_name(name)
    with transaction_scope():
        path, _ = _paths()
        data = _read_receipts(path)
        receipt = data["entries"].get(name)
        if not receipt or receipt.get("state") != "pending":
            raise ValueError("no pending private MCP receipt")
        current = _current(name)
        if current == receipt.get("target"):
            receipt["state"] = receipt["next_state"]
            key = "disabled_digest" if receipt["state"] == "disabled" else "owned_digest"
            receipt[key] = _digest(current)
        elif current == receipt.get("before"):
            if "before_state" in receipt:
                receipt["state"] = receipt["before_state"]
            else:
                del data["entries"][name]
        else:
            raise ValueError("native ownership drift; manual investigation required, no recovery write")
        _write_receipts(path, data)
        return {"state": data["entries"].get(name, {}).get("state", "absent")}


def disable(name):
    return _mutate(name, "disable")


def detach(name):
    return _mutate(name, "detach")


def restore(name):
    return _mutate(name, "restore")


def release(name):
    return _mutate(name, "release")
