## TODO
- Finalize backend for recreating Ed client (use Ed API key primarily (no need for refreshing), have fallback for username/password if needed)
    - Notify user that using username/password is less secure and they will need to sign-in again every time their session expires
- Make React SPA for Ed recreation frontend
    - Create a comprehensive SPEC.md for current frontend functionality, use it to guide new React frontend development
    - Have similar to Ed interface where threads are listed in a left column and clicking on one shows the content in the right main area
    - Frontend for semantic search for threads can be in a search bar at the top
        - RAG chat interface could be in a floating bar at the bottom

## Features (to implement)

### Retrieval: Semantic Search, RAG
- Semantic search for threads in a course (embeddings + vector database)
    - Evolve to RAG (Retrieval Augmented Generation) where agent can pull in relevant threads and use them to answer user queries
        - LLM should use filters on Ed discussion according to user query (like filter with "homework 5" if user asks specifically about homework 5)
        - LLM should provide Ed discussion links as references (render directly in interface?)
- Prioritize recent posts? Reranker step?

### Event-Driven: Notifications, Continuous Updates
- Notification system (agent has tool to send email when it finds new post on topic)
    - Basically "semantic event listeners" that trigger when new post matches criteria defined in natural language
    - Figure out how to see if new posts have been made in a course (for notifications)
    - Could group common notifications (like assignment deadline changes) as an option for users to subscribe to, so reduces the number of custom notifications to set up
- Continuous compilation of updates regarding specific topics (homework, project, exam) that students can query for latest info/tips/etc.
    - Memory: Store in Markdown for human readability OR structured format (YAML/JSON) for easy parsing and retrieval
        - If structured, could have each update be an object with fields like "title", "content", "source_link", "created_at", etc.

> Note: Use Ollama SDK for LLMs and embedding models
> Note: Architecture should probably be building services (semantic retrieval of threads in a course, semantic event listener which would have its own LLM call) and then make a final agent that can use those services as tools to answer user queries and set up notifications

## Use Cases to think about
- "Tell me everything I need to know about homework 5"
    - Should compile a list of tips/advice from relevant posts
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
