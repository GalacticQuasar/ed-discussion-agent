## Done
- Read-only MCP server (`ed-discussion-mcp`): `list_courses`, `course_overview`, `search_threads`, `list_threads`, `get_thread`
    - Results include Ed thread links, and the server instructions tell agents to cite them
    - Filters: category, thread type, pinned, staff author, date
    - Region support, stdio and HTTP (bearer auth) modes
    - Published on PyPI (0.1.1): `uvx ed-discussion-mcp`
- Tests (`uv run pytest`): `ed_api` credentials, regions and errors; `server._call()` re-login and error conversion
    - Tools (`tests/test_server_tools.py`): result shaping, filters, paging, `get_thread` lookup
    - Not yet covered: `get_courses` dashboard sorting and `http_transport`
- Real-time new post stream over Ed's websocket (prototype: `experiments/ed_stream.py`, `experiments/watch_posts.py`)

## Next
- Semantic event listeners: notify when a new post matches criteria defined in natural language
    - Plug in as an `on_message` handler on `ed_stream.listen()`, filtering `thread.new` events
    - LLM/embedding check per new thread against each rule
    - Decide where rules are stored and how notifications are sent (email?)
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
