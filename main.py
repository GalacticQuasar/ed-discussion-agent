from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from auth_flow import get_token, scrape_threads


app = FastAPI()


@app.get("/")
def serve_frontend():
    return FileResponse("index.html")


@app.get("/api/threads")
def get_threads(course_id: int = 92707, limit: int = 30, sort: str = "new"):
    token, _ = get_token()
    data = scrape_threads(token, course_id)
    return data


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
