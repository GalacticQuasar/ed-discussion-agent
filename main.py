from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from auth_flow import get_auth_token, get_courses, scrape_threads, EdAuthError


app = FastAPI()


@app.get("/")
def serve_frontend():
    return FileResponse("index.html")


@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    email = body.get("email")
    password = body.get("password")

    if not email or not password:
        return JSONResponse({"error": "Email and password are required"}, status_code=400)

    try:
        token = get_auth_token(email, password)
        return {"token": token}
    except EdAuthError as e:
        return JSONResponse({"error": str(e)}, status_code=401)


@app.get("/api/courses")
def fetch_courses(request: Request):
    token = request.headers.get("X-Token")
    if not token:
        return JSONResponse({"error": "TOKEN_EXPIRED"}, status_code=401)

    try:
        courses = get_courses(token)
    except EdAuthError:
        return JSONResponse({"error": "TOKEN_EXPIRED"}, status_code=401)

    return {"courses": courses}


@app.get("/api/threads")
def fetch_threads(request: Request, course_id: int, limit: int = 30, sort: str = "new"):
    token = request.headers.get("X-Token")
    if not token:
        return JSONResponse({"error": "TOKEN_EXPIRED"}, status_code=401)

    try:
        data = scrape_threads(token, course_id)
        return data
    except EdAuthError:
        return JSONResponse({"error": "TOKEN_EXPIRED"}, status_code=401)
    except Exception:
        return JSONResponse({"error": "Failed to fetch threads"}, status_code=500)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
