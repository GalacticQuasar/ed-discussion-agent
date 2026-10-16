"""Watch for new Ed discussion threads and log them to the terminal.

Usage:
    python3 watch_posts.py [COURSE_ID ...]

With no arguments, fetches your enrolled courses and lets you pick which
ones to watch. Passing course IDs skips the prompt. Waits for real-time
'thread.new' events from Ed's websocket stream and prints each new post
with the local time it was received.
"""

import asyncio
import datetime
import logging
import sys

import ed_stream


def pick_courses():
    """Interactively let the user choose which courses to watch."""
    courses = ed_stream.fetch_enrolled_courses()
    if not courses:
        raise SystemExit("No enrolled courses found for this account.")

    print("Your courses:")
    for i, course in enumerate(courses, start=1):
        status = "" if course["dashboard_active"] else " [archived]"
        print(f"  {i}) {course['code']}: {course['name']} (ID: {course['id']}){status}")

    raw = input(
        "\nEnter course numbers to watch (comma/space separated, e.g. '1,3'), "
        "or press Enter for all: "
    ).strip()
    if not raw:
        return [c["id"] for c in courses]

    picked = []
    for part in raw.replace(",", " ").split():
        try:
            index = int(part) - 1
            picked.append(courses[index]["id"])
        except (ValueError, IndexError):
            print(f"Ignoring invalid selection: {part!r}")
    if not picked:
        raise SystemExit("No valid courses selected.")
    return picked


def render_text(content):
    """Very rough rich-text to plain text conversion for previews."""
    if not content:
        return ""
    if content.lstrip().startswith("["):
        try:
            blocks = parse_document(content)
            return blocks
        except Exception:
            pass
    import html
    import re

    text = re.sub(r"<br\s*/?>", "\n", content)
    text = re.sub(r"</p\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text)


def parse_document(document):
    """Convert Ed rich-text 'document' JSON string to plain text."""
    import json

    blocks = json.loads(document)
    parts = []
    for block in blocks:
        btype = block.get("type")
        content = block.get("content", [])
        if btype == "heading":
            text = "".join(c.get("text", "") for c in content if isinstance(c, dict))
            parts.append(f"\n## {text}\n")
        elif btype == "p":
            text = "".join(c.get("text", "") for c in content if isinstance(c, dict))
            parts.append(text + "\n")
        elif btype == "code":
            text = "".join(c.get("text", "") for c in content if isinstance(c, dict))
            parts.append("    " + text.replace("\n", "\n    "))
        elif btype == "image":
            parts.append(f"[image: {block.get('alt', '')}]")
        elif btype == "ul" or btype == "ol":
            for item in content:
                text = "".join(
                    c.get("text", "") for c in item.get("content", []) if isinstance(c, dict)
                )
                parts.append(f"  - {text}")
    return "\n".join(parts)


def print_new_post(thread):
    received_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("=" * 70)
    print(f"NEW POST RECEIVED AT: {received_at}")
    print("=" * 70)
    print(f"Title:     {thread.get('title') or '(no title)'}")
    print(f"Author:    {thread.get('user', {}).get('name', 'Anonymous')}")
    print(f"Category:  {thread.get('category', 'Uncategorized')}")
    print(f"Link:      https://{ed_stream.API_HOST}/courses/{thread.get('course_id')}/discussion/{thread.get('id')}")
    print("-" * 70)
    body = render_text(thread.get("content") or thread.get("document"))
    if body.strip():
        print(body.strip()[:1000])
    else:
        print("(no content)")
    print()


async def handle_event(message):
    if message.get("type") != "thread.new":
        return
    thread = (message.get("data") or {}).get("thread") or {}
    print_new_post(thread)


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    if len(sys.argv) > 1:
        course_ids = [int(arg) for arg in sys.argv[1:]]
    else:
        course_ids = pick_courses()
    print(f"Watching for new posts in course(s): {course_ids}")
    print("Press Ctrl+C to stop.\n")
    await ed_stream.listen(course_ids, handle_event)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")