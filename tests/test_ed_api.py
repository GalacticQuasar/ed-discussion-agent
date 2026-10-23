import json

import pytest

from ed_discussion_mcp import ed_api

US_API = "https://us.edstem.org/api"


# --- get_token(): credential precedence ---

def test_api_token_is_used_without_login(monkeypatch, mocked_http):
    monkeypatch.setenv("ED_API_TOKEN", "static-token")
    assert ed_api.get_token() == ("static-token", False)
    assert len(mocked_http.calls) == 0


def test_api_token_wins_over_email_password(monkeypatch, mocked_http):
    monkeypatch.setenv("ED_API_TOKEN", "static-token")
    monkeypatch.setenv("ED_EMAIL", "me@example.com")
    monkeypatch.setenv("ED_PASSWORD", "hunter2")
    assert ed_api.get_token() == ("static-token", False)
    assert len(mocked_http.calls) == 0


def test_email_password_logs_in(monkeypatch, mocked_http):
    monkeypatch.setenv("ED_EMAIL", "me@example.com")
    monkeypatch.setenv("ED_PASSWORD", "hunter2")
    mocked_http.post(f"{US_API}/token", json={"token": "login-token"})

    assert ed_api.get_token() == ("login-token", True)
    assert json.loads(mocked_http.calls[0].request.body) == {"login": "me@example.com", "password": "hunter2"}


def test_login_uses_region_host(monkeypatch, mocked_http):
    monkeypatch.setenv("ED_REGION", "au")
    monkeypatch.setenv("ED_EMAIL", "me@example.com")
    monkeypatch.setenv("ED_PASSWORD", "hunter2")
    mocked_http.post("https://edstem.org/api/token", json={"token": "login-token"})

    assert ed_api.get_token() == ("login-token", True)


def test_no_credentials_raises_with_settings_url(monkeypatch):
    monkeypatch.setenv("ED_REGION", "au")
    with pytest.raises(ed_api.MissingCredentialsError, match="https://edstem.org/au/settings/api-tokens"):
        ed_api.get_token()


def test_email_without_password_raises(monkeypatch, mocked_http):
    monkeypatch.setenv("ED_EMAIL", "me@example.com")
    with pytest.raises(ed_api.MissingCredentialsError):
        ed_api.get_token()
    assert len(mocked_http.calls) == 0


def test_failed_login_raises_ed_api_error(monkeypatch, mocked_http):
    monkeypatch.setenv("ED_EMAIL", "me@example.com")
    monkeypatch.setenv("ED_PASSWORD", "wrong")
    mocked_http.post(f"{US_API}/token", status=400, json={"code": "bad_login", "message": "Invalid login"})

    with pytest.raises(ed_api.EdAPIError) as excinfo:
        ed_api.get_token()
    assert excinfo.value.status == 400
    assert excinfo.value.code == "bad_login"
    assert not excinfo.value.is_auth_error


# --- Regions and URLs ---

def test_default_region_is_us():
    assert ed_api.region() == "us"
    assert ed_api.api_base() == "https://us.edstem.org/api"
    assert ed_api.stream_url() == "wss://us.edstem.org/api/stream"
    assert ed_api.web_base() == "https://edstem.org/us"


def test_au_api_is_on_bare_domain(monkeypatch):
    monkeypatch.setenv("ED_REGION", "au")
    assert ed_api.api_base() == "https://edstem.org/api"
    assert ed_api.stream_url() == "wss://edstem.org/api/stream"
    # Web links keep the region path like every other region.
    assert ed_api.web_base() == "https://edstem.org/au"
    assert ed_api.thread_url(1, 2) == "https://edstem.org/au/courses/1/discussion/2"


@pytest.mark.parametrize("code", ["ca", "sg", "eu"])
def test_other_regions_use_subdomain(monkeypatch, code):
    monkeypatch.setenv("ED_REGION", code)
    assert ed_api.api_base() == f"https://{code}.edstem.org/api"
    assert ed_api.stream_url() == f"wss://{code}.edstem.org/api/stream"
    assert ed_api.web_base() == f"https://edstem.org/{code}"


def test_region_is_normalized(monkeypatch):
    monkeypatch.setenv("ED_REGION", " US ")
    assert ed_api.region() == "us"


def test_empty_region_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("ED_REGION", "")
    assert ed_api.region() == "us"


@pytest.mark.parametrize("value", ["x", "us1", "u-s", "usaa", "us.evil.com", "evil.com/"])
def test_invalid_region_raises(monkeypatch, value):
    monkeypatch.setenv("ED_REGION", value)
    with pytest.raises(ed_api.InvalidRegionError):
        ed_api.region()
    with pytest.raises(ed_api.InvalidRegionError):
        ed_api.api_base()


def test_link_helpers():
    assert ed_api.thread_url(92707, 123) == "https://edstem.org/us/courses/92707/discussion/123"
    assert ed_api.token_settings_url() == "https://edstem.org/us/settings/api-tokens"


# --- EdAPIError / is_auth_error ---

def _error_from(mocked_http, status, **body):
    mocked_http.get(f"{US_API}/user", status=status, **body)
    with pytest.raises(ed_api.EdAPIError) as excinfo:
        ed_api._authed_get(f"{US_API}/user", "tok")
    return excinfo.value


def test_bad_token_400_is_auth_error(mocked_http):
    # Ed answers an invalid or expired token with 400 "bad_token", not 401.
    error = _error_from(mocked_http, 400, json={"code": "bad_token", "message": "Invalid token"})
    assert error.status == 400
    assert error.code == "bad_token"
    assert error.is_auth_error


def test_401_is_auth_error(mocked_http):
    assert _error_from(mocked_http, 401, body="Unauthorized").is_auth_error


@pytest.mark.parametrize("status, body", [
    (400, {"code": "bad_request"}),
    (403, {"code": "forbidden"}),
    (404, {}),
    (500, {}),
])
def test_other_errors_are_not_auth_errors(mocked_http, status, body):
    assert not _error_from(mocked_http, status, json=body).is_auth_error


def test_non_json_body_is_summarized(mocked_http):
    page = "<html>\n  <body>\n" + "x" * 1000 + "</body></html>"
    error = _error_from(mocked_http, 502, body=page, content_type="text/html")
    assert error.code is None
    message = str(error)
    assert message.startswith(f"GET {US_API}/user failed: 502 - <html> <body> x")
    assert "\n" not in message
    assert len(message.split(" - ", 1)[1]) == 300


# --- _authed_get ---

def test_authed_get_sends_bearer_token(mocked_http):
    mocked_http.get(f"{US_API}/user", json={"ok": True})
    response = ed_api._authed_get(f"{US_API}/user", "tok", params={"a": 1})

    assert response.json() == {"ok": True}
    request = mocked_http.calls[0].request
    assert request.headers["Authorization"] == "Bearer tok"
    assert "X-Token" not in request.headers
    assert request.url == f"{US_API}/user?a=1"


def test_authed_get_keeps_extra_headers(mocked_http):
    mocked_http.get(f"{US_API}/user", json={})
    ed_api._authed_get(f"{US_API}/user", "tok", headers={"Accept": "application/json"})
    request = mocked_http.calls[0].request
    assert request.headers["Accept"] == "application/json"
    assert request.headers["Authorization"] == "Bearer tok"


def test_authed_get_does_not_follow_redirects(mocked_http):
    # A wrong region's host redirects to the homepage instead of answering.
    mocked_http.get(f"{US_API}/user", status=302, headers={"Location": "https://edstem.org/"})
    with pytest.raises(ed_api.EdAPIError) as excinfo:
        ed_api._authed_get(f"{US_API}/user", "tok")
    assert excinfo.value.status == 302
    assert len(mocked_http.calls) == 1
