"""Per-launch token auth. Every HTTP request and the WebSocket need the token."""
from __future__ import annotations

import secrets

from fastapi import Request, WebSocket

from backend.core.errors import ApiError


def token_ok(expected: str, supplied: str | None) -> bool:
    return bool(supplied) and secrets.compare_digest(expected.encode(), supplied.encode())


async def require_token(request: Request) -> None:
    expected: str = request.app.state.config.token
    supplied = request.headers.get("x-pw-token")
    if supplied is None:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:]
    if not token_ok(expected, supplied):
        raise ApiError(401, "unauthorized", "Missing or invalid API token.")


def websocket_token_ok(ws: WebSocket) -> bool:
    # Browsers cannot set headers on WebSocket, so the token travels in the query string.
    # (uvicorn access logging is disabled so this never reaches a log file.)
    return token_ok(ws.app.state.config.token, ws.query_params.get("token"))
