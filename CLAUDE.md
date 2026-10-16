# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Early-stage tooling for an agent over Ed Discussion (edstem.org) course forums. Currently it authenticates against Ed's unofficial API, lists threads, watches for new posts in real time, and exposes read-only course access to agents through an MCP server. `todo.md` describes the planned direction: semantic retrieval over threads, natural-language "semantic event listeners" that trigger notifications (e.g. email) on matching new posts, and continuously compiled topic summaries. The plan is to build these as services and expose them as tools to a final agent. **Use the Ollama SDK for LLMs and embedding models** (per `todo.md`).

## Setup and commands

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Credentials live in `.env` (gitignored):
- `ED_API_TOKEN` (preferred): a static API token. When set, it takes precedence everywhere.
- `ED_EMAIL` / `ED_PASSWORD` (fallback): email/password login via `POST /api/token`, used only when `ED_API_TOKEN` is unset.

All entry points get their token from `auth_flow.get_token()`, which applies this precedence. Don't call `get_auth_token()` directly.

Entry points (there are no tests, linter, or build step):
- `python main.py`: FastAPI server on :8000. It serves `index.html` at `/` and proxies `/api/threads?course_id=...`.
- `python watch_posts.py [COURSE_ID ...]`: watches for new threads over websocket. With no args it prompts you to pick from your enrolled courses.
- `python ed_mcp_server.py`: MCP server over stdio (registered for Claude Code in `.mcp.json`).
- `python auth_flow.py`: interactive CLI that logs in, lists courses, and dumps 30 recent threads for a course.

## Architecture

- **`auth_flow.py`**: synchronous `requests` helpers for the Ed REST API at `https://us.edstem.org/api`. `get_token()` returns `(token, refreshable)`: it uses `ED_API_TOKEN` if set (not refreshable) and otherwise logs in with `get_auth_token()` (refreshable). `get_courses()` and `scrape_threads()` go through `_authed_get()`, which sends `Authorization: Bearer <token>` (the style Ed's API docs specify; `ED_API_TOKEN` is rejected in `X-Token`) and raises `EdAPIError` on any non-2xx response (`status`, Ed's error `code`, and `is_auth_error`; Ed answers an invalid or expired token with 400 `bad_token`, not 401, so check `is_auth_error` rather than the status). `get_token()` raises `MissingCredentialsError` when neither `ED_API_TOKEN` nor `ED_EMAIL`/`ED_PASSWORD` is set. `get_courses()` returns courses in Ed dashboard order via `sort_courses()`, which sets `dashboard_active` on each course (dashboard "Courses" vs "Archived"; `is_dashboard_active()` mirrors Ed's rule: not archived and `last_active` within a month). Any tool that lists courses should use this rather than sorting itself.
- **`ed_stream.py`**: async real-time layer using `aiohttp`. `listen(course_ids, on_message)` connects to `wss://us.edstem.org/api/stream` with `Authorization: Bearer <token>`, sends a `course.subscribe` per course, and calls the async `on_message` for each event. It swallows the `course.subscribe` acks and `chat.init`. It reconnects on disconnect and re-logs in on a rejected token (handshake status 400 or 401; Ed uses 400 for an invalid token) only when the token came from email/password; a rejected static `ED_API_TOKEN` raises. `fetch_enrolled_courses()` is a thin wrapper around `auth_flow.get_courses()`.
- **`watch_posts.py`**: a consumer of `ed_stream`. It filters for `thread.new` events (the thread is at `message["data"]["thread"]`) and converts Ed's rich-text `document` JSON or HTML `content` to plain text. New event-driven features (such as semantic listeners) should plug in as `on_message` handlers in the same way.
- **`ed_mcp_server.py`**: read-only MCP server built on the `mcp` 2.x SDK (`MCPServer`, the renamed FastMCP). Tools: `list_courses`, `course_overview`, `search_threads`, `list_threads`, `get_thread`. All API calls go through `_call()`, which gets the token lazily, re-logs in on an auth error only for email/password tokens, and turns `EdAPIError` and `MissingCredentialsError` into `ToolError` so the model sees the message (any other exception reaches the model only as a generic "Error executing tool"). Keyword search uses Ed's `GET /courses/{id}/threads/search?query=` endpoint, which matches titles, bodies and replies and supports `limit`, `offset` and `category`. Other endpoints used: `/courses/{id}/threads` (max 100 per page, paged with `offset`, accepts `category`), `/threads/{id}` and `/courses/{id}/threads/{number}` (full thread with nested `answers`/`comments` plus a `users` list for author lookup), and `/courses/{id}` (discussion categories under `settings.discussion.categories`). A thread's `document` field is already plain text; `content` is XML. Staff means any `course_role` other than `student`.
- **`main.py` + `index.html`**: a minimal web UI. The default `course_id` is hardcoded (92707), the endpoint re-authenticates on every request, and its `limit`/`sort` query params are accepted but ignored (`scrape_threads` hardcodes them).

`old/` is reference material, not part of the app. `todo.md` lists the thread metadata fields the API returns.

## Conventions

Don't create git commits; leave committing to the user.
