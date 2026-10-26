## Done
- Read-only MCP server (`ed-discussion-mcp`): `list_courses`, `course_overview`, `search_threads`, `list_threads`, `get_thread`
    - Results include Ed thread links, and the server instructions tell agents to cite them
    - Filters: category, thread type, pinned, staff author, date
    - Region support, stdio and HTTP (bearer auth) modes
    - Published on PyPI (0.1.2): `uvx ed-discussion-mcp`
- Tests (`uv run pytest`): `ed_api` credentials, regions and errors; `server._call()` re-login and error conversion
    - Tools (`tests/test_server_tools.py`): result shaping, filters, paging, `get_thread` lookup
    - Not yet covered: `get_courses` dashboard sorting and `http_transport`
- Real-time new post stream over Ed's websocket (prototype: `experiments/ed_stream.py`, `experiments/watch_posts.py`)

## Next
- Semantic event listeners: notify when a new post matches criteria defined in natural language (plan below)
- Semantic retrieval of threads in a course
    - Embed threads and index them locally; keyword search via Ed already exists
    - Could become an MCP tool (adding a tool changes the server's public interface)
- Continuous compilation of updates on specific topics (homework, project, exam) that students can query for latest info/tips/etc.
    - Store in Markdown for human readability OR structured format (YAML/JSON) for easy parsing and retrieval
        - If structured, could have each update be an object with fields like "title", "content", "source_link", "created_at", etc.
    - Depends on semantic retrieval; listeners could keep it updated incrementally
- Final agent that uses these services as tools to answer user queries and set up notifications
- Cleanup: decide whether to retire `experiments/main.py`, `index.html` and `auth_flow.py` (the MCP server covers them; `main.py` hardcodes a course ID and ignores its query params)

> Note: Use Ollama SDK for LLMs and embedding models
> Note: Architecture should probably be building services (semantic retrieval of threads in a course, semantic event listener which would have its own LLM call) and then make a final agent that can use those services as tools to answer user queries and set up notifications

# Plan: semantic event listener

## Decisions
- Personal daemon using the user's own `ED_API_TOKEN`; no hosted multi-user service
- Events: `thread.new` plus staff replies (answers/comments), since recordings and extensions often land as staff replies in existing threads or megathreads
- Rules are written by the user's own agent through MCP tools (e.g. "notify me if there are any deadline changes" in Claude Code); no generative LLM runs locally
- Matching: structured prefilters (course, `staff_only`, private) then Ollama System One (`POST /v1/systemone`, https://docs.ollama.com/api/systemone)
    - One request per event; each active rule in the course is one `noul` question (max 64 per request, chunk beyond that)
    - `noul` returns an uncalibrated 0-1 score, so each rule has a threshold (global default, per-rule override)
    - No explanation text comes back, so notifications show the post and which rules matched
    - Local `nimble` by default; cloud models opt-in, and private threads are skipped on cloud unless allowed
    - No Python SDK is documented for System One; use `requests` unless `ollama-python` supports it
- Rules are `once` (disable after first match) or `always`
- Storage: SQLite, shared by the MCP server and the daemon
- Notifications: `Notifier` interface; ntfy first, Discord webhook later
- Downtime: backfill from a per-course cursor on every (re)connect; dedupe by event id
- One notification per event, listing every rule it matched; edits never re-notify

## Packaging
- Rule tools live in `ed-discussion-mcp` but only register when `ED_LISTENER=1`, so the default tool list is unchanged. They write local state only, never Ed. Minor version bump.
- The daemon is `ed-discussion-mcp listen`, run by the user or as a systemd/launchd service; the MCP server's stdio process only lives while the client session is open
- Tools need only `requests` and `sqlite3`; the websocket client goes in a `listener` extra: `uvx --from 'ed-discussion-mcp[listener]' ed-discussion-mcp listen`
- `experiments/ed_stream.py` moves into the package as `ed_discussion_mcp/listener/stream.py`
- DB at `~/.local/share/ed-discussion-mcp/listener.db` by default (`ED_LISTENER_DB` overrides), so both processes find it without matching config
- Config: `OLLAMA_HOST`, `ED_LISTENER_MODEL` (default `nimble`), `ED_LISTENER_NTFY_TOPIC` (optional, see below)

## ntfy setup
- If `ED_LISTENER_NTFY_TOPIC` is unset, `listen` generates a random topic on first run, stores it in SQLite, and prints the subscribe link `https://ntfy.sh/<topic>` (plus a terminal QR code)
- User setup is: install the ntfy app (or open `https://ntfy.sh/app`), subscribe to the printed topic. No account.
- `list_rules` returns the subscribe link so the agent can tell the user where to subscribe
- The topic name is the only access control on public ntfy.sh, and the operator can read messages. Consider a title-only option; document self-hosting / reserved topics
- Check ntfy.sh's current anonymous rate limits before documenting

## MCP tools (only with `ED_LISTENER=1`)
- `preview_rule(course_id, instructions, staff_only=False, limit=20)`: scores the course's recent threads, returns top hits with scores and links plus the score distribution. Docstring tells the agent to phrase instructions as a yes/no question about this post ("Does this post announce a change to an assignment deadline?"), look for false positives (e.g. students asking about the topic), and revise before creating
- `create_rule(course_ids, instructions, request, mode, staff_only=False, threshold=None)`: `request` is the user's original sentence, shown in notifications
- `list_rules()`: also reports daemon health from a heartbeat row and the ntfy subscribe link
- `delete_rule(id)`
- `INSTRUCTIONS` gains a paragraph describing the preview-then-create workflow when the flag is set

## Pipeline
- websocket stream + backfill -> normalize -> prefilter -> System One judge -> dedupe/record -> notify
- Event: `{key, course_id, thread_id, kind: thread|reply, author_is_staff, is_private, title, category, body, parent_context, url}`; key is `thread:<id>` or `comment:<id>`; `parent_context` (thread title and shortened body, via `get_thread`) for replies
- Log every score, not just hits, so near-misses can be reviewed
- Write order: insert into `sent` (unique key = dedupe), notify, mark sent/failed (retry failed), disable `once` rules

## Schema
- `rules(id, request, instructions, course_ids, staff_only, mode, threshold, enabled, created_at, fired_at)`
- `cursors(course_id, last_seen_at)`
- `judgments(event_key, rule_id, score, model, at)`
- `sent(event_key UNIQUE, rule_ids, status, at)`
- `settings(key, value)`: generated ntfy topic
- `heartbeat(at, version)`: updated every minute by the daemon

## Things to check first
1. Websocket event names for answers/comments, and whether the payload includes the author's role (log every event type with `watch_posts`)
2. Whether `/courses/{id}/threads` can sort by `updated_at` for reply backfill (otherwise diff `reply_count`)
3. `nimble` latency per request: sets `preview_rule`'s default `limit` and whether previews run in parallel
4. Whether `ollama-python` supports System One
5. Long megathread post plus parent context vs the 64 KiB local limit; where to truncate

## Milestones
1. Run the checks above
2. Judge, `preview_rule`, and an offline eval harness (replay a past term's threads against sample rules, label hits, report precision/recall per threshold; bias toward recall)
3. SQLite storage, rule tools behind the flag, `INSTRUCTIONS` addition
4. `listen`: stream, backfill, dedupe, heartbeat, ntfy with auto-generated topic
5. README section and minor release; then Discord notifier

## Tests
- System One and ntfy mocked with `responses`; a canned websocket feed replayed through the pipeline
- Unit tests for dedupe, `once` rules and backfill cursors against a temporary SQLite file

## Risks
- Silent misses are the worst failure and invisible to the user; the eval harness and near-miss log are the defence
- Ed's websocket is undocumented and may change; backfill doubles as a polling fallback

# Use Cases to think about
- "Tell me everything I need to know about homework 5"
    - Should compile a list of tips/advice from relevant posts
    - Partly possible today with the MCP server (search + read threads); digests would make it faster and more complete
- "Notify me when the lecture recording for week 7 is posted"
    - Agent should be able to send email notification when new post is made with lecture recording link
    - Could generalize to "Notify me when there's a due date extension" for any assignment
- General/Continuous: Create markdown formatted report on all discussion activity (group by homework, project, exam)
    - Could store in structured format (YAML/JSON) for easy parsing

Available metadata for inspiration:
- id
- user_id
- course_id
- original_id
- editor_id
- accepted_id
- duplicate_id
- number
- type
- title
- content
- document
- category
- subcategory
- subsubcategory
- flag_count
- star_count
- view_count
- unique_view_count
- vote_count
- reply_count
- unresolved_count
- is_locked
- is_pinned
- is_private
- is_endorsed
- is_answered
- is_student_answered
- is_staff_answered
- is_archived
- is_anonymous
- is_megathread
- anonymous_comments
- approved_status
- created_at
- updated_at
- deleted_at
- pinned_at
- anonymous_id
- vote
- is_seen
- is_starred
- is_watched
- glanced_at
- new_reply_count
- duplicate_title
- user
