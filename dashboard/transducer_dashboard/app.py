"""FastAPI service hosting the Panel dashboard over the read-only transducer API.

The service holds no database credentials. It authenticates the browser with a
read API token, forwards that token on every read API call, and persists only
its own layout state as a JSON file on a Docker volume.

FastAPI owns login, logout, health, and the security headers. The dashboard UI is
a HoloViz Panel application mounted at the root of the same process.
"""

from __future__ import annotations

import html
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from panel.io.fastapi import add_applications

from .auth import COOKIE_NAME, Session, SessionCodec
from .panel_app import create_panel_app
from .read_client import ReadApiError, ReadClient
from .settings import DashboardSettings, SettingsError
from .state import StateStore

CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "worker-src 'self' blob:; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


def _login_page(error: str | None) -> str:
    alert = ""
    if error:
        alert = f'<p class="error" role="alert">{html.escape(error)}</p>'
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sign in · Transducer dashboard</title>
  <style>
    body {{ margin: 0; font-family: system-ui, sans-serif; background: #f5f6f8; color: #1d2433; }}
    main {{ max-width: 26rem; margin: 12vh auto; padding: 2rem; background: #fff;
            border: 1px solid #d8dde6; border-radius: 0.5rem; }}
    h1 {{ margin-top: 0; font-size: 1.4rem; }}
    label {{ display: block; margin-bottom: 0.4rem; font-weight: 600; }}
    input {{ box-sizing: border-box; width: 100%; padding: 0.6rem; font: inherit;
             border: 1px solid #9aa4b5; border-radius: 0.3rem; }}
    button {{ margin-top: 1rem; padding: 0.6rem 1.2rem; font: inherit; cursor: pointer; }}
    .error {{ color: #a11616; }}
  </style>
</head>
<body>
  <main>
    <h1>Transducer dashboard</h1>
    <p>Sign in with a read API token.</p>
    {alert}
    <form method="post" action="login">
      <label for="token">Read API token</label>
      <input id="token" name="token" type="password" autocomplete="off" required autofocus>
      <button type="submit">Sign in</button>
    </form>
  </main>
</body>
</html>"""


def _dashboard_url(request: Request) -> str:
    root_path = (request.scope.get("root_path") or "").rstrip("/")
    return f"{root_path}/"


def create_app(
    settings: DashboardSettings | None = None,
    read_client: ReadClient | None = None,
) -> FastAPI:
    settings = settings or DashboardSettings.from_env()
    client = read_client or ReadClient(
        settings.read_api_url, timeout=settings.request_timeout
    )
    store = StateStore(settings.state_dir)
    store.ensure()

    codec = SessionCodec(settings.secret_key, settings.session_max_age)

    app = FastAPI(
        title="Transducer dashboard",
        version="2.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    # Panel is mounted before the security middleware so that the middleware
    # wraps both FastAPI and Panel responses. FastAPI routes declared below take
    # precedence over the Panel application for paths such as /login and
    # /health.
    add_applications(
        {"/": create_panel_app(settings, codec)},
        app=app,
        title="Transducer dashboard",
        location=False,
    )

    def cookie_path(request: Request) -> str:
        return request.scope.get("root_path") or "/"

    def read_session(request: Request) -> Session | None:
        return codec.from_cookies(request.cookies)

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Content-Security-Policy", CSP)
        return response

    # -- authentication ----------------------------------------------------

    @app.get("/login", name="login_page", response_class=HTMLResponse)
    def login_page(request: Request) -> Response:
        if read_session(request) is not None:
            return RedirectResponse(_dashboard_url(request), status_code=303)
        return HTMLResponse(_login_page(None))

    @app.post("/login", name="login")
    def login(request: Request, token: str = Form("")) -> Response:
        token = token.strip()
        if not token:
            return HTMLResponse(
                _login_page("Enter a read API token."), status_code=400
            )
        try:
            label = client.whoami(token)
        except ReadApiError as exc:
            status = 401 if exc.status_code == 401 else 502
            return HTMLResponse(_login_page(exc.detail), status_code=status)

        response = RedirectResponse(_dashboard_url(request), status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            codec.dump(label, token),
            max_age=settings.session_max_age,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="strict",
            path=cookie_path(request),
        )
        return response

    @app.post("/logout", name="logout")
    def logout(request: Request) -> Response:
        response = RedirectResponse(request.url_for("login_page"), status_code=303)
        response.delete_cookie(COOKIE_NAME, path=cookie_path(request))
        return response

    # -- health ------------------------------------------------------------

    @app.get("/health", name="health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.state.settings = settings
    app.state.read_client = client
    app.state.store = store
    app.state.session_codec = codec
    return app


def main() -> int:
    import uvicorn

    try:
        DashboardSettings.from_env()
    except SettingsError as exc:
        raise SystemExit(str(exc)) from exc

    uvicorn.run(
        "transducer_dashboard.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=8080,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
