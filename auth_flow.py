import requests

ED_API_BASE_URL = "https://us.edstem.org/api"


class EdAuthError(Exception):
    pass


def get_auth_token(email, password):
    payload = {
        "login": email,
        "password": password
    }

    response = requests.post(f"{ED_API_BASE_URL}/token", json=payload)

    if response.status_code == 200:
        return response.json().get("token")
    else:
        raise EdAuthError(f"Login failed: {response.status_code} - {response.text}")

def get_courses(token):
    url = f"{ED_API_BASE_URL}/user"
    headers = {"X-Token": token}

    response = requests.get(url, headers=headers)
    if response.status_code == 401:
        raise EdAuthError("Token expired or invalid")
    if response.status_code != 200:
        return []

    data = response.json()
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
        "X-Token": token,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    }
    params = {
        "limit": 30,
        "sort": "new"
    }

    response = requests.get(f"{ED_API_BASE_URL}/courses/{course_id}/threads", headers=headers, params=params)
    if response.status_code == 401:
        raise EdAuthError("Token expired or invalid")
    response.raise_for_status()
    return response.json()
