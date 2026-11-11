"""FastAPI + HTMX dashboard over the read-only transducer API.

The service holds no database credentials. It authenticates the browser with a
read API token, forwards that token on every read API call, and persists only
its own layout state as a JSON file on a Docker volume.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import figures as figure_ops
from .figures import SCOPES, FigureError, FigureNotFound, FigurePermissionError
from .read_client import ReadApiError, ReadClient
from .settings import DashboardSettings, SettingsError
from .state import StateStore

COOKIE_NAME = "transducer_dashboard"
COOKIE_SALT = "transducer-dashboard-session"
RANGES = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
DEFAULT_RANGE = "7d"


class NotAuthenticated(Exception):
    pass


@dataclass(frozen=True)
class Session:
    label: str
    token: str


def _default_range(settings: DashboardSettings) -> str:
    if settings.default_window_days <= 1:
        return "24h"
    if settings.default_window_days <= 7:
        return "7d"
    return "30d"


def _names_from_locations(rows: list[dict[str, Any]]) -> dict[int, str]:
    """Map ``location_id`` to name from a ``GET /locations`` response."""
    names: dict[int, str] = {}
    for row in rows:
        raw = row.get("location_id") if isinstance(row, dict) else None
        if isinstance(raw, int):
            name = row.get("name")
            names[raw] = name if isinstance(name, str) else ""
    return names


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

    serializer = URLSafeTimedSerializer(settings.secret_key, salt=COOKIE_SALT)

    package_dir = Path(__file__).parent
    templates = Jinja2Templates(directory=str(package_dir / "templates"))

    app = FastAPI(
        title="Transducer dashboard",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.mount(
        "/static",
        StaticFiles(directory=str(package_dir / "static")),
        name="static",
    )

    def cookie_path(request: Request) -> str:
        return request.scope.get("root_path") or "/"

    def read_session(request: Request) -> Session | None:
        raw = request.cookies.get(COOKIE_NAME)
        if not raw:
            return None
        try:
            payload = serializer.loads(raw, max_age=settings.session_max_age)
        except (BadSignature, SignatureExpired):
            return None
        if not isinstance(payload, dict):
            return None
        label = payload.get("label")
        token = payload.get("token")
        if not isinstance(label, str) or not label:
            return None
        if not isinstance(token, str) or not token:
            return None
        return Session(label=label, token=token)

    def require_session(request: Request) -> Session:
        session = read_session(request)
        if session is None:
            raise NotAuthenticated()
        return session

    @app.exception_handler(NotAuthenticated)
    async def _not_authenticated(request: Request, _exc: NotAuthenticated) -> Response:
        response = RedirectResponse(request.url_for("login_page"), status_code=303)
        response.delete_cookie(COOKIE_NAME, path=cookie_path(request))
        return response

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
        )
        return response

    def redirect(request: Request, endpoint: str = "index") -> RedirectResponse:
        return RedirectResponse(request.url_for(endpoint), status_code=303)

    def page_context(request: Request, session: Session, **extra: Any) -> dict[str, Any]:
        context = {
            "request": request,
            "session": session,
            "shared_editors": settings.shared_editors,
            "ranges": list(RANGES),
        }
        context.update(extra)
        return context

    def chart_payload(
        session: Session,
        series: dict[str, Any],
        range_key: str,
        location_id: int | None = None,
        location_names: dict[int, str] | None = None,
    ) -> dict[str, Any]:
        end = datetime.now(ZoneInfo(settings.site_timezone)).replace(
            tzinfo=None, microsecond=0
        )
        start = end - RANGES[range_key]
        points = client.points(
            session.token,
            int(series["series_id"]),
            start=start.isoformat(),
            end=end.isoformat(),
            points=settings.max_points,
            tz="local",
            location_id=location_id,
        )
        # The label names the location and the logger so a chart stays
        # attributable when a location has hosted several instruments.
        segments: list[str] = []
        if location_id is not None:
            name = (location_names or {}).get(location_id)
            if name:
                segments.append(name)
        if series.get("serial_number"):
            segments.append(str(series["serial_number"]))
        segments.append(f"{series.get('identification', 'value')} ({series.get('unit', '')})")
        label = " · ".join(segments)
        return {
            "series_id": series["series_id"],
            "location_id": location_id,
            "label": label,
            "unit": series.get("unit", ""),
            "range": range_key,
            "points": points,
        }

    def location_names(session: Session) -> dict[int, str]:
        """Names for chart labels; tolerant of errors other than dead sessions."""
        try:
            rows = client.locations(session.token)
        except ReadApiError as exc:
            api_failure(exc)  # raises only for a dead session; otherwise no names
            return {}
        return _names_from_locations(rows)

    def api_failure(exc: ReadApiError) -> str:
        if exc.status_code == 401:
            raise NotAuthenticated()
        return exc.detail

    # -- authentication ----------------------------------------------------

    @app.get("/login", name="login_page", response_class=HTMLResponse)
    def login_page(request: Request) -> Response:
        if read_session(request) is not None:
            return redirect(request)
        return templates.TemplateResponse(
            request, "login.html", page_context(request, Session("", ""), error=None)
        )

    @app.post("/login", name="login")
    def login(request: Request, token: str = Form("")) -> Response:
        token = token.strip()
        if not token:
            return templates.TemplateResponse(
                request,
                "login.html",
                page_context(request, Session("", ""), error="Enter a read API token."),
                status_code=400,
            )
        try:
            label = client.whoami(token)
        except ReadApiError as exc:
            status = 401 if exc.status_code == 401 else 502
            return templates.TemplateResponse(
                request,
                "login.html",
                page_context(request, Session("", ""), error=exc.detail),
                status_code=status,
            )

        response = redirect(request)
        response.set_cookie(
            COOKIE_NAME,
            serializer.dumps({"label": label, "token": token}),
            max_age=settings.session_max_age,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="strict",
            path=cookie_path(request),
        )
        return response

    @app.post("/logout", name="logout")
    def logout(request: Request) -> Response:
        response = redirect(request, "login_page")
        response.delete_cookie(COOKIE_NAME, path=cookie_path(request))
        return response

    # -- pages -------------------------------------------------------------

    @app.get("/health", name="health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", name="index", response_class=HTMLResponse)
    def index(
        request: Request,
        session: Session = Depends(require_session),
        range: str = Query(""),
    ) -> Response:
        state = store.load()
        ui = figure_ops.get_ui(state, session.label)
        scope = request.query_params.get("scope") or ui.get("view") or "personal"
        if scope not in SCOPES:
            scope = "personal"

        figures = figure_ops.list_figures(state, scope, session.label)
        selected_id = ui[scope].get("selected_figure_id")
        figure = None
        if selected_id:
            figure = next(
                (item for item in figures if item.get("id") == selected_id), None
            )

        range_key = range if range in RANGES else _default_range(settings)
        locations: list[dict[str, Any]] = []
        analytes: list[dict[str, Any]] = []
        chart: dict[str, Any] | None = None
        error: str | None = None

        try:
            locations = client.locations(session.token)
            figure_location = figure.get("location_id") if figure else None
            if figure_location is not None:
                # Strict scoping: only series with readings recorded at the
                # bound location, so the selector matches what the chart can
                # show for a logger that has also served other wells.
                analytes = client.series_at_location(
                    session.token, int(figure_location)
                )
            if figure and figure.get("series_id"):
                series_detail = client.series_detail(
                    session.token, int(figure["series_id"])
                )
                if figure_location is None:
                    # Figures saved before location scoping (and deliberately
                    # un-scoped ones) keep the whole series selectable; the
                    # chart then spans every location the logger served.
                    analytes = [series_detail]
                chart = chart_payload(
                    session,
                    series_detail,
                    range_key,
                    location_id=figure_location,
                    location_names=_names_from_locations(locations),
                )
        except ReadApiError as exc:
            error = api_failure(exc)

        return templates.TemplateResponse(
            request,
            "index.html",
            page_context(
                request,
                session,
                scope=scope,
                figures=figures,
                figure=figure,
                ui=ui,
                locations=locations,
                analytes=analytes,
                chart=chart,
                range_key=range_key,
                can_edit=figure_ops.can_edit(
                    scope, session.label, settings.shared_editors
                ),
                error=error,
            ),
        )

    # -- HTMX partials -----------------------------------------------------

    @app.get("/partials/analytes", name="analytes_partial", response_class=HTMLResponse)
    def analytes_partial(
        request: Request,
        location_id: str = Query(""),
        selected: int | None = Query(None),
        session: Session = Depends(require_session),
    ) -> Response:
        options: list[dict[str, Any]] = []
        location_chosen = False
        error: str | None = None
        if location_id.strip():
            location_chosen = True
            try:
                parsed = int(location_id)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail="invalid location"
                ) from exc
            try:
                options = client.series_at_location(session.token, parsed)
            except ReadApiError as exc:
                error = api_failure(exc)
        else:
            # "All locations": offer every series, matching un-scoped figures.
            try:
                options = client.series(session.token)
            except ReadApiError as exc:
                error = api_failure(exc)
        return templates.TemplateResponse(
            request,
            "_analytes.html",
            {
                "request": request,
                "analytes": options,
                "selected_series_id": selected,
                "location_chosen": location_chosen,
                "error": error,
            },
        )

    @app.get("/partials/chart", name="chart_partial", response_class=HTMLResponse)
    def chart_partial(
        request: Request,
        figure_id: str = Query(...),
        scope: str = Query("personal"),
        range: str = Query(DEFAULT_RANGE),
        session: Session = Depends(require_session),
    ) -> Response:
        state = store.load()
        figure = figure_ops.get_figure(state, scope, session.label, figure_id)
        if figure is None:
            raise HTTPException(status_code=404, detail="figure not found")

        range_key = range if range in RANGES else _default_range(settings)
        chart: dict[str, Any] | None = None
        error: str | None = None
        if figure.get("series_id"):
            figure_location = figure.get("location_id")
            try:
                series_detail = client.series_detail(
                    session.token, int(figure["series_id"])
                )
                chart = chart_payload(
                    session,
                    series_detail,
                    range_key,
                    location_id=figure_location,
                    location_names=(
                        location_names(session)
                        if figure_location is not None
                        else None
                    ),
                )
            except ReadApiError as exc:
                error = api_failure(exc)
        return templates.TemplateResponse(
            request,
            "_chart.html",
            {
                "request": request,
                "chart": chart,
                "error": error,
                "figure": figure,
            },
        )

    # -- figure mutations --------------------------------------------------

    @app.post("/figures", name="create_figure")
    def create_figure(
        request: Request,
        scope: str = Form("personal"),
        session: Session = Depends(require_session),
    ) -> Response:
        if scope not in SCOPES:
            raise HTTPException(status_code=400, detail="unknown scope")
        try:
            store.mutate(
                lambda state: figure_ops.create_figure(
                    state, scope, session.label, settings.shared_editors
                )
            )
        except FigurePermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return redirect(request)

    @app.post("/figures/{scope}/{figure_id}", name="update_figure")
    def update_figure(
        request: Request,
        scope: str,
        figure_id: str,
        name: str = Form(""),
        series_id: str = Form(""),
        location_id: str = Form(""),
        session: Session = Depends(require_session),
    ) -> Response:
        if scope not in SCOPES:
            raise HTTPException(status_code=400, detail="unknown scope")
        cleaned = name.strip()
        if not cleaned:
            raise HTTPException(status_code=400, detail="name must not be blank")
        parsed_series: int | None
        try:
            parsed_series = int(series_id) if series_id.strip() else None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid series") from exc
        parsed_location: int | None
        try:
            parsed_location = int(location_id) if location_id.strip() else None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid location") from exc

        # Reject binding to a series that does not exist so a stale figure
        # cannot silently render an empty chart forever.
        if parsed_series is not None:
            try:
                client.series_detail(session.token, parsed_series)
            except ReadApiError as exc:
                if exc.status_code == 404:
                    raise HTTPException(
                        status_code=400, detail="selected series does not exist"
                    ) from exc
                api_failure(exc)

        # A location-scoped figure must pair a series that actually has
        # readings recorded at that location; otherwise the chart would
        # render permanently empty for a logger that never served the well.
        if parsed_location is not None:
            try:
                at_location = client.series_at_location(
                    session.token, parsed_location
                )
            except ReadApiError as exc:
                if exc.status_code == 404:
                    raise HTTPException(
                        status_code=400, detail="selected location does not exist"
                    ) from exc
                api_failure(exc)
            if parsed_series is not None and parsed_series not in {
                item.get("series_id") for item in at_location
            }:
                raise HTTPException(
                    status_code=400,
                    detail="selected series has no readings at this location",
                )

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            existing = figure_ops.get_figure(state, scope, session.label, figure_id)
            config = dict(existing.get("config") or {}) if existing else {}
            return figure_ops.update_figure(
                state,
                scope,
                session.label,
                settings.shared_editors,
                figure_id,
                name=cleaned,
                series_id=parsed_series,
                location_id=parsed_location,
                config=config,
            )

        try:
            store.mutate(operation)
        except FigureNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FigurePermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FigureError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return redirect(request)

    @app.post("/figures/{scope}/{figure_id}/delete", name="delete_figure")
    def delete_figure(
        request: Request,
        scope: str,
        figure_id: str,
        session: Session = Depends(require_session),
    ) -> Response:
        if scope not in SCOPES:
            raise HTTPException(status_code=400, detail="unknown scope")
        try:
            store.mutate(
                lambda state: figure_ops.delete_figure(
                    state,
                    scope,
                    session.label,
                    settings.shared_editors,
                    figure_id,
                )
            )
        except FigureNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FigurePermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return redirect(request)

    # -- persisted UI state ------------------------------------------------

    @app.post("/ui/view", name="set_view")
    def set_view(
        request: Request,
        scope: str = Form("personal"),
        session: Session = Depends(require_session),
    ) -> Response:
        if scope not in SCOPES:
            raise HTTPException(status_code=400, detail="unknown scope")
        store.mutate(
            lambda state: figure_ops.set_ui(state, session.label, view=scope)
        )
        return redirect(request)

    @app.post("/ui/select", name="select_figure")
    def select_figure(
        request: Request,
        scope: str = Form("personal"),
        figure_id: str = Form(""),
        session: Session = Depends(require_session),
    ) -> Response:
        if scope not in SCOPES:
            raise HTTPException(status_code=400, detail="unknown scope")

        def operation(state: dict[str, Any]) -> None:
            exists = bool(figure_id) and (
                figure_ops.get_figure(state, scope, session.label, figure_id)
                is not None
            )
            figure_ops.set_ui(
                state,
                session.label,
                view=scope,
                **{scope: {"selected_figure_id": figure_id if exists else None}},
            )

        store.mutate(operation)
        return redirect(request)

    @app.post("/ui/toggle-folders", name="toggle_folders")
    def toggle_folders(
        request: Request,
        scope: str = Form(""),
        session: Session = Depends(require_session),
    ) -> Response:
        def operation(state: dict[str, Any]) -> None:
            ui = figure_ops.get_ui(state, session.label)
            target = scope if scope in SCOPES else ui.get("view", "personal")
            if target not in SCOPES:
                target = "personal"
            collapsed = bool(ui[target].get("folders_collapsed"))
            figure_ops.set_ui(
                state,
                session.label,
                **{target: {"folders_collapsed": not collapsed}},
            )

        store.mutate(operation)
        return redirect(request)

    app.state.settings = settings
    app.state.read_client = client
    app.state.store = store
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
