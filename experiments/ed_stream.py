"""Real-time Ed discussion event stream; listen() now lives in the package."""

import dotenv

from ed_discussion_mcp.ed_api import get_courses, get_token
from ed_discussion_mcp.listener.stream import listen  # noqa: F401 (re-exported for watch_posts)

dotenv.load_dotenv()


def fetch_enrolled_courses():
    """Fetch the user's courses in Ed dashboard order (see ed_api.get_courses)."""
    token, _ = get_token()
    return get_courses(token)
