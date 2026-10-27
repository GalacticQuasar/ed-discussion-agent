"""Shared fixtures. No test talks to the real Ed API: HTTP is mocked with `responses`."""

import pytest
import responses

from ed_discussion_mcp import server

ED_ENV_VARS = (
    "ED_API_TOKEN", "ED_EMAIL", "ED_PASSWORD", "ED_REGION",
    "ED_LISTENER", "ED_LISTENER_MODEL", "ED_LISTENER_NTFY_TOPIC", "ED_LISTENER_NTFY_SERVER",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Start every test with no Ed credentials, region or listener settings, whatever
    the developer's shell has, and a listener database in a temporary directory."""
    for name in ED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ED_LISTENER_DB", str(tmp_path / "listener.db"))


@pytest.fixture(autouse=True)
def reset_server_token(monkeypatch):
    """server._call() caches the token in module globals; don't let it leak between tests."""
    monkeypatch.setattr(server, "_token", None)
    monkeypatch.setattr(server, "_refreshable", False)


@pytest.fixture
def mocked_http():
    """Intercept requests; any request that wasn't registered fails the test."""
    with responses.RequestsMock() as rsps:
        yield rsps
