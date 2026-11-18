import panel as pn
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from transducer_dashboard.app import CSP_PANEL, CSP_STRICT, _csp_for, create_app
from transducer_dashboard.auth import COOKIE_NAME, SessionCodec
from transducer_dashboard.panel_app import build_view
from transducer_dashboard.read_client import ReadApiError
from transducer_dashboard.settings import DashboardSettings
from transducer_dashboard.state import StateStore


class FakeReadClient:
    def whoami(self, token: str) -> str:
        labels = {"good": "alice", "viewer": "bob"}
        if token not in labels:
            raise ReadApiError(401, "invalid or expired token")
        return labels[token]


def make_settings(tmp_path) -> DashboardSettings:
    return DashboardSettings(
        read_api_url="http://read-api:8080",
        secret_key="x" * 32,
        state_dir=tmp_path,
        cookie_secure=False,
    )


def make_client(tmp_path, root_path: str = "") -> TestClient:
    app = create_app(make_settings(tmp_path), FakeReadClient())
    return TestClient(app, root_path=root_path)


@pytest.fixture
def client(tmp_path):
    with make_client(tmp_path) as test_client:
        yield test_client


def login(test_client: TestClient, token: str = "good"):
    return test_client.post(
        "/login", data={"token": token}, follow_redirects=False
    )


def walk(view):
    yield view
    for child in getattr(view, "objects", None) or []:
        yield from walk(child)


def html_text(view) -> str:
    return "\n".join(
        obj.object for obj in walk(view) if isinstance(obj, pn.pane.HTML)
    )


def button_labels(view) -> list[str]:
    return [obj.label for obj in walk(view) if isinstance(obj, pn.widgets.Button)]


def make_store(tmp_path) -> StateStore:
    store = StateStore(tmp_path)
    store.ensure()
    return store


def build(tmp_path, cookies: dict[str, str]):
    codec = SessionCodec("x" * 32, 60)
    return build_view(
        cookies,
        codec,
        make_store(tmp_path),
        FakeReadClient(),
        make_settings(tmp_path),
    )


def test_health_is_public(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_login_page_renders(client: TestClient) -> None:
    response = client.get("/login")

    assert response.status_code == 200
    assert "Read API token" in response.text


def test_login_rejects_bad_token(client: TestClient) -> None:
    response = login(client, "bad")

    assert response.status_code == 401
    assert "invalid or expired token" in response.text


def test_login_sets_hardened_cookie(client: TestClient) -> None:
    response = login(client)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE_NAME}=")
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/" in cookie


def test_login_page_redirects_an_existing_session(client: TestClient) -> None:
    login(client)

    response = client.get("/login", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_logout_clears_the_cookie(client: TestClient) -> None:
    login(client)

    response = client.post("/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].endswith("/login")
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f'{COOKIE_NAME}=""')
    assert "Path=/" in cookie


def test_root_serves_the_panel_shell(client: TestClient) -> None:
    login(client)
    response = client.get("/")

    assert response.status_code == 200
    assert "<title>Transducer dashboard</title>" in response.text
    assert "static/js/bokeh.min.js" in response.text
    assert "static/extensions/panel/panel.min.js" in response.text
    assert "static/extensions/panel/bundled/gridstack/" in response.text
    assert "gridstack-all.js" in response.text
    assert "cdn.jsdelivr" not in response.text
    assert "unpkg.com" not in response.text


def test_panel_serves_its_own_static_assets(client: TestClient) -> None:
    for path in ("/static/js/bokeh.min.js", "/static/extensions/panel/panel.min.js"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith("application/javascript")
        assert response.content
        # Static assets share the Panel policy; the strict policy starts at
        # the routes that are not part of the mounted application.
        script_src = _directive(
            response.headers["Content-Security-Policy"], "script-src"
        )
        assert "'unsafe-eval'" in script_src


def _directive(csp: str, name: str) -> str:
    for directive in csp.split(";"):
        directive = directive.strip()
        if directive.startswith(f"{name} "):
            return directive[len(name) + 1 :]
    raise AssertionError(f"{name} missing from {csp}")


@pytest.mark.parametrize(
    ("path", "root_path", "relaxed"),
    [
        ("/", "", True),
        ("/static/js/bokeh.min.js", "", True),
        # Production shape: the proxy strips the prefix and uvicorn prepends
        # --root-path to scope["path"] again, so the path arrives prefixed.
        ("/dashboard/", "/dashboard", True),
        ("/dashboard/static/js/bokeh.min.js", "/dashboard", True),
        ("/dashboard", "/dashboard", True),
        ("/login", "", False),
        ("/dashboard/login", "/dashboard", False),
        ("/dashboard/logout", "/dashboard", False),
        ("/dashboard/health", "/dashboard", False),
    ],
)
def test_csp_is_chosen_from_the_route_path(
    path: str, root_path: str, relaxed: bool
) -> None:
    request = Request({"type": "http", "path": path, "root_path": root_path})

    assert _csp_for(request) == (CSP_PANEL if relaxed else CSP_STRICT)


def test_security_headers_wrap_panel_responses(client: TestClient) -> None:
    response = client.get("/")

    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "same-origin"
    csp = response.headers["Content-Security-Policy"]
    script_src = _directive(csp, "script-src")
    assert "'self'" in script_src
    assert "'unsafe-inline'" in script_src
    assert "'unsafe-eval'" in script_src
    assert "blob:" in script_src
    assert _directive(csp, "style-src") == "'self' 'unsafe-inline'"
    assert _directive(csp, "connect-src") == "'self' ws: wss:"
    assert _directive(csp, "worker-src") == "'self' blob:"
    assert _directive(csp, "frame-ancestors") == "'none'"
    assert _directive(csp, "object-src") == "'none'"


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/login"), ("POST", "/logout"), ("GET", "/health")],
)
def test_strict_csp_covers_the_non_panel_routes(
    client: TestClient, method: str, path: str
) -> None:
    response = client.request(method, path, follow_redirects=False)

    script_src = _directive(response.headers["Content-Security-Policy"], "script-src")
    assert "'self'" in script_src
    assert "'unsafe-inline'" in script_src
    assert "'unsafe-eval'" not in script_src
    assert "blob:" not in script_src


def test_root_path_is_preserved_for_login_and_panel(tmp_path) -> None:
    # TestClient sets scope["root_path"] without prefixing scope["path"], so this
    # only covers the unprefixed shape. test_csp_is_chosen_from_the_route_path
    # covers the prefixed shape that uvicorn produces in production.
    with make_client(tmp_path, root_path="/dashboard") as test_client:
        page = test_client.get("/")
        assert page.status_code == 200
        assert "<title>Transducer dashboard</title>" in page.text
        assert "'unsafe-eval'" in _directive(
            page.headers["Content-Security-Policy"], "script-src"
        )

        login_page = test_client.get("/login")
        assert login_page.status_code == 200
        assert "'unsafe-eval'" not in _directive(
            login_page.headers["Content-Security-Policy"], "script-src"
        )

        response = login(test_client)
        assert response.status_code == 303
        assert response.headers["location"] == "/dashboard/"
        assert "Path=/dashboard" in response.headers["set-cookie"]

        logout = test_client.post("/logout", follow_redirects=False)
        assert logout.headers["location"].endswith("/dashboard/login")


def test_build_view_shows_sign_in_without_a_session(tmp_path) -> None:
    view = build(tmp_path, {})

    assert "Sign in" in html_text(view)


def test_build_view_shows_the_scene_shell_for_a_session(tmp_path) -> None:
    codec = SessionCodec("x" * 32, 60)
    view = build(tmp_path, {COOKIE_NAME: codec.dump("alice", "good")})

    text = html_text(view)
    assert "Signed in as" in text
    assert "alice" in text
    assert "+ New scene" in button_labels(view)
    assert "No scenes yet" in text


def test_build_view_escapes_the_session_label(tmp_path) -> None:
    codec = SessionCodec("x" * 32, 60)
    view = build(
        tmp_path,
        {COOKIE_NAME: codec.dump("<script>alert(1)</script>", "good")},
    )

    text = html_text(view)
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_build_view_rejects_an_invalid_cookie(tmp_path) -> None:
    view = build(tmp_path, {COOKIE_NAME: "not-a-signed-cookie"})

    assert "Sign in" in html_text(view)
