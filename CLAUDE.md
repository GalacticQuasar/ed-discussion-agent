# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Tooling for agents over Ed Discussion (edstem.org) course forums. The main deliverable is `ed-discussion-mcp`, a read-only MCP server published on PyPI that users run locally with `uvx ed-discussion-mcp` and their own Ed API token (see `README.md` for user-facing setup). `experiments/` holds prototypes that aren't part of the package.

`todo.md` tracks what's done and what's next. The planned direction: semantic retrieval over threads, natural-language "semantic event listeners" that trigger notifications (e.g. email) on matching new posts, and continuously compiled topic summaries. The plan is to build these as services and expose them as tools to a final agent. **Use the Ollama SDK for LLMs and embedding models** (per `todo.md`).

## Setup and commands

Managed with uv (`pyproject.toml` + `uv.lock`; there is no requirements.txt):

```bash
uv sync    # installs the package in editable mode plus the "experiments" and "dev" dependency groups
```

Credentials (all read from environment variables at call time, never at import):
- `ED_API_TOKEN` (preferred): a static API token from `https://edstem.org/<region>/settings/api-tokens`. When set, it takes precedence everywhere.
- `ED_EMAIL` / `ED_PASSWORD` (fallback): email/password login via `POST /api/token`, used only when `ED_API_TOKEN` is unset.
- `ED_REGION` (default `us`): selects the Ed region.

The package never loads `.env` itself: installed via `uvx` it has no repo to find one in, so users pass variables through their MCP client's config. Locally, `.env` (gitignored) is loaded by `uv run --env-file .env` for the server and by `dotenv.load_dotenv()` in the experiment scripts.

All callers get their token from `ed_api.get_token()`, which applies this precedence. Don't call `get_auth_token()` directly.

Tests: `uv run pytest` (add `-k <name>` for one test). They need no credentials or network: HTTP is mocked with `responses`, and `tests/conftest.py` has autouse fixtures that clear the `ED_*` variables and reset `server`'s cached token. `pytest` and `responses` live in the `dev` dependency group. GitHub Actions (`.github/workflows/tests.yml`) runs them on every push to `main` and every PR, on Python 3.10 through 3.14, after `uv sync --locked` (so a stale `uv.lock` fails CI; run `uv lock` after editing dependencies). Dependabot (`.github/dependabot.yml`) opens a weekly PR when the workflow's actions have new releases.

Entry points (there is no linter or build step beyond `uv build`):
- `uv run --env-file .env ed-discussion-mcp`: the MCP server over stdio from the working copy. `.mcp.json` runs exactly this for Claude Code. `python -m ed_discussion_mcp` also works.
- `uv run --env-file .env ed-discussion-mcp --http [--host H] [--port 8001]`: the same server over Streamable HTTP at `/mcp`, for clients that connect to a URL (e.g. Open WebUI).
- `uvx --no-cache --from . ed-discussion-mcp`: runs the working copy packaged the way users get it. Without `--no-cache`, uvx reuses its first cached environment for a local path at the same version and silently runs stale code.
- `uv run experiments/watch_posts.py [COURSE_ID ...]`, `uv run experiments/main.py` (FastAPI UI on :8000), `uv run experiments/auth_flow.py` (interactive CLI): prototypes.

Releasing to PyPI (only when the user asks; ordinary commits don't need a release). The README is the PyPI project page, so README changes ship only with a new version, and a version number can never be re-uploaded:
1. Bump the version in both `pyproject.toml` and `src/ed_discussion_mcp/__init__.py` (`__version__`), then `uv lock`. Patch for fixes and docs; minor for new features, and while on 0.x also for breaking changes (renaming tools or changing their parameters).
2. Commit, tag `vX.Y.Z`, and push both (`git push && git push --tags`) before publishing, so the release matches a commit. Don't amend or force-push a tagged commit.
3. `rm -rf dist && uv build` (`uv publish` uploads everything in `dist/`), then `uv run --env-file .env -- uv publish` (`uv publish` has no `--env-file`; it reads `UV_PUBLISH_TOKEN`, a project-scoped token kept in `.env`). Never pass the token with `--token`.
4. Verify with `uvx --no-cache ed-discussion-mcp --help`.

## Architecture

### Package (`src/ed_discussion_mcp/`)

Dependencies are only `mcp`, `requests` and `uvicorn` (for HTTP mode); anything only the experiments need goes in the `experiments` dependency group, and test-only tools go in `dev`, not `[project.dependencies]`.

- **`ed_api.py`**: synchronous `requests` helpers for Ed's REST API.
  - Regions: `region()` validates `ED_REGION`; `api_base()`, `stream_url()`, `web_base()`, `thread_url()` and `token_settings_url()` build URLs from it. The API host is `<region>.edstem.org` except for `au`, whose API is on the bare `edstem.org` (`au.edstem.org` just redirects to the homepage). Web links are `https://edstem.org/<region>/...` for every region.
  - `get_token()` returns `(token, refreshable)`: `ED_API_TOKEN` if set (not refreshable), otherwise an email/password login (refreshable). It raises `MissingCredentialsError` if neither is configured, and `region()` raises `InvalidRegionError`.
  - `_authed_get()` sends `Authorization: Bearer <token>` (`ED_API_TOKEN` is rejected in `X-Token`), doesn't follow redirects, and raises `EdAPIError` on any non-2xx status. `EdAPIError` has `status`, Ed's error `code`, and `is_auth_error`: Ed answers an invalid or expired token with 400 `bad_token`, not 401, so check `is_auth_error` rather than the status.
  - `get_courses()` returns courses in Ed dashboard order via `sort_courses()`, which sets `dashboard_active` on each course (`is_dashboard_active()` mirrors Ed's rule: not archived and `last_active` within a month). Anything that lists courses should use this rather than sorting itself.
- **`server.py`**: read-only MCP server on the `mcp` 2.x SDK (`MCPServer`, the renamed FastMCP; 1.x examples using `FastMCP` won't match). `main()` is the `ed-discussion-mcp` console script. Tools: `list_courses`, `course_overview`, `search_threads`, `list_threads`, `get_thread`.
  - All API calls go through `_call()`, which gets the token lazily, re-logs in on an auth error only for email/password tokens, and turns `EdAPIError`, `MissingCredentialsError`, `InvalidRegionError` and connection errors into `ToolError` with a hint. Only `ToolError` messages reach the model; any other exception shows up as a generic "Error executing tool".
  - Endpoints used: `GET /courses/{id}/threads/search?query=` for keyword search (matches titles, bodies and replies; supports `limit`, `offset`, `category`); `/courses/{id}/threads` (max 100 per page, paged with `offset`, accepts `category`); `/threads/{id}` and `/courses/{id}/threads/{number}` (full thread with nested `answers`/`comments` plus a `users` list for author lookup); `/courses/{id}` (categories under `settings.discussion.categories`).
  - A thread's `document` field is already plain text; `content` is XML. Staff means any `course_role` other than `student`.
  - Logs go to stderr; stdout carries the MCP protocol.
  - `main()` parses `--http/--host/--port`; without `--http` it runs stdio.
  - The tool names, parameters and `INSTRUCTIONS` are the server's public interface for users' agents; changing them is a breaking change.

- **`http_transport.py`**: HTTP mode. Wraps the SDK's `streamable_http_app()` in `BearerAuth`, an ASGI middleware that returns 401 unless `Authorization: Bearer <ED_MCP_AUTH_TOKEN>` matches (constant-time compare; a random token is generated and logged if the variable is unset), and runs it with uvicorn. Non-HTTP scopes pass through so the app's lifespan still starts its session manager. For a localhost bind the SDK adds DNS rebinding protection (foreign `Host` headers get 421); for other binds, the bearer token is the only protection. Auth is deliberately not optional: anyone who can call the endpoint can read Ed as the token owner.

### Experiments (`experiments/`)

Prototypes that import `ed_discussion_mcp.ed_api` and load `.env` with python-dotenv. Run them as scripts (`uv run experiments/<file>.py`); they import each other as top-level modules.

- **`ed_stream.py`**: async real-time layer using `aiohttp`. `listen(course_ids, on_message)` connects to `ed_api.stream_url()` with `Authorization: Bearer <token>`, sends a `course.subscribe` per course, and calls the async `on_message` for each event. It swallows the `course.subscribe` acks and `chat.init`. It reconnects on disconnect and re-logs in on a rejected token (handshake status 400 or 401; Ed uses 400 for an invalid token) only when the token came from email/password; a rejected static `ED_API_TOKEN` raises.
- **`watch_posts.py`**: a consumer of `ed_stream`. It filters for `thread.new` events (the thread is at `message["data"]["thread"]`) and prints them as plain text. New event-driven features (such as semantic listeners) should plug in as `on_message` handlers in the same way.
- **`main.py` + `index.html`**: a minimal web UI. The default `course_id` is hardcoded (92707), the endpoint re-authenticates on every request, and its `limit`/`sort` query params are accepted but ignored (`scrape_threads` in `auth_flow.py` hardcodes them).
- **`auth_flow.py`**: interactive CLI that lists courses and dumps 30 recent threads; also defines `scrape_threads()` for `main.py`.

`old/` is reference material, not part of the app. `todo.md` lists the thread metadata fields the API returns.

## Conventions

Don't create git commits; leave committing to the user.

Tests never call the real Ed API. Mock HTTP with the `mocked_http` fixture (a `responses.RequestsMock`, which fails on any unregistered request), or `monkeypatch` `ed_api` functions when testing logic above the HTTP layer (as `tests/test_server_call.py` does for `_call()`). Set credentials and region with `monkeypatch.setenv`.
