"""Prod-like development server with the real FastAPI application.

Run from the repository root:

    python -m uvicorn dev_fastapi:app --app-dir dashboard --reload

Fake data is the default. Set ``DASHBOARD_DEV_REAL_API=1`` and
``READ_API_URL`` to use the real read API. Set ``DASHBOARD_DEV_ROOT_PATH`` to
serve the app under a local prefix such as ``/dashboard``.
"""

from __future__ import annotations

import os
from typing import cast

from starlette.types import ASGIApp, Receive, Scope, Send

from dev_fixtures import DEV_REAL_API_ENV, build_app, default_state_dir
from transducer_dashboard.app import create_app

ROOT_PATH_ENV = "DASHBOARD_DEV_ROOT_PATH"


class StripPrefixMiddleware:
    """Serve an ASGI app under a prefix the way Caddy does in production."""

    def __init__(self, app: ASGIApp, prefix: str) -> None:
        self.app = app
        self.prefix = "/" + prefix.strip("/")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] in {"http", "websocket"} and (
            path == self.prefix or path.startswith(self.prefix + "/")
        ):
            scope = cast(Scope, dict(scope))
            scope["path"] = path[len(self.prefix) :] or "/"
            scope["raw_path"] = scope["path"].encode("utf-8")
            scope["root_path"] = self.prefix
        await self.app(scope, receive, send)


if os.environ.get(DEV_REAL_API_ENV) == "1":
    app: ASGIApp = create_app()
else:
    app = build_app(default_state_dir())

root_path = os.environ.get(ROOT_PATH_ENV, "").strip()
if root_path:
    app = StripPrefixMiddleware(app, root_path)
