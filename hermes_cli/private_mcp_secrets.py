"""Fail-closed credential provenance for private, profile-bound MCP installs."""
from contextlib import contextmanager
from pathlib import Path

from hermes_constants import get_hermes_home


def profile_secrets(names):
    from agent.secret_scope import current_secret_scope, current_secret_scope_home
    from hermes_cli.config import load_env, get_env_path
    from hermes_cli.private_mcp_validation import reject_symlinks

    home = Path(get_hermes_home()).resolve()
    reject_symlinks(get_env_path())
    values = dict(load_env())
    scope_home = current_secret_scope_home()
    if scope_home is not None and Path(scope_home).resolve() == home:
        values.update(current_secret_scope() or {})
    return {key: str(values[key]) for key in names if values.get(key)
            and not str(values[key]).startswith(("${", "op://"))}


@contextmanager
def install_secret_scope(names):
    from agent.secret_scope import (
        set_secret_scope, reset_secret_scope, set_multiplex_context, reset_multiplex_context,
    )
    values = profile_secrets(names)
    missing = sorted(set(names) - set(values))
    if missing:
        raise ValueError(f"private MCP requires preconfigured profile references: {', '.join(missing)}")
    token = set_secret_scope(values, profile_home=str(Path(get_hermes_home()).resolve()))
    multiplex = set_multiplex_context(True)
    try:
        yield
    finally:
        reset_multiplex_context(multiplex)
        reset_secret_scope(token)
