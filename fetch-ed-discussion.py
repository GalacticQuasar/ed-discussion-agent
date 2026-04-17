import requests
import dotenv
import os

dotenv.load_dotenv()

url = "https://us.edstem.org/api/courses/92707/threads"  # CS 250 Ed discussion
params = {
    "limit": 30,
    "sort": "new"
}

headers = {
#    "accept": "*/*",
#    "accept-language": "en-US,en;q=0.9",
#    "dnt": "1",
#    "origin": "https://edstem.org",
#    "priority": "u=1, i",
#    "sec-ch-ua": '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
#    "sec-ch-ua-mobile": "?0",
#    "sec-ch-ua-platform": '"Windows"',
#    "sec-fetch-dest": "empty",
#    "sec-fetch-mode": "cors",
#    "sec-fetch-site": "same-site",
#    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "x-token": os.getenv("ED_X_TOKEN")
}

response = requests.get(url, headers=headers, params=params)

print("res status code:", response.status_code)

if response.ok:
    data = response.json()
    # print(data)
    print("Ruquest Success.")
else:
    print("Error:", response.text)

print("--------------------OUTPUT--------------------")
for thread in data["threads"]:
    print("--------------------OUTPUT--------------------")
    print(thread["title"], thread["id"])
    print("\tCategory:", thread["category"])
    print("\t", thread["document"])
print("----------------------------------------------")

# print(data['threads'][0].keys())
#print(json.dumps(data, indent=4)[:10000])
