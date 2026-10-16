"""Interactive CLI: log in, list your courses, and dump 30 recent threads for one."""

import dotenv

from ed_discussion_mcp import ed_api

dotenv.load_dotenv()


def scrape_threads(token, course_id):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    }
    params = {
        "limit": 30,
        "sort": "new"
    }

    response = ed_api._authed_get(f"{ed_api.api_base()}/courses/{course_id}/threads", token, headers=headers, params=params)
    return response.json()


if __name__ == "__main__":
    try:
        token, _ = ed_api.get_token()
        print("Successfully authenticated!")
        print(f"Token: {token[:6]}...")

        all_courses = ed_api.get_courses(token)

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
