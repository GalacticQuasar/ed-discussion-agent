"""Real-time Ed discussion event stream over websockets."""

import asyncio
import logging
import os

import aiohttp
import dotenv

log = logging.getLogger("ed_stream")

API_HOST = "us.edstem.org"
WS_URL = f"wss://{API_HOST}/api/stream"
RECONNECT_DELAY = 5

_CLOSE_TYPES = (
    aiohttp.WSMsgType.CLOSE,
    aiohttp.WSMsgType.CLOSING,
    aiohttp.WSMsgType.CLOSED,
)


def get_token():
    """Return (token, refreshable).

    Prefers ED_API_TOKEN from the environment (.env); falls back to
    email/password login. 'refreshable' means a rejected token can be
    replaced by logging in again (email/password tokens yes, static API
    tokens no).
    """
    dotenv.load_dotenv()
    token = os.getenv("ED_API_TOKEN")
    if token:
        log.debug("Using ED_API_TOKEN from environment")
        return token, False
    from auth_flow import get_auth_token

    log.debug("Authenticating with email/password")
    return get_auth_token(), True


def fetch_enrolled_courses():
    """Fetch courses the user is enrolled in via the REST API.

    Returns a list of dicts with id, code, name, status, role, created_at,
    sorted newest first.
    """
    import requests

    token, _ = get_token()
    url = f"https://{API_HOST}/api/user"
    res = None
    for headers in (
        {"Authorization": f"Bearer {token}"},
        {"X-Token": token},
    ):
        res = requests.get(url, headers=headers, timeout=15)
        if res.status_code == 200:
            break
    if res is None or res.status_code != 200:
        detail = f"{res.status_code} - {res.text}" if res is not None else "no response"
        raise RuntimeError(f"Could not fetch courses: {detail}")

    courses = []
    for entry in res.json().get("courses", []):
        info = entry.get("course", {})
        courses.append({
            "id": info.get("id"),
            "code": info.get("code"),
            "name": info.get("name"),
            "status": info.get("status"),
            "role": (entry.get("role") or {}).get("role"),
            "created_at": info.get("created_at") or "",
        })
    courses.sort(key=lambda c: c["created_at"], reverse=True)
    return courses


async def listen(course_ids, on_message):
    """Connect to Ed's websocket stream and subscribe to course_ids.

    Calls on_message(message_dict) for every event received. Runs until
    cancelled, reconnecting automatically on disconnects and re-authenticating
    if the token is rejected (when possible).
    """
    token, refreshable = get_token()
    auth_headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.ws_connect(
                    WS_URL,
                    headers=auth_headers,
                    heartbeat=60,
                ) as ws:
                    log.info("Connected to Ed stream")
                    pending = {}
                    for i, course_id in enumerate(course_ids, start=1):
                        pending[i] = course_id
                        await ws.send_json({"type": "course.subscribe", "oid": course_id, "id": i})
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            message = msg.json()
                            mtype = message.get("type")
                            if mtype == "course.subscribe":
                                log.info("Subscribed to course %s", pending.get(message.get("id")))
                                continue
                            if mtype == "chat.init":
                                continue
                            try:
                                await on_message(message)
                            except Exception:
                                log.exception("Error in event handler")
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            log.error("Stream error: %s", ws.exception())
                            break
                        elif msg.type in _CLOSE_TYPES:
                            log.info("Stream closed by server (code %s)", msg.data)
                            break
            except aiohttp.WSServerHandshakeError as e:
                if e.status == 401:
                    if refreshable:
                        log.warning("Token rejected (401); re-authenticating")
                        token, refreshable = get_token()
                        auth_headers = {"Authorization": f"Bearer {token}"}
                    else:
                        log.error(
                            "ED_API_TOKEN was rejected (401). Check the token in .env "
                            "or unset it to use email/password login."
                        )
                        raise
                else:
                    log.warning("Handshake failed (status %s); retrying in %ss", e.status, RECONNECT_DELAY)
            except aiohttp.ClientConnectionError as e:
                log.warning("Connection failed (%s); retrying in %ss", e, RECONNECT_DELAY)
            else:
                log.warning("Disconnected; reconnecting in %ss", RECONNECT_DELAY)
            await asyncio.sleep(RECONNECT_DELAY)