import calendar
import requests
import logging
import os
import re
import dotenv
from datetime import datetime, timezone

dotenv.load_dotenv()

log = logging.getLogger("auth_flow")

EMAIL = os.getenv("ED_EMAIL")
PASSWORD = os.getenv("ED_PASSWORD")


def get_token():
    """Return (token, refreshable).

    Prefers ED_API_TOKEN from the environment (.env); falls back to
    email/password login. 'refreshable' means a rejected token can be
    replaced by logging in again (email/password tokens yes, static API
    tokens no).
    """
    token = os.getenv("ED_API_TOKEN")
    if token:
        log.debug("Using ED_API_TOKEN from environment")
        return token, False
    log.debug("Authenticating with email/password")
    return get_auth_token(), True


def get_auth_token():
    """Log in with ED_EMAIL/ED_PASSWORD. Prefer get_token(), which uses ED_API_TOKEN when set."""
    payload = {
        "login": EMAIL,
        "password": PASSWORD
    }

    response = requests.post("https://us.edstem.org/api/token", json=payload)

    if response.status_code == 200:
        return response.json().get("token")
    else:
        raise Exception(f"Login failed: {response.status_code} - {response.text}")

def _authed_get(url, token, headers=None, **kwargs):
    """GET with Bearer auth (the style Ed's API docs specify). Raises on a non-2xx status."""
    headers = {**(headers or {}), "Authorization": f"Bearer {token}"}
    response = requests.get(url, headers=headers, **kwargs)
    if not response.ok:
        raise Exception(f"GET {url} failed: {response.status_code} - {response.text}")
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
    url = "https://us.edstem.org/api/user"

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

def scrape_threads(token, course_id):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    }
    params = {
        "limit": 30,
        "sort": "new"
    }

    response = _authed_get(f"https://us.edstem.org/api/courses/{course_id}/threads", token, headers=headers, params=params)
    return response.json()


if __name__ == "__main__":
    try:
        token, _ = get_token()
        print("Successfully authenticated!")
        print(f"Token: {token[:6]}...")

        all_courses = get_courses(token)

        print("---------- Available Courses: ----------")
        for c in all_courses:
            status_str = "[ACTIVE]" if c['dashboard_active'] else "[ARCHIVED]"
            print(f"{status_str} {c['code']}: {c['name']} (ID: {c['id']})")
        print("----------------------------------------")
        
        selected_course = input("\nEnter the course ID to scrape threads from: ")

        data = scrape_threads(token, selected_course)
        print(f"Retrieved {len(data.get('threads', []))} threads.")
        # Print out all threads
        for thread in data.get("threads", []):
            print(f"Thread ID: {thread['id']}, Title: {thread['title']}")

    except Exception as e:
        print(f"Error: {e}")