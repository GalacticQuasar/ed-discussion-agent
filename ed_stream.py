"""Real-time Ed discussion event stream over websockets."""

import asyncio
import logging

import aiohttp

from auth_flow import get_courses, get_token

log = logging.getLogger("ed_stream")

API_HOST = "us.edstem.org"
WS_URL = f"wss://{API_HOST}/api/stream"
RECONNECT_DELAY = 5
# Ed rejects an invalid or expired token at the handshake with 400 (and a
# missing one with 401). aiohttp drops the body, so the "bad_token" code
# that auth_flow.EdAPIError checks isn't available here.
_AUTH_REJECTED_STATUSES = {400, 401}

_CLOSE_TYPES = (
    aiohttp.WSMsgType.CLOSE,
    aiohttp.WSMsgType.CLOSING,
    aiohttp.WSMsgType.CLOSED,
)


def fetch_enrolled_courses():
    """Fetch the user's courses in Ed dashboard order (see auth_flow.get_courses)."""
    token, _ = get_token()
    return get_courses(token)


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
                if e.status in _AUTH_REJECTED_STATUSES:
                    if refreshable:
                        log.warning("Token rejected (%s); re-authenticating", e.status)
                        token, refreshable = get_token()
                        auth_headers = {"Authorization": f"Bearer {token}"}
                    else:
                        log.error(
                            "ED_API_TOKEN was rejected (%s). Check the token in .env "
                            "or unset it to use email/password login.", e.status
                        )
                        raise
                else:
                    log.warning("Handshake failed (status %s); retrying in %ss", e.status, RECONNECT_DELAY)
            except aiohttp.ClientConnectionError as e:
                log.warning("Connection failed (%s); retrying in %ss", e, RECONNECT_DELAY)
            else:
                log.warning("Disconnected; reconnecting in %ss", RECONNECT_DELAY)
            await asyncio.sleep(RECONNECT_DELAY)