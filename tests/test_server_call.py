import pytest
import requests
from mcp.server.mcpserver.exceptions import ToolError

from ed_discussion_mcp import ed_api, server


def bad_token():
    return ed_api.EdAPIError("GET x failed: 400 - bad token", 400, "bad_token")


def http_error(status):
    return ed_api.EdAPIError(f"GET x failed: {status} - nope", status)


class FakeAuth:
    """Stands in for ed_api.get_token / get_auth_token and counts calls."""

    def __init__(self, monkeypatch, token="first", refreshable=True, relogin_tokens=("second",)):
        self.get_token_calls = 0
        self.relogin_calls = 0
        self._token = token
        self._refreshable = refreshable
        self._relogin_tokens = iter(relogin_tokens)
        monkeypatch.setattr(ed_api, "get_token", self.get_token)
        monkeypatch.setattr(ed_api, "get_auth_token", self.get_auth_token)

    def get_token(self):
        self.get_token_calls += 1
        return self._token, self._refreshable

    def get_auth_token(self):
        self.relogin_calls += 1
        token = next(self._relogin_tokens)
        if isinstance(token, Exception):
            raise token
        return token


def failing_with(*errors, result="ok"):
    """An fn for _call() that raises each error in turn, then returns result. Records tokens it saw."""
    pending = list(errors)

    def fn(token):
        fn.tokens.append(token)
        if pending:
            raise pending.pop(0)
        return result

    fn.tokens = []
    return fn


# --- Token handling and re-login ---

def test_token_fetched_once_and_cached(monkeypatch):
    auth = FakeAuth(monkeypatch)
    fn = failing_with()
    assert server._call(fn) == "ok"
    assert server._call(fn) == "ok"
    assert auth.get_token_calls == 1
    assert fn.tokens == ["first", "first"]


@pytest.mark.parametrize("error", [bad_token(), http_error(401)], ids=["400-bad_token", "401"])
def test_refreshable_token_relogs_in_on_auth_error(monkeypatch, error):
    auth = FakeAuth(monkeypatch, refreshable=True)
    fn = failing_with(error)

    assert server._call(fn) == "ok"
    assert auth.relogin_calls == 1
    assert fn.tokens == ["first", "second"]
    # The new token is cached for later calls.
    assert server._call(fn) == "ok"
    assert fn.tokens[-1] == "second"
    assert auth.get_token_calls == 1


def test_static_token_is_not_refreshed(monkeypatch):
    auth = FakeAuth(monkeypatch, refreshable=False)
    with pytest.raises(ToolError, match="Ed rejected the credentials"):
        server._call(failing_with(bad_token()))
    assert auth.relogin_calls == 0


def test_non_auth_error_is_not_retried(monkeypatch):
    auth = FakeAuth(monkeypatch, refreshable=True)
    fn = failing_with(http_error(404))
    with pytest.raises(ToolError, match="not found"):
        server._call(fn)
    assert auth.relogin_calls == 0
    assert fn.tokens == ["first"]


def test_retries_only_once(monkeypatch):
    auth = FakeAuth(monkeypatch, refreshable=True)
    fn = failing_with(bad_token(), bad_token())
    with pytest.raises(ToolError, match="Ed rejected the credentials"):
        server._call(fn)
    assert auth.relogin_calls == 1
    assert fn.tokens == ["first", "second"]


def test_failed_relogin_becomes_tool_error(monkeypatch):
    login_error = ed_api.EdAPIError("Login failed: 400 - bad login", 400, "bad_login")
    FakeAuth(monkeypatch, refreshable=True, relogin_tokens=[login_error])
    with pytest.raises(ToolError, match="Login failed") as excinfo:
        server._call(failing_with(bad_token()))
    assert excinfo.value.__cause__ is login_error


# --- Conversion of errors into ToolError ---

@pytest.mark.parametrize("error", [
    ed_api.MissingCredentialsError("No Ed credentials found."),
    ed_api.InvalidRegionError("ED_REGION must be a region code"),
])
def test_config_errors_become_tool_errors(monkeypatch, error):
    def get_token():
        raise error

    monkeypatch.setattr(ed_api, "get_token", get_token)
    with pytest.raises(ToolError) as excinfo:
        server._call(failing_with())
    assert str(excinfo.value) == str(error)
    assert excinfo.value.__cause__ is error


def test_connection_error_becomes_tool_error(monkeypatch):
    FakeAuth(monkeypatch)
    error = requests.ConnectionError("Name or service not known")
    with pytest.raises(ToolError, match="Could not reach Ed.*ED_REGION") as excinfo:
        server._call(failing_with(error))
    assert excinfo.value.__cause__ is error


@pytest.mark.parametrize("status, hint", [
    (302, "redirected the request instead of answering; check ED_REGION"),
    (403, "no access: check the course_id with list_courses"),
    (404, "not found: check the id or number"),
    (500, None),
])
def test_ed_api_error_hints(monkeypatch, status, hint):
    FakeAuth(monkeypatch, refreshable=False)
    error = http_error(status)
    with pytest.raises(ToolError) as excinfo:
        server._call(failing_with(error))
    message = str(excinfo.value)
    assert message.startswith(str(error))
    if hint:
        assert hint in message
    else:
        assert message == str(error)
    assert excinfo.value.__cause__ is error


def test_auth_error_hint(monkeypatch):
    FakeAuth(monkeypatch, refreshable=False)
    with pytest.raises(ToolError) as excinfo:
        server._call(failing_with(bad_token()))
    assert str(excinfo.value) == str(bad_token()) + server._AUTH_HINT


def test_other_exceptions_pass_through(monkeypatch):
    FakeAuth(monkeypatch)
    with pytest.raises(KeyError):
        server._call(failing_with(KeyError("threads")))


# --- End to end through the HTTP layer ---

def test_get_sends_static_token_to_region_api(monkeypatch, mocked_http):
    monkeypatch.setenv("ED_API_TOKEN", "static-token")
    mocked_http.get("https://us.edstem.org/api/courses/1", json={"course": {"id": 1}})

    assert server._get("/courses/1") == {"course": {"id": 1}}
    assert mocked_http.calls[0].request.headers["Authorization"] == "Bearer static-token"


def test_get_with_rejected_static_token_does_not_log_in(monkeypatch, mocked_http):
    monkeypatch.setenv("ED_API_TOKEN", "static-token")
    monkeypatch.setenv("ED_EMAIL", "me@example.com")
    monkeypatch.setenv("ED_PASSWORD", "hunter2")
    mocked_http.get("https://us.edstem.org/api/courses/1", status=400, json={"code": "bad_token"})

    with pytest.raises(ToolError, match="Ed rejected the credentials"):
        server._get("/courses/1")
    # Only the GET; no POST /api/token.
    assert [call.request.method for call in mocked_http.calls] == ["GET"]


def test_get_relogs_in_with_email_password(monkeypatch, mocked_http):
    monkeypatch.setenv("ED_EMAIL", "me@example.com")
    monkeypatch.setenv("ED_PASSWORD", "hunter2")
    mocked_http.post("https://us.edstem.org/api/token", json={"token": "old"})
    mocked_http.post("https://us.edstem.org/api/token", json={"token": "new"})
    mocked_http.get("https://us.edstem.org/api/courses/1", status=400, json={"code": "bad_token"})
    mocked_http.get("https://us.edstem.org/api/courses/1", json={"course": {"id": 1}})

    assert server._get("/courses/1") == {"course": {"id": 1}}
    gets = [call.request for call in mocked_http.calls if call.request.method == "GET"]
    assert [r.headers["Authorization"] for r in gets] == ["Bearer old", "Bearer new"]
