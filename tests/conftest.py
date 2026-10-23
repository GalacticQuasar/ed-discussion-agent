"""Shared fixtures. No test talks to the real Ed API: HTTP is mocked with `responses`."""

import pytest
import responses

from ed_discussion_mcp import server

ED_ENV_VARS = ("ED_API_TOKEN", "ED_EMAIL", "ED_PASSWORD", "ED_REGION")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start every test with no Ed credentials or region, whatever the developer's shell has."""
    for name in ED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


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
