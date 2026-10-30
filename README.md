# ed-discussion-mcp

[![PyPI](https://img.shields.io/pypi/v/ed-discussion-mcp)](https://pypi.org/project/ed-discussion-mcp/)
[![Tests](https://github.com/GalacticQuasar/ed-discussion-agent/actions/workflows/tests.yml/badge.svg)](https://github.com/GalacticQuasar/ed-discussion-agent/actions/workflows/tests.yml)

An [MCP](https://modelcontextprotocol.io) server that lets AI agents (Claude Code, Claude Desktop, Cursor, VS Code, Codex and others) search and read your [Ed Discussion](https://edstem.org) courses. You can then ask things like:

- "When is the CS 180 midterm, and has the room changed?"
- "Was the HW3 deadline extended?"
- "What should I know before starting homework 4?"

**Read-only and local.** The server runs on your own machine, signs in with your own Ed API token, and only reads from Ed: it never posts, edits or reacts. Your token is sent only to Ed.

The optional [notifications](#notifications-experimental) feature adds tools that save rules on your machine; it still never writes to Ed.

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

With `ED_LISTENER=1`, five more tools set up and review [notifications](#notifications-experimental): `preview_rule`, `create_rule`, `list_rules`, `list_notifications` and `delete_rule`.

## Setup

### 1. Install uv

The server runs with [uv](https://docs.astral.sh/uv/getting-started/installation/)'s `uvx`, which downloads [`ed-discussion-mcp` from PyPI](https://pypi.org/project/ed-discussion-mcp/) and runs it in an isolated environment; there is nothing else to install.

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
      "args": ["ed-discussion-mcp"],
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
  uvx ed-discussion-mcp
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
      "args": ["ed-discussion-mcp"],
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
  uvx ed-discussion-mcp
```

Or add it to `~/.codex/config.toml`:

```toml
[mcp_servers.ed-discussion]
command = "uvx"
args = ["ed-discussion-mcp"]
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
| `ED_LISTENER` | | Set to `1` to add the [notification](#notifications-experimental) tools. |

### HTTP mode

By default, your client launches the server and talks to it over stdio. Some clients, such as Open WebUI, instead connect to a server that is already running at a URL. For those, start the server yourself with `--http`:

```bash
ED_API_TOKEN=your_token ED_MCP_AUTH_TOKEN=choose-a-long-random-secret \
  uvx ed-discussion-mcp --http
```

It serves MCP over Streamable HTTP at `http://127.0.0.1:8001/mcp` (change it with `--host` and `--port`). Point your client at that URL and set the header `Authorization: Bearer <your ED_MCP_AUTH_TOKEN>`; most clients have a bearer token or headers field for this. Requests without the token are rejected. If you leave `ED_MCP_AUTH_TOKEN` unset, the server makes up a token and prints it at startup, but it changes every time the server restarts.

For example, in Open WebUI go to Admin Settings → External Tools, add a server of type **MCP (Streamable HTTP)**, enter the URL, and choose Bearer authentication with your token.

The server listens only on your own machine (`127.0.0.1`) unless you pass `--host`. If your client runs in Docker (for example Open WebUI in Docker Desktop), start the server with `--host 0.0.0.0` and use `http://host.docker.internal:8001/mcp` as the URL. That makes the server reachable from your network too, so the token is all that stands between it and your Ed account. Use a long random one.

## Notifications (experimental)

Ask your agent "notify me if any deadline changes in CS 180" or "tell me when the week 7 recording is posted", and you'll get a push notification on your phone when a matching post appears. The agent turns your request into a yes/no question about a post, tests it against the course's recent threads, and saves it as a rule. A small background program, the listener, watches your courses, checks each new thread and staff reply against your rules with a small model running on your machine, and sends matches to the [ntfy](https://ntfy.sh) app.

### Set up

1. Install [Ollama](https://ollama.com/download) and download the model the listener uses (about 830 MB):

   ```bash
   ollama pull tev1:0.8b
   ```

2. Turn on the notification tools by adding `"ED_LISTENER": "1"` to the `env` in your client's config, next to `ED_API_TOKEN` (with Claude Code, add `-e ED_LISTENER=1` to the `claude mcp add` command). Restart your client.

3. Start the listener in a terminal:

   ```bash
   ED_API_TOKEN=your_token uvx --from 'ed-discussion-mcp[listener]' ed-discussion-mcp listen
   ```

   It prints a link and a QR code. Install the ntfy app ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy), [iOS](https://apps.apple.com/app/ntfy/id1625396347), or [the web app](https://ntfy.sh/app)), and scan the code or open the link to subscribe. No account is needed. Your agent can show the link again any time (`list_rules`).

4. Ask your agent for a notification. Rules apply as soon as they're saved; there's no need to restart the listener.

Later, ask "what did my notifications catch this week?" The agent can list what was sent and which posts almost matched, which helps when a rule fires too often or misses something.

The listener only notifies while it's running. To keep it running in the background, run it as a service. On Linux, for example, save your token in `~/.config/ed-listener.env` as `ED_API_TOKEN=your_token` (run `chmod 600` on that file), and create `~/.config/systemd/user/ed-listener.service`:

```ini
[Unit]
Description=Ed Discussion notification listener

[Service]
EnvironmentFile=%h/.config/ed-listener.env
ExecStart=%h/.local/bin/uvx --from ed-discussion-mcp[listener] ed-discussion-mcp listen
Restart=on-failure

[Install]
WantedBy=default.target
```

Then run `systemctl --user enable --now ed-listener`, and `journalctl --user -u ed-listener` to see its link and logs.

You can stop the listener, lose your connection or close your laptop: when it's back, it catches up on new threads and staff replies posted in the meantime. If Ed or Ollama can't be reached while a post is being checked, it tries that post again every minute for up to a day.

### Privacy

- Posts are checked by the model on your own machine, unless you configure a cloud model. With a cloud model, private threads are skipped unless you set `ED_LISTENER_CLOUD_PRIVATE=1`.
- Notifications go through the public ntfy.sh server. Anyone who knows your topic name can read them, so the listener generates a long random one; don't share the link. Notifications include the post's title and the start of its body, and for private threads only the title. To keep notifications off ntfy.sh, [self-host ntfy](https://docs.ntfy.sh/install/) and set `ED_LISTENER_NTFY_SERVER`.
- Rules, notification history and the topic are stored in a local SQLite file (`~/.local/share/ed-discussion-mcp/listener.db` on Linux).

### Limitations

- The model is small, so expect a few false alarms per rule over a term (for example, a "new homework released" rule can also fire on a "grades released" post). Specific wording helps; the agent's preview shows how a rule scores on recent posts.
- Only new threads and replies by course staff are checked, not student replies or edits.
- After a long time off, everything that matched while it was off arrives at once.

### Listener configuration

| Variable | Default | Description |
|---|---|---|
| `ED_LISTENER_MODEL` | `tev1:0.8b` | Ollama model used to check posts. It must support Ollama's System One API. |
| `ED_LISTENER_KEEP_ALIVE` | `30s` | How long Ollama keeps the model in memory after checking a post, as a duration (`10s`, `5m`) or seconds. |
| `OLLAMA_HOST` | `127.0.0.1:11434` | Where Ollama is running. |
| `ED_LISTENER_DB` | per-user data folder | The SQLite file holding rules and history. The MCP server and the listener must use the same file. |
| `ED_LISTENER_NTFY_TOPIC` | random | The ntfy topic to send to, instead of the generated one. |
| `ED_LISTENER_NTFY_SERVER` | `https://ntfy.sh` | A self-hosted ntfy server. |
| `ED_LISTENER_CLOUD_PRIVATE` | | Set to `1` to let a cloud model check private threads. |

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

- `uv run --env-file .env ed-discussion-mcp listen` runs the notification listener. Set `ED_LISTENER_DB` to a scratch file to keep test runs out of your real rules.

The package lives in `src/ed_discussion_mcp/`: `server.py` defines the MCP tools, `ed_api.py` handles credentials, regions and Ed's REST API, and `listener/` holds the notification tools and the listener.

## Experiments

`experiments/` holds prototypes that are not part of the package. They use the package's `ed_api` module and read credentials from `.env`. `uv sync` installs their dependencies.

- `uv run experiments/watch_posts.py [COURSE_ID ...]`: watches for new posts in real time over Ed's websocket stream. With no arguments it asks which courses to watch.
- `uv run experiments/main.py`: a minimal web UI on port 8000 that lists a course's recent threads.
- `uv run experiments/auth_flow.py`: an interactive CLI that lists your courses and dumps a course's 30 newest threads.
- `uv run experiments/eval_listener.py`: scores a course's threads against sample notification rules and reports precision and recall per threshold, from labels you add.

## License

[MIT](LICENSE)
