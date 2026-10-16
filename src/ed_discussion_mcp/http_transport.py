"""Serve the MCP server over Streamable HTTP, guarded by a bearer token.

Anyone who can call the endpoint can read Ed as the token's owner, so every
request must carry `Authorization: Bearer <token>`. The token comes from the
ED_MCP_AUTH_TOKEN environment variable; without it, a random one is generated
and logged at startup (fine for trying it out, but it changes on every start).
"""

import hmac
import json
import logging
import os
import secrets

import uvicorn

log = logging.getLogger(__name__)

AUTH_TOKEN_ENV = "ED_MCP_AUTH_TOKEN"
MCP_PATH = "/mcp"


class BearerAuth:
    """ASGI middleware that rejects HTTP requests without the expected bearer token."""

    def __init__(self, app, token):
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        # Lifespan events pass through; the MCP app uses them to start its session manager.
        if scope["type"] == "http":
            supplied = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(supplied, self.expected):
                body = json.dumps({"error": "unauthorized", "message": "Missing or invalid bearer token"}).encode()
                await send({
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", b'Bearer realm="ed-discussion-mcp"'),
                    ],
                })
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def serve(mcp, host, port):
    """Serve mcp at http://host:port/mcp until interrupted."""
    token = os.getenv(AUTH_TOKEN_ENV)
    if not token:
        token = secrets.token_urlsafe(32)
        log.warning(
            "%s is not set; generated a token for this run only: %s", AUTH_TOKEN_ENV, token
        )
    # For 127.0.0.1/localhost the SDK also enables DNS rebinding protection (Host/Origin checks).
    app = BearerAuth(mcp.streamable_http_app(streamable_http_path=MCP_PATH, host=host), token)
    log.info("Serving MCP over HTTP at http://%s:%s%s", host, port, MCP_PATH)
    uvicorn.run(app, host=host, port=port, log_level="info")
