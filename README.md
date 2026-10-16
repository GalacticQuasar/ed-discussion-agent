# ed-discussion-mcp

An [MCP](https://modelcontextprotocol.io) server that lets AI agents (Claude Code, Claude Desktop, Cursor, VS Code, Codex and others) search and read your [Ed Discussion](https://edstem.org) courses. You can then ask things like:

- "When is the CS 180 midterm, and has the room changed?"
- "Was the HW3 deadline extended?"
- "What should I know before starting homework 4?"

**Read-only and local.** The server runs on your own machine, signs in with your own Ed API token, and only reads from Ed: it never posts, edits or reacts. Your token is sent only to Ed.

Keep in mind that the agent sees whatever your Ed account can see, including private threads. Thread content it reads goes to the AI model your client uses, just as if you had pasted it into the chat.

> This project uses Ed's unofficial API, so an Ed update could break it. It is not affiliated with Ed.

## Tools

| Tool | What it does |
|---|---|
| `list_courses` | Your courses in Ed dashboard order (archived ones optional) |
| `course_overview` | A course's categories, pinned threads, recent announcements and recent threads |
| `search_threads` | Keyword search using Ed's own search (titles, bodies and replies) |
| `list_threads` | Newest threads, filtered by category, type, pinned, staff author or date |
| `get_thread` | A full thread with nested answers and comments, by id or by `#number` |

Every result includes the author's role (student or staff), timestamps, whether the post was edited, and a link to the thread.

## Setup

### 1. Install uv

The server runs with [uv](https://docs.astral.sh/uv/getting-started/installation/)'s `uvx`, which downloads and runs it in an isolated environment; there is nothing else to install.

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. Create an Ed API token

Go to [edstem.org/us/settings/api-tokens](https://edstem.org/us/settings/api-tokens) (use your own region in place of `us`), create a token, and copy it.

### 3. Add the server to your client

Most MCP clients accept this standard config. Put your token in place of `your_token`:

```json
{
  "mcpServers": {
    "ed-discussion": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/GalacticQuasar/ed-discussion-agent", "ed-discussion-mcp"],
      "env": { "ED_API_TOKEN": "your_token" }
    }
  }
}
```

Your token goes in your client's own config file on your machine. Don't put it in a file you commit or share.

<details>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add ed-discussion --scope user -e ED_API_TOKEN=your_token -- \
  uvx --from git+https://github.com/GalacticQuasar/ed-discussion-agent ed-discussion-mcp
```
</details>

<details>
<summary><b>Claude Desktop</b></summary>

Open Settings → Developer → Edit Config, add the standard config to `claude_desktop_config.json`, and restart Claude Desktop.
</details>

<details>
<summary><b>Cursor</b></summary>

Add the standard config to `~/.cursor/mcp.json` (all projects) or `.cursor/mcp.json` (one project), or open Settings → MCP → Add new MCP Server.
</details>

<details>
<summary><b>VS Code (GitHub Copilot)</b></summary>

Run **MCP: Open User Configuration** from the Command Palette and add the server. VS Code uses `servers` rather than `mcpServers`, and can ask for your token once and store it securely instead of keeping it in the file:

```json
{
  "inputs": [
    { "type": "promptString", "id": "ed-api-token", "description": "Ed API token", "password": true }
  ],
  "servers": {
    "ed-discussion": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "git+https://github.com/GalacticQuasar/ed-discussion-agent", "ed-discussion-mcp"],
      "env": { "ED_API_TOKEN": "${input:ed-api-token}" }
    }
  }
}
```
</details>

<details>
<summary><b>Codex</b></summary>

```bash
codex mcp add ed-discussion --env ED_API_TOKEN=your_token -- \
  uvx --from git+https://github.com/GalacticQuasar/ed-discussion-agent ed-discussion-mcp
```

Or add it to `~/.codex/config.toml`:

```toml
[mcp_servers.ed-discussion]
command = "uvx"
args = ["--from", "git+https://github.com/GalacticQuasar/ed-discussion-agent", "ed-discussion-mcp"]
env = { ED_API_TOKEN = "your_token" }
```
</details>

**Other clients:** any client that can launch a local MCP server works. Give it the `uvx` command and arguments from the standard config, with `ED_API_TOKEN` set in its environment. For clients that only connect to servers over HTTP, see [HTTP mode](#http-mode).

### Configuration

| Variable | Default | Description |
|---|---|---|
| `ED_API_TOKEN` | | Your Ed API token. |
| `ED_REGION` | `us` | Your Ed region, as in your Ed URL (`edstem.org/us/...`), e.g. `au` or `eu`. |
| `ED_EMAIL`, `ED_PASSWORD` | | Alternative to `ED_API_TOKEN`: sign in with email and password. Ignored when `ED_API_TOKEN` is set. |
| `ED_MCP_AUTH_TOKEN` | random | [HTTP mode](#http-mode) only: the bearer token clients must send. |

### HTTP mode

By default, your client launches the server and talks to it over stdio. Some clients, such as Open WebUI, instead connect to a server that is already running at a URL. For those, start the server yourself with `--http`:

```bash
ED_API_TOKEN=your_token ED_MCP_AUTH_TOKEN=choose-a-long-random-secret \
  uvx --from git+https://github.com/GalacticQuasar/ed-discussion-agent ed-discussion-mcp --http
```

It serves MCP over Streamable HTTP at `http://127.0.0.1:8001/mcp` (change it with `--host` and `--port`). Point your client at that URL and set the header `Authorization: Bearer <your ED_MCP_AUTH_TOKEN>`; most clients have a bearer token or headers field for this. Requests without the token are rejected. If you leave `ED_MCP_AUTH_TOKEN` unset, the server makes up a token and prints it at startup, but it changes every time the server restarts.

For example, in Open WebUI go to Admin Settings → External Tools, add a server of type **MCP (Streamable HTTP)**, enter the URL, and choose Bearer authentication with your token.

The server listens only on your own machine (`127.0.0.1`) unless you pass `--host`. If your client runs in Docker (for example Open WebUI in Docker Desktop), start the server with `--host 0.0.0.0` and use `http://host.docker.internal:8001/mcp` as the URL. That makes the server reachable from your network too, so the token is all that stands between it and your Ed account. Use a long random one.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/GalacticQuasar/ed-discussion-agent.git
cd ed-discussion-agent
uv sync
```

Put your credentials in a `.env` file in the repo root (it is gitignored):

```env
ED_API_TOKEN=your_api_token
```

- `uv run --env-file .env ed-discussion-mcp` runs the server from your working copy. Claude Code does this automatically through `.mcp.json` when you open the repo (approve the server when prompted).
- `uvx --no-cache --from . ed-discussion-mcp` runs it the way users will, from a fresh build. `--no-cache` matters here: without it, uvx reuses its first build of the local package even after you change the code.

The package lives in `src/ed_discussion_mcp/`: `server.py` defines the MCP tools, and `ed_api.py` handles credentials, regions and Ed's REST API.

## Experiments

`experiments/` holds prototypes that are not part of the package. They use the package's `ed_api` module and read credentials from `.env`. `uv sync` installs their dependencies.

- `uv run experiments/watch_posts.py [COURSE_ID ...]`: watches for new posts in real time over Ed's websocket stream. With no arguments it asks which courses to watch.
- `uv run experiments/main.py`: a minimal web UI on port 8000 that lists a course's recent threads.
- `uv run experiments/auth_flow.py`: an interactive CLI that lists your courses and dumps a course's 30 newest threads.
