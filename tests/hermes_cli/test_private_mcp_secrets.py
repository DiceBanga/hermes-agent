"""Private install credentials must not come from the container's ambient env."""
from pathlib import Path

import pytest


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def test_ambient_only_secret_is_not_profile_authority(profile, monkeypatch):
    from hermes_cli.private_mcp_secrets import profile_secrets
    monkeypatch.setenv("PRIVATE_TOKEN", "ambient-test-value")
    assert profile_secrets(["PRIVATE_TOKEN"]) == {}


def test_native_profile_dotenv_is_accepted_without_value_disclosure(profile):
    from hermes_cli.private_mcp_secrets import profile_secrets
    (profile / ".env").write_text("PRIVATE_TOKEN=profile-test-value\n")
    (profile / ".env").chmod(0o600)
    assert profile_secrets(["PRIVATE_TOKEN"])["PRIVATE_TOKEN"] == "profile-test-value"


def test_foreign_scope_does_not_supply_this_profiles_secret(profile):
    from agent.secret_scope import set_secret_scope, reset_secret_scope
    from hermes_cli.private_mcp_secrets import profile_secrets
    token = set_secret_scope({"PRIVATE_TOKEN": "foreign-test-value"}, profile_home=str(profile / "other"))
    try:
        assert profile_secrets(["PRIVATE_TOKEN"]) == {}
    finally:
        reset_secret_scope(token)


def test_bound_scoped_secret_is_accepted(profile):
    from agent.secret_scope import set_secret_scope, reset_secret_scope
    from hermes_cli.private_mcp_secrets import profile_secrets
    token = set_secret_scope({"PRIVATE_TOKEN": "bound-test-value"}, profile_home=str(profile))
    try:
        assert profile_secrets(["PRIVATE_TOKEN"])["PRIVATE_TOKEN"] == "bound-test-value"
    finally:
        reset_secret_scope(token)
