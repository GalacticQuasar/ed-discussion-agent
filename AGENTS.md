# AGENTS.md

## Run

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows PowerShell
pip install -r requirements.txt
```

Start server:
```bash
python main.py                   # FastAPI on http://0.0.0.0:8000
```

No `.env` needed — users log in via the frontend.

## Key Files

- `main.py` — FastAPI app, stateless. Endpoints: `GET /`, `POST /api/login`, `GET /api/courses`, `GET /api/threads?course_id=&limit=&sort=`
- `auth_flow.py` — Auth + API module: `get_auth_token(email, password)` → `get_courses(token)` → `scrape_threads(token, course_id)`. Raises `EdAuthError` on auth failures. `main.py` imports from here.
- `old/fetch-ed-discussion.py` — Legacy scratch script. Uses `ED_X_TOKEN` env var directly; do not import from here.
- `index.html` — SPA frontend (HTML/CSS/JS): login view, course selection view, threads view. Token + credentials stored in `localStorage`. Auto token refresh with toast notification.
- `todo.md` — Planned features and available thread metadata fields.

## Architecture

- **Server is stateless** — no server-side session or `.env` credentials. All auth state lives on the client.
- Client stores `{token, email, password}` in `localStorage` after login.
- Every API call (courses, threads) sends `X-Token` header.
- On 401 `TOKEN_EXPIRED`, client auto-refreshes token via `POST /api/login` with stored credentials, retries the request, and shows a toast.
- If refresh also fails, client redirects to login and clears stored credentials.
- `main.py` → `auth_flow.py` (login → session token → API calls)
- No tests, no linting, no CI yet.
- Planned: Ollama SDK for LLM/embedding, independent service modules → agent that uses them as tools.

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | None | Serves `index.html` |
| POST | `/api/login` | None | `{email, password}` → `{token}` |
| GET | `/api/courses` | `X-Token` | Returns `{courses: [...]}` or 401 `TOKEN_EXPIRED` |
| GET | `/api/threads?course_id=X` | `X-Token` | Returns thread data or 401 `TOKEN_EXPIRED` |

## Ed API Quirks

- Base: `https://us.edstem.org/api/`
- Auth: `POST /api/token` with `{"login": email, "password": password}` → returns session token
- Thread fetching: `GET /api/courses/{course_id}/threads` with `X-Token` header (capitalized)
- Session tokens are short-lived — client handles refresh automatically
- Threads response has a `document` field containing HTML content