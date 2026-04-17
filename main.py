from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import requests
import os
import dotenv

dotenv.load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ED_API_URL = "https://us.edstem.org/api/courses/92707/threads"


@app.get("/")
def serve_frontend():
    return FileResponse("index.html")


@app.get("/api/threads")
def get_threads(limit: int = 30, sort: str = "new"):
    headers = {"x-token": os.getenv("ED_X_TOKEN")}
    params = {"limit": limit, "sort": sort}

    response = requests.get(ED_API_URL, headers=headers, params=params)

    if response.ok:
        # print(response.json()["threads"][0].keys())
        return response.json()
    else:
        return {"error": response.text}, response.status_code


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
