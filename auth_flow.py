import requests
import logging
import os
import dotenv

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

def get_courses(token):
    url = "https://us.edstem.org/api/user"

    data = _authed_get(url, token).json()
    courses = []

    for entry in data.get("courses", []):
        course_info = entry.get("course", {})
        courses.append({
            "id": course_info.get("id"),
            "code": course_info.get("code"),
            "name": course_info.get("name"),
            "status": course_info.get("status")
        })

    return courses

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
            status_str = "[ACTIVE]" if c['status'] == 'active' else "[ARCHIVED]"
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