"""Notification channels. ntfy for now; ED_LISTENER_NTFY_SERVER selects a self-hosted server."""

import os

DEFAULT_NTFY_SERVER = "https://ntfy.sh"


def ntfy_server():
    return (os.environ.get("ED_LISTENER_NTFY_SERVER") or DEFAULT_NTFY_SERVER).rstrip("/")


def ntfy_subscribe_url(topic):
    """Opening this link in a browser or the ntfy app subscribes to the topic."""
    return f"{ntfy_server()}/{topic}"
