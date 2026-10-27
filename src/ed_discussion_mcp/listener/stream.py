"""Real-time Ed discussion events over Ed's (undocumented) websocket."""

import asyncio
import logging

import aiohttp

from ..ed_api import get_token, stream_url

log = logging.getLogger(__name__)

RECONNECT_DELAY = 5
# Ed rejects an invalid or expired token at the handshake with 400 (and a
# missing one with 401). aiohttp drops the body, so the "bad_token" code
# that ed_api.EdAPIError checks isn't available here.
_AUTH_REJECTED_STATUSES = {400, 401}
# Sent on connect, and on every thread view; nothing listens for them.
_IGNORED_TYPES = {"course.subscribe", "chat.init", "herald.init", "course.count"}

_CLOSE_TYPES = (
    aiohttp.WSMsgType.CLOSE,
    aiohttp.WSMsgType.CLOSING,
    aiohttp.WSMsgType.CLOSED,
)


async def listen(course_ids, on_message, on_connect=None, reconnect=None):
    """Connect to Ed's websocket stream and subscribe to courses.

    course_ids is a list, or a callable returning one, called on every
    (re)connect. on_message(message) is awaited for every event except
    connection bookkeeping; on_connect() is awaited after subscribing (e.g.
    to backfill what was missed while disconnected). Setting the optional
    reconnect asyncio.Event drops the connection and reconnects, picking up
    new course_ids.

    Runs until cancelled, reconnecting on disconnects and re-authenticating
    when the token is rejected and came from email/password.
    """
    token, refreshable = get_token()
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.ws_connect(
                    stream_url(),
                    headers={"Authorization": f"Bearer {token}"},
                    heartbeat=60,
                ) as ws:
                    log.info("Connected to Ed stream")
                    courses = course_ids() if callable(course_ids) else course_ids
                    pending = {}
                    for i, course_id in enumerate(courses, start=1):
                        pending[i] = course_id
                        await ws.send_json({"type": "course.subscribe", "oid": course_id, "id": i})
                    if on_connect:
                        await on_connect()
                    await _read(ws, pending, on_message, reconnect)
                    if reconnect and reconnect.is_set():
                        reconnect.clear()
                        log.info("Reconnecting to pick up course changes")
                        continue
            except aiohttp.WSServerHandshakeError as e:
                if e.status in _AUTH_REJECTED_STATUSES:
                    if refreshable:
                        log.warning("Token rejected (%s); re-authenticating", e.status)
                        token, refreshable = get_token()
                    else:
                        log.error("ED_API_TOKEN was rejected (%s). Check the token, or unset it to use "
                                  "email/password login.", e.status)
                        raise
                else:
                    log.warning("Handshake failed (status %s); retrying in %ss", e.status, RECONNECT_DELAY)
            except aiohttp.ClientConnectionError as e:
                log.warning("Connection failed (%s); retrying in %ss", e, RECONNECT_DELAY)
            else:
                log.warning("Disconnected; reconnecting in %ss", RECONNECT_DELAY)
            await asyncio.sleep(RECONNECT_DELAY)


async def _read(ws, pending, on_message, reconnect):
    """Dispatch messages until the connection closes or reconnect is set."""
    stop = asyncio.ensure_future(reconnect.wait()) if reconnect else None
    try:
        while True:
            receive = asyncio.ensure_future(ws.receive())
            waiting = {receive, stop} if stop else {receive}
            await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
            if not receive.done():
                receive.cancel()
                await ws.close()
                return
            msg = receive.result()
            if msg.type == aiohttp.WSMsgType.TEXT:
                message = msg.json()
                mtype = message.get("type")
                if mtype == "course.subscribe":
                    log.info("Subscribed to course %s", pending.get(message.get("id")))
                    continue
                if mtype in _IGNORED_TYPES:
                    continue
                try:
                    await on_message(message)
                except Exception:
                    log.exception("Error in event handler")
            elif msg.type == aiohttp.WSMsgType.ERROR:
                log.error("Stream error: %s", ws.exception())
                return
            elif msg.type in _CLOSE_TYPES:
                log.info("Stream closed by server (code %s)", msg.data)
                return
    finally:
        if stop:
            stop.cancel()
