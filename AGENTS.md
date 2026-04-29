# AGENTS.md

## Run

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows PowerShell
pip install -r requirements.txt
```

Create `.env`:
```
ED_EMAIL=<your edstem email>
ED_PASSWORD=<your edstem password>
```

Start server:
```bash
python main.py                   # FastAPI on http://0.0.0.0:8000
```

## Key Files

- `main.py` — FastAPI app, serves `index.html` at `/` and `/api/threads?course_id=&limit=&sort=`
- `auth_flow.py` — Canonical auth module: `get_auth_token()` → `get_courses(token)` → `scrape_threads(token, course_id)`. `main.py` imports from here.
- `old/fetch-ed-discussion.py` — Legacy scratch script. Uses `ED_X_TOKEN` env var directly; do not import from here.
- `index.html` — All-in-one frontend (HTML/CSS/JS), served as static file.
- `todo.md` — Planned features and available thread metadata fields.

## Architecture

- `main.py` → `auth_flow.py` (email/password login → session token → API calls)
- No tests, no linting, no CI yet.
- Planned: Ollama SDK for LLM/embedding, independent service modules → agent that uses them as tools.

## Ed API Quirks

- Base: `https://us.edstem.org/api/`
- Auth: `POST /api/token` with `{"login": email, "password": password}` → returns session token
- Thread fetching: `GET /api/courses/{course_id}/threads` with `X-Token` header (capitalized)
- Session tokens are short-lived — may need refreshing on long runs
- Threads response has a `document` field containing HTML content