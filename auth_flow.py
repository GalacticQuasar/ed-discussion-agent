import requests
import os
import dotenv

dotenv.load_dotenv()

EMAIL = os.getenv("ED_EMAIL")
PASSWORD = os.getenv("ED_PASSWORD")


def get_auth_token():
    payload = {
        "login": EMAIL,
        "password": PASSWORD
    }

    response = requests.post("https://us.edstem.org/api/token", json=payload)

    if response.status_code == 200:
        return response.json().get("token")
    else:
        raise Exception(f"Login failed: {response.status_code} - {response.text}")

def get_courses(token):
    url = "https://us.edstem.org/api/user"
    headers = {"X-Token": token}

    response = requests.get(url, headers=headers)
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

    response = requests.get(f"https://us.edstem.org/api/courses/{course_id}/threads", headers=headers, params=params)
    return response.json()


if __name__ == "__main__":
    try:
        token = get_auth_token()
        print("Successfully authenticated!")
        print(f"Token: {token}")

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