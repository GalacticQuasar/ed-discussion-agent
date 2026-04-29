import requests
import os
import dotenv

dotenv.load_dotenv()

# --- Configuration ---
LOGIN_URL = "https://us.edstem.org/api/token"
THREADS_URL = "https://us.edstem.org/api/courses/92707/threads"  # CS 252 Ed discussion
EMAIL = os.getenv("ED_EMAIL")
PASSWORD = os.getenv("ED_PASSWORD")


def get_fresh_token():
    payload = {
        "login": EMAIL,
        "password": PASSWORD
    }

    response = requests.post(LOGIN_URL, json=payload)

    if response.status_code == 200:
        return response.json().get("token")
    else:
        raise Exception(f"Login failed: {response.status_code} - {response.text}")

def scrape_threads(token):
    headers = {
        "X-Token": token,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    }
    params = {
        "limit": 30,
        "sort": "new"
    }

    response = requests.get(THREADS_URL, headers=headers, params=params)
    return response.json()


try:
    token = get_fresh_token()
    print("Successfully authenticated!")
    print(f"Token: {token}")

    data = scrape_threads(token)
    print(f"Retrieved {len(data.get('threads', []))} threads.")

except Exception as e:
    print(f"Error: {e}")