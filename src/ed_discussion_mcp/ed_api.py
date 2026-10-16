"""Synchronous helpers for Ed Discussion's REST API: credentials, regions, errors and courses.

Configuration comes from environment variables, read on every call so a
caller can load a .env file after importing this module:

- ED_API_TOKEN: a static API token (preferred).
- ED_EMAIL / ED_PASSWORD: email/password login, used only when ED_API_TOKEN is unset.
- ED_REGION: the Ed region your account lives in, e.g. "us" or "au" (default "us").
"""

import calendar
import logging
import os
import re
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

DEFAULT_REGION = "us"
# Ed's original (Australian) region serves its API from the bare domain;
# au.edstem.org only redirects to the homepage. Other regions use a subdomain.
_API_HOSTS = {"au": "edstem.org"}


class MissingCredentialsError(Exception):
    """Neither ED_API_TOKEN nor ED_EMAIL/ED_PASSWORD is set."""


class InvalidRegionError(Exception):
    """ED_REGION is not a plausible Ed region code."""


def region():
    """The configured Ed region code, e.g. "us" (the default) or "au"."""
    value = (os.getenv("ED_REGION") or DEFAULT_REGION).strip().lower()
    if not re.fullmatch(r"[a-z]{2,3}", value):
        raise InvalidRegionError(f'ED_REGION must be a region code such as "us" or "au", got {value!r}.')
    return value


def _api_host():
    code = region()
    return _API_HOSTS.get(code, f"{code}.edstem.org")


def api_base():
    """Base URL of the REST API for the configured region."""
    return f"https://{_api_host()}/api"


def stream_url():
    """Websocket URL of the real-time event stream for the configured region."""
    return f"wss://{_api_host()}/api/stream"


def web_base():
    """Base URL of the Ed website for the configured region, e.g. https://edstem.org/us."""
    return f"https://edstem.org/{region()}"


def thread_url(course_id, thread_id):
    """Browser link to a thread."""
    return f"{web_base()}/courses/{course_id}/discussion/{thread_id}"


def token_settings_url():
    """Page where users create and view their API tokens."""
    return f"{web_base()}/settings/api-tokens"


def get_token():
    """Return (token, refreshable).

    Prefers ED_API_TOKEN; falls back to email/password login. 'refreshable'
    means a rejected token can be replaced by logging in again
    (email/password tokens yes, static API tokens no).
    """
    token = os.getenv("ED_API_TOKEN")
    if token:
        log.debug("Using ED_API_TOKEN from environment")
        return token, False
    if not (os.getenv("ED_EMAIL") and os.getenv("ED_PASSWORD")):
        raise MissingCredentialsError(
            f"No Ed credentials found. Set the ED_API_TOKEN environment variable to an API token "
            f"(create one at {token_settings_url()}), or set ED_EMAIL and ED_PASSWORD."
        )
    log.debug("Authenticating with email/password")
    return get_auth_token(), True


def get_auth_token():
    """Log in with ED_EMAIL/ED_PASSWORD. Prefer get_token(), which uses ED_API_TOKEN when set."""
    payload = {
        "login": os.getenv("ED_EMAIL"),
        "password": os.getenv("ED_PASSWORD"),
    }

    response = requests.post(f"{api_base()}/token", json=payload, allow_redirects=False)

    if response.status_code == 200:
        return response.json().get("token")
    else:
        raise EdAPIError.from_response("Login failed", response)

class EdAPIError(Exception):
    """A non-2xx response from Ed's API. 'status' holds the HTTP status code and
    'code' Ed's error code from the JSON body (e.g. "bad_token"), if any."""

    def __init__(self, message, status, code=None):
        super().__init__(message)
        self.status = status
        self.code = code

    @classmethod
    def from_response(cls, message, response):
        try:
            code = response.json().get("code")
        except (ValueError, AttributeError):
            code = None
        body = re.sub(r"\s+", " ", response.text).strip()[:300]
        return cls(f"{message}: {response.status_code} - {body}", response.status_code, code)

    @property
    def is_auth_error(self):
        """True if Ed rejected the token. Ed answers an invalid token with 400 "bad_token", not 401."""
        return self.status == 401 or self.code == "bad_token"


def _authed_get(url, token, headers=None, **kwargs):
    """GET with Bearer auth (the style Ed's API docs specify). Raises EdAPIError on a non-2xx status.

    Redirects aren't followed: Ed's API doesn't redirect, but a wrong region's
    host does (to the homepage), which would otherwise surface as a JSON error.
    """
    headers = {**(headers or {}), "Authorization": f"Bearer {token}"}
    response = requests.get(url, headers=headers, allow_redirects=False, **kwargs)
    if not 200 <= response.status_code < 300:
        raise EdAPIError.from_response(f"GET {url} failed", response)
    return response

# Session ranks from Ed's dashboard; later in the year ranks higher.
_SESSION_RANK = {"winter": 1, "spring": 2, "summer": 3, "fall": 4}


def _natural_key(value):
    """Approximates Ed's localeCompare(..., {numeric: true}): case-insensitive, digit runs compared as numbers."""
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", str(value or ""))]


def _one_month_before(now):
    year, month = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
    return now.replace(year=year, month=month, day=min(now.day, calendar.monthrange(year, month)[1]))


def is_dashboard_active(course, now=None):
    """True if Ed's dashboard shows the course under "Courses" rather than "Archived".

    Mirrors the dashboard rule: not archived, and either a perpetual course
    (session "X"), flagged always_active, or with course activity
    (last_active, roughly the newest post) within the last month.
    """
    if course["status"] == "archived":
        return False
    if course["session"] == "X" or course["always_active"]:
        return True
    now = now or datetime.now(timezone.utc)
    return course["last_active"] is not None and course["last_active"] >= _one_month_before(now)


def sort_courses(courses, now=None):
    """Return courses in Ed dashboard order, setting course["dashboard_active"] on each.

    Active courses come first, sorted by code. Archived ones follow, sorted by
    year (newest first), then session (fall before spring), then code.
    """
    for course in courses:
        course["dashboard_active"] = is_dashboard_active(course, now)

    def key(course):
        if course["dashboard_active"]:
            return (0, _natural_key(course["code"]))
        try:
            year = -int(course["year"])
        except (TypeError, ValueError):
            year = 0
        rank = _SESSION_RANK.get(str(course["session"] or "").strip().lower())
        session = (0, -rank) if rank is not None else (1, _natural_key(course["session"]))
        return (1, year, session, _natural_key(course["code"]))

    return sorted(courses, key=key)


def get_courses(token):
    """Return the user's courses in Ed dashboard order (see sort_courses)."""
    url = f"{api_base()}/user"

    data = _authed_get(url, token).json()
    courses = []

    for entry in data.get("courses", []):
        course_info = entry.get("course", {})
        last_active = entry.get("last_active")
        courses.append({
            "id": course_info.get("id"),
            "code": course_info.get("code"),
            "name": course_info.get("name"),
            "status": course_info.get("status"),
            "role": (entry.get("role") or {}).get("role"),
            "year": course_info.get("year"),
            "session": course_info.get("session"),
            "always_active": bool((course_info.get("settings") or {}).get("always_active")),
            "last_active": datetime.fromisoformat(last_active) if last_active else None,
        })

    return sort_courses(courses)
