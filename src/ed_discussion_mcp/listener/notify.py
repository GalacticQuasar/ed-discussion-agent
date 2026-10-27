"""Notification channels. ntfy for now; ED_LISTENER_NTFY_SERVER selects a self-hosted server.

A notification is a dict with "title", "message" and "url" (the Ed thread).
"""

import logging
import os

import requests

log = logging.getLogger(__name__)

DEFAULT_NTFY_SERVER = "https://ntfy.sh"


def ntfy_server():
    return (os.environ.get("ED_LISTENER_NTFY_SERVER") or DEFAULT_NTFY_SERVER).rstrip("/")


def ntfy_subscribe_url(topic):
    """Opening this link in a browser or the ntfy app subscribes to the topic."""
    return f"{ntfy_server()}/{topic}"


def send_ntfy(topic, notification):
    """Publish to ntfy; returns whether it was accepted.

    Uses ntfy's JSON publishing (POST to the server root) rather than headers,
    so titles can be any Unicode.
    """
    payload = {
        "topic": topic,
        "title": notification["title"],
        "message": notification["message"],
        "click": notification["url"],
        "tags": ["mailbox_with_mail"],
    }
    try:
        response = requests.post(ntfy_server(), json=payload, timeout=10)
    except requests.RequestException as e:
        log.warning("ntfy send failed: %s", e)
        return False
    if not response.ok:
        log.warning("ntfy rejected the notification (%s): %s", response.status_code, response.text[:200])
    return response.ok
