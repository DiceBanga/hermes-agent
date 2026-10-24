"""Contracts for operator-pinned native companion skill bundles."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from hermes_cli import private_mcp_skill as skills


@pytest.fixture(autouse=True)
def isolated_private_profile(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))


def make_bundle(root: Path) -> Path:
    bundle = root / "source"
    (bundle / "references").mkdir(parents=True)
    (bundle / "SKILL.md").write_text("---\nname: mission-control\n---\nUse MCP.\n")
    (bundle / "references" / "read-first.md").write_text("Read this first.\n")
    return bundle


def spec_for(bundle: Path, name: str = "mission-control") -> dict:
    return {"name": name, "source": str(bundle), "sha256": skills.tree_digest(bundle)}


def test_install_preserves_native_bundle_and_reports_owned(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    source = make_bundle(tmp_path)
    spec = spec_for(source)
    assert skills.validate_skill_spec(spec) == spec
    receipt = skills.install_skill(spec)
    installed = home / "skills" / "mission-control"
    assert receipt["status"] == "installed"
    assert (installed / "SKILL.md").read_bytes() == (source / "SKILL.md").read_bytes()
    assert (installed / "references" / "read-first.md").read_bytes() == (source / "references" / "read-first.md").read_bytes()
    assert skills.skill_status(spec)["status"] == "owned"


def test_same_bytes_existing_skill_is_not_claimed(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    source = make_bundle(tmp_path)
    dest = home / "skills" / "mission-control"
    dest.mkdir(mode=0o700, parents=True)
    for p in source.rglob("*"):
        if p.is_file():
            out = dest / p.relative_to(source)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(p.read_bytes())
    assert skills.install_skill(spec_for(source))["status"] == "preexisting"
    assert skills.skill_status(spec_for(source))["status"] == "preexisting"


def test_drift_and_symlinks_fail_closed(tmp_path):
    source = make_bundle(tmp_path)
    spec = spec_for(source)
    (source / "SKILL.md").write_text("changed")
    with pytest.raises(ValueError, match="digest"):
        skills.validate_skill_spec(spec)


def test_rejects_unknown_fields_and_symlink_files(tmp_path):
    source = make_bundle(tmp_path)
    with pytest.raises(ValueError):
        skills.validate_skill_spec({**spec_for(source), "extra": True})
    target = tmp_path / "outside"
    target.write_text("unsafe")
    (source / "link").symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        skills.tree_digest(source)


def test_remove_owned_moves_exact_bundle_to_backup(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    source = make_bundle(tmp_path)
    skills.install_skill(spec_for(source))
    backup = skills.remove_owned_skill("mission-control")
    assert backup.is_dir()
    assert not (tmp_path / "profile" / "skills" / "mission-control").exists()


def test_digest_is_deterministic(tmp_path):
    source = make_bundle(tmp_path)
    first = skills.tree_digest(source)
    assert first == hashlib.sha256(skills.tree_payload(source)).hexdigest()
    assert first == skills.tree_digest(source)


def test_frontmatter_name_must_match_slug(tmp_path):
    source = make_bundle(tmp_path)
    (source / "SKILL.md").write_text("---\nname: another-skill\n---\nBody\n")
    with pytest.raises(ValueError, match="frontmatter"):
        skills.validate_skill_spec(spec_for(source))


def test_reject_fifo_and_symlinked_source_parent(tmp_path):
    import stat
    source = make_bundle(tmp_path)
    os.mkfifo(source / "pipe")
    with pytest.raises(ValueError, match="non-regular"):
        skills.tree_digest(source)
    os.unlink(source / "pipe")
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    spec = spec_for(alias / "source")
    with pytest.raises(ValueError, match="symlinked parent"):
        skills.validate_skill_spec(spec)


def test_restore_backup_reclaims_owned_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    source = make_bundle(tmp_path)
    spec = spec_for(source)
    skills.install_skill(spec)
    skills.remove_owned_skill("mission-control")
    restored = skills.restore_owned_skill("mission-control")
    assert restored["status"] == "owned"
    assert skills.skill_status(spec)["status"] == "owned"
