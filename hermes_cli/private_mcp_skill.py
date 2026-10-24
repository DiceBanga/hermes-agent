"""Install operator-pinned companion skill bundles into the active Hermes profile."""
from __future__ import annotations


import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from hermes_constants import get_hermes_home

_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?\Z")
_MAX_FILES = 512
_MAX_BYTES = 16 * 1024 * 1024
_MAX_PATH = 1024


def _home() -> Path:
    from hermes_cli.private_mcp_validation import reject_symlinks
    home = Path(get_hermes_home()).absolute()
    reject_symlinks(home)
    return home


def _journal() -> Path:
    from hermes_cli.private_mcp_validation import reject_symlinks
    path = _home() / "private_mcp" / "skill_ownership.json"
    reject_symlinks(path)
    return path


def _read_journal() -> dict[str, Any]:
    path = _journal()
    if not path.exists():
        return {"version": 1, "profile_home": str(_home()), "skills": {}}
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("skill ownership journal exceeds size limit")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("skill ownership journal is unreadable; refusing changes") from exc
    if (not isinstance(data, dict) or data.get("version") != 1
            or data.get("profile_home") != str(_home()) or not isinstance(data.get("skills"), dict)):
        raise ValueError("skill ownership journal is invalid; refusing changes")
    return data


def _write_journal(data: dict[str, Any]) -> None:
    path = _journal()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".skill-journal-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _walk(root: Path) -> list[tuple[str, Path, bool]]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("skill source must be a real directory, not a symlink")
    base = root.absolute()
    result: list[tuple[str, Path, bool]] = []
    total = 0
    for current, dirs, files in os.walk(base, followlinks=False):
        dirs.sort()
        files.sort()
        current_path = Path(current)
        for name in list(dirs):
            path = current_path / name
            rel = path.relative_to(base).as_posix()
            if path.is_symlink():
                raise ValueError("skill tree contains a symlink")
            if len(rel.encode()) > _MAX_PATH:
                raise ValueError("skill path exceeds limit")
            result.append((rel, path, True))
            if len(result) > _MAX_FILES * 2:
                raise ValueError("skill tree exceeds entry-count limit")
        for name in files:
            path = current_path / name
            rel = path.relative_to(base).as_posix()
            if path.is_symlink():
                raise ValueError("skill tree contains a symlink")
            if not path.is_file():
                raise ValueError("skill tree contains a non-regular file")
            if len(rel.encode()) > _MAX_PATH:
                raise ValueError("skill path exceeds limit")
            total += path.stat().st_size
            result.append((rel, path, False))
            if total > _MAX_BYTES:
                raise ValueError("skill tree exceeds byte limit")
            if sum(not is_dir for _, _, is_dir in result) > _MAX_FILES:
                raise ValueError("skill tree exceeds file-count limit")
    return sorted(result, key=lambda item: item[0])


def tree_payload(root: Path) -> bytes:
    """Canonical SHA-256 payload: sorted entries framed by type/path/length/content."""
    chunks = []
    for rel, path, is_dir in _walk(Path(root)):
        name = rel.encode("utf-8")
        marker = b"D" if is_dir else b"F"
        body = b"" if is_dir else path.read_bytes()
        chunks.append(marker + len(name).to_bytes(4, "big") + name + len(body).to_bytes(8, "big") + body)
    return b"".join(chunks)


def tree_digest(root: Path) -> str:
    return hashlib.sha256(tree_payload(root)).hexdigest()


def validate_skill_spec(spec: Any) -> dict[str, str]:
    if not isinstance(spec, dict) or set(spec) != {"name", "source", "sha256"}:
        raise ValueError("companion_skill must contain exactly name, source, sha256")
    name, source, digest = spec["name"], spec["source"], spec["sha256"]
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("invalid companion skill slug")
    if not isinstance(source, str) or not Path(source).is_absolute():
        raise ValueError("skill source must be an absolute staged directory path")
    path = Path(source)
    # Reject symlinked ancestors as well as a symlink at the leaf.
    cursor = path
    while cursor != cursor.parent:
        if cursor.is_symlink():
            raise ValueError("skill source path has a symlinked parent")
        cursor = cursor.parent
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("sha256 must be 64 lowercase hex characters")
    actual = tree_digest(path)
    if actual != digest:
        raise ValueError("skill source digest mismatch")
    try:
        frontmatter = (path / "SKILL.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError("skill requires UTF-8 SKILL.md frontmatter") from exc
    match = re.match(r"\A---\s*\n(.*?)\n---(?:\s*\n|\Z)", frontmatter, re.DOTALL)
    names = re.findall(r"(?m)^name:\s*['\"]?([^\s'\"]+)['\"]?\s*$", match.group(1)) if match else []
    if names != [name]:
        raise ValueError("skill frontmatter name must exactly match companion slug")
    return {"name": name, "source": str(path), "sha256": digest}


def _lock():
    from hermes_cli.private_mcp_lifecycle import transaction_scope
    return transaction_scope()


def _same_tree(path: Path, digest: str) -> bool:
    try:
        from hermes_cli.private_mcp_validation import reject_symlinks
        reject_symlinks(path)
        return tree_digest(path) == digest
    except (OSError, ValueError):
        return False


def install_skill(spec: Any) -> dict[str, Any]:
    clean = validate_skill_spec(spec)
    with _lock():
        journal = _read_journal()
        if clean["name"] in journal["skills"]:
            owned = journal["skills"][clean["name"]]
            if owned.get("state") != "owned":
                return {"status": "pending", "name": clean["name"], "reason": "ownership transition is uncertain"}
            target = _home() / "skills" / clean["name"]
            if owned.get("sha256") == clean["sha256"] and _same_tree(target, clean["sha256"]):
                return {"status": "owned", "name": clean["name"], "sha256": clean["sha256"]}
            raise ValueError("owned companion skill changed; refusing overwrite")
        target = _home() / "skills" / clean["name"]
        from hermes_cli.private_mcp_validation import reject_symlinks
        reject_symlinks(target)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            if target.is_dir() and _same_tree(target, clean["sha256"]):
                return {"status": "preexisting", "name": clean["name"], "sha256": clean["sha256"], "owned": False}
            raise ValueError("destination exists and is not the exact pinned skill")
        pending = {"sha256": clean["sha256"], "state": "pending"}
        journal["skills"][clean["name"]] = pending
        _write_journal(journal)
        stage = Path(tempfile.mkdtemp(prefix=f".{clean['name']}-", dir=target.parent))
        try:
            for rel, src, is_dir in _walk(Path(clean["source"])):
                out = stage / PurePosixPath(rel)
                if is_dir:
                    out.mkdir(parents=True, exist_ok=True)
                else:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, out)
            if tree_digest(stage) != clean["sha256"] or tree_digest(Path(clean["source"])) != clean["sha256"]:
                raise ValueError("skill source or staged digest changed during installation")
            os.rename(stage, target)
            journal["skills"][clean["name"]] = {"sha256": clean["sha256"], "state": "owned"}
            _write_journal(journal)
        except BaseException:
            # Keep the pending journal record: a subsequent status fails closed.
            shutil.rmtree(stage, ignore_errors=True)
            raise
        return {"status": "installed", "name": clean["name"], "sha256": clean["sha256"]}


def skill_status(spec: Any) -> dict[str, Any]:
    clean = validate_skill_spec(spec)
    journal = _read_journal()
    target = _home() / "skills" / clean["name"]
    record = journal["skills"].get(clean["name"])
    if record:
        if record.get("state") != "owned":
            return {"status": "pending", "name": clean["name"]}
        if record.get("sha256") != clean["sha256"] or not _same_tree(target, clean["sha256"]):
            return {"status": "drifted", "name": clean["name"]}
        return {"status": "owned", "name": clean["name"], "sha256": clean["sha256"]}
    if target.exists() or target.is_symlink():
        return {"status": "preexisting" if target.is_dir() and _same_tree(target, clean["sha256"]) else "conflict", "name": clean["name"], "owned": False}
    return {"status": "absent", "name": clean["name"]}


def remove_owned_skill(name: str) -> Path:
    """Move an exact owned skill to private_mcp/skill_backups; never delete it."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("invalid companion skill slug")
    with _lock():
        journal = _read_journal()
        record = journal["skills"].get(name)
        target = _home() / "skills" / name
        if not record or record.get("state") != "owned" or not _same_tree(target, record.get("sha256", "")):
            raise ValueError("skill is not an exact owned bundle")
        backups = _home() / "private_mcp" / "skill_backups"
        from hermes_cli.private_mcp_validation import reject_symlinks
        reject_symlinks(backups)
        backups.mkdir(mode=0o700, parents=True, exist_ok=True)
        backup = backups / f"{name}-{record['sha256']}"
        if backup.exists():
            raise ValueError("skill backup already exists")
        reject_symlinks(backup)
        record.update(state="backup-pending", backup=str(backup))
        _write_journal(journal)
        os.rename(target, backup)
        record["state"] = "backed-up"
        _write_journal(journal)
        return backup


def restore_owned_skill(name: str) -> dict[str, Any]:
    """Restore the most recent exact backup only when the native destination is absent."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("invalid companion skill slug")
    with _lock():
        journal = _read_journal()
        record = journal["skills"].get(name)
        if not record or record.get("state") != "backed-up":
            raise ValueError("skill has no settled owned backup record")
        backups = _home() / "private_mcp" / "skill_backups"
        from hermes_cli.private_mcp_validation import reject_symlinks
        reject_symlinks(backups)
        candidates = sorted(backups.glob(f"{name}-[0-9a-f]" + "*")) if backups.is_dir() else []
        candidates = [p for p in candidates if re.fullmatch(rf"{re.escape(name)}-[0-9a-f]{{64}}", p.name)]
        if len(candidates) != 1:
            raise ValueError("expected exactly one skill backup")
        backup = candidates[0]
        digest = backup.name.rsplit("-", 1)[1]
        if record.get("sha256") != digest or record.get("backup") != str(backup):
            raise ValueError("backup ownership receipt does not match")
        if not _same_tree(backup, digest):
            raise ValueError("skill backup digest mismatch")
        target = _home() / "skills" / name
        reject_symlinks(target)
        if target.exists() or target.is_symlink():
            raise ValueError("native skill destination is occupied")
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        record["state"] = "restore-pending"
        _write_journal(journal)
        os.rename(backup, target)
        journal["skills"][name] = {"sha256": digest, "state": "owned"}
        _write_journal(journal)
        return {"status": "owned", "name": name, "sha256": digest}


def recover_owned_skill(name: str) -> dict[str, Any]:
    """Operator-only recovery of exact journaled before/after trees, never foreign data."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("invalid companion skill slug")
    with _lock():
        journal = _read_journal()
        record = journal["skills"].get(name)
        if not record or record.get("state") not in {"pending", "backup-pending", "restore-pending"}:
            raise ValueError("no uncertain companion skill transition")
        from hermes_cli.private_mcp_validation import reject_symlinks
        target = _home() / "skills" / name
        reject_symlinks(target)
        digest = record["sha256"]
        target_absent = not target.exists()
        if record["state"] == "pending":
            if _same_tree(target, digest):
                record["state"] = "owned"
            elif target_absent:
                del journal["skills"][name]
            else:
                raise ValueError("skill ownership drift; refusing recovery")
        else:
            backup = _home() / "private_mcp" / "skill_backups" / f"{name}-{digest}"
            reject_symlinks(backup)
            if record.get("backup") != str(backup):
                raise ValueError("backup receipt mismatch")
            if target_absent and _same_tree(backup, digest):
                record["state"] = "backed-up"
            elif not backup.exists() and _same_tree(target, digest):
                record["state"] = "owned"
            else:
                raise ValueError("backup/native skill drift; refusing recovery")
        _write_journal(journal)
        return {"status": journal["skills"].get(name, {}).get("state", "absent"), "name": name}
