"""Operator-only withdrawal of a private registration, without resource deletion."""
from __future__ import annotations

from hermes_cli.private_mcp_validation import NAME


def unregister(name):
    from hermes_cli.private_mcp import _read_registry, _registry_path, _atomic_write
    from hermes_cli.private_mcp_lifecycle import transaction_scope, _paths, _read_receipts, _write_receipts, _current

    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("invalid private MCP name")
    with transaction_scope():
        registry = _read_registry()
        if name not in registry["entries"]:
            raise ValueError("private MCP registration is absent")
        if _current(name) is not None:
            raise ValueError("detach/release the owned native entry before withdrawing registration")
        path, _ = _paths()
        receipts = _read_receipts(path)
        receipt = receipts["entries"].get(name)
        if receipt and receipt.get("state") not in {"detached", "released"}:
            raise ValueError("ownership is not released; settle pending operations first")
        # Withdraw model access first. A crash before the receipt archive fails closed;
        # registration cannot overwrite the old receipt and needs operator recovery.
        del registry["entries"][name]
        _atomic_write(_registry_path(), registry)
        if receipt:
            receipts.setdefault("history", []).append({"name": name, "receipt": receipt})
            del receipts["entries"][name]
            _write_receipts(path, receipts)
        return {"state": "unregistered", "native_skills_preserved": True, "credentials_preserved": True}
