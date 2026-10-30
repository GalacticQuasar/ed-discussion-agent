"""listener.stream: reconnect behaviour. aiohttp's session is faked; nothing connects."""

import asyncio

import aiohttp
import pytest

from ed_discussion_mcp.listener import stream


class Stop(Exception):
    """Ends listen()'s loop from the fake sleep."""


def test_reconnect_delay_backs_off_to_a_cap(monkeypatch):
    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def ws_connect(self, *args, **kwargs):
            raise aiohttp.ClientConnectionError("network is unreachable")

    delays = []

    async def sleep(seconds):
        delays.append(seconds)
        if len(delays) == 7:
            raise Stop

    monkeypatch.setattr(stream, "get_token", lambda: ("token", False))
    monkeypatch.setattr(stream.aiohttp, "ClientSession", Session)
    monkeypatch.setattr(stream.asyncio, "sleep", sleep)

    with pytest.raises(Stop):
        asyncio.run(stream.listen([1], on_message=None))

    assert delays == [5, 10, 20, 40, 60, 60, 60]
