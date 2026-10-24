"""Reversible operator lifecycle for profile-owned private MCP configuration."""
from __future__ import annotations

import multiprocessing

import pytest


def _home(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def test_owned_install_disable_restore_detach_preserves_unrelated_config(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    raw = '# keep this comment\nmodel: test\nmcp_servers:\n  sibling:\n    command: sibling\n'
    (home / "config.yaml").write_text(raw)
    from hermes_cli.private_mcp_lifecycle import install_owned, disable, restore, detach

    cfg = {"type": "stdio", "command": "/bin/true", "args": [], "enabled": True}
    assert install_owned("private-one", cfg, "a" * 64, lambda *_: [("tool", "")]) == ["tool"]
    assert "# keep this comment" in (home / "config.yaml").read_text()
    disable("private-one")
    restore("private-one")
    detach("private-one")
    text = (home / "config.yaml").read_text()
    assert "sibling" in text and "private-one" not in text


def test_foreign_drift_is_never_overwritten_or_detached(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    from hermes_cli.private_mcp_lifecycle import install_owned, detach
    cfg = {"type": "stdio", "command": "/bin/true", "args": []}
    install_owned("private-one", cfg, "b" * 64, lambda *_: [])
    from hermes_cli.config import read_raw_config, get_config_path, atomic_config_replace
    current = read_raw_config()
    current["mcp_servers"]["private-one"] = {"command": "foreign"}
    atomic_config_replace(get_config_path(), current)
    with pytest.raises(ValueError, match="drift|ownership|changed"):
        detach("private-one")
    assert read_raw_config()["mcp_servers"]["private-one"]["command"] == "foreign"


def test_pending_receipt_fails_closed_on_recovery(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    from hermes_cli.private_mcp_lifecycle import install_owned, status
    def broken(*_):
        raise RuntimeError("probe failed")
    with pytest.raises(RuntimeError):
        install_owned("private-one", {"type": "stdio", "command": "/bin/true"}, "c" * 64, broken)
    assert status("private-one")["state"] == "pending"


def test_tool_names_are_not_truncated_and_retry_reprobes(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    from hermes_cli.private_mcp_lifecycle import install_owned
    cfg = {"command": "/bin/true", "enabled": True}
    assert install_owned("example", cfg, "a" * 64, lambda *_: ["read_missions"]) == ["read_missions"]
    assert install_owned("example", cfg, "a" * 64, lambda *_: ["fresh_probe"]) == ["fresh_probe"]


def test_disabled_install_retry_never_enables(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    from hermes_cli.private_mcp_lifecycle import install_owned, disable
    from hermes_cli.config import read_raw_config
    cfg = {"command": "/bin/true", "enabled": True}
    install_owned("example", cfg, "a" * 64, lambda *_: [])
    disable("example")
    with pytest.raises(ValueError):
        install_owned("example", cfg, "a" * 64, lambda *_: [])
    assert read_raw_config()["mcp_servers"]["example"]["enabled"] is False


def test_uncertain_empty_install_can_be_recovered_by_operator(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    from hermes_cli import private_mcp_lifecycle as life
    with pytest.raises(RuntimeError):
        life.install_owned("example", {"command": "/bin/true"}, "a" * 64,
                           lambda *_: (_ for _ in ()).throw(RuntimeError("test probe failure")))
    assert life.recover("example")["state"] == "absent"
    assert life.install_owned("example", {"command": "/bin/true"}, "a" * 64, lambda *_: []) == []


def test_symlinked_lifecycle_root_is_rejected_without_chmod(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    other = tmp_path / "other"
    other.mkdir(mode=0o750)
    mode = other.stat().st_mode
    (home / "private_mcp").symlink_to(other, target_is_directory=True)
    from hermes_cli.private_mcp_lifecycle import status
    with pytest.raises(ValueError, match="symlink"):
        status("example")
    assert other.stat().st_mode == mode


def _hold_lock(home, acquired, release):
    import os
    os.environ["HERMES_HOME"] = home
    from hermes_cli.private_mcp_lifecycle import transaction_scope
    with transaction_scope():
        acquired.set()
        release.wait(5)


def test_cross_process_transaction_times_out(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    ctx = multiprocessing.get_context("fork")
    acquired, release = ctx.Event(), ctx.Event()
    process = ctx.Process(target=_hold_lock, args=(str(home), acquired, release))
    process.start()
    try:
        assert acquired.wait(4)
        from hermes_cli.private_mcp_lifecycle import transaction_scope
        with pytest.raises(TimeoutError):
            with transaction_scope(timeout=0.1):
                pytest.fail("concurrent lock was not enforced")
    finally:
        release.set()
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join()
    assert process.exitcode == 0

