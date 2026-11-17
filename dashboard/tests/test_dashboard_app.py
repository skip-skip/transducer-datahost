import panel as pn
import pytest
from fastapi.testclient import TestClient

from transducer_dashboard.app import create_app
from transducer_dashboard.auth import COOKIE_NAME, SessionCodec
from transducer_dashboard.panel_app import build_placeholder
from transducer_dashboard.read_client import ReadApiError
from transducer_dashboard.settings import DashboardSettings


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


def html_text(view: pn.Column) -> str:
    return "\n".join(
        obj.object for obj in view.objects if isinstance(obj, pn.pane.HTML)
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
    response = client.get("/")

    assert response.status_code == 200
    assert "<title>Transducer dashboard</title>" in response.text
    assert "static/js/bokeh.min.js" in response.text
    assert "static/extensions/panel/panel.min.js" in response.text
    assert "cdn.jsdelivr" not in response.text
    assert "unpkg.com" not in response.text


def test_panel_serves_its_own_static_assets(client: TestClient) -> None:
    for path in ("/static/js/bokeh.min.js", "/static/extensions/panel/panel.min.js"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith("application/javascript")
        assert response.content


def test_security_headers_wrap_panel_responses(client: TestClient) -> None:
    response = client.get("/")

    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "same-origin"
    csp = response.headers["Content-Security-Policy"]
    assert "script-src 'self' 'unsafe-inline'" in csp
    assert "style-src 'self' 'unsafe-inline'" in csp
    assert "connect-src 'self' ws: wss:" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-eval" not in csp


def test_root_path_is_preserved_for_login_and_panel(tmp_path) -> None:
    with make_client(tmp_path, root_path="/dashboard") as test_client:
        page = test_client.get("/")
        assert page.status_code == 200
        assert "<title>Transducer dashboard</title>" in page.text

        login_page = test_client.get("/login")
        assert login_page.status_code == 200

        response = login(test_client)
        assert response.status_code == 303
        assert response.headers["location"] == "/dashboard/"
        assert "Path=/dashboard" in response.headers["set-cookie"]

        logout = test_client.post("/logout", follow_redirects=False)
        assert logout.headers["location"].endswith("/dashboard/login")


def test_placeholder_shows_sign_in_without_a_session() -> None:
    codec = SessionCodec("x" * 32, 60)
    view = build_placeholder({}, codec)

    assert "Sign in" in html_text(view)


def test_placeholder_shows_the_signed_in_label() -> None:
    codec = SessionCodec("x" * 32, 60)
    view = build_placeholder({COOKIE_NAME: codec.dump("alice", "good")}, codec)

    text = html_text(view)
    assert "Signed in as" in text
    assert "alice" in text


def test_placeholder_escapes_the_label() -> None:
    codec = SessionCodec("x" * 32, 60)
    view = build_placeholder(
        {COOKIE_NAME: codec.dump("<script>alert(1)</script>", "good")}, codec
    )

    text = html_text(view)
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_placeholder_rejects_an_invalid_cookie() -> None:
    codec = SessionCodec("x" * 32, 60)
    view = build_placeholder({COOKIE_NAME: "not-a-signed-cookie"}, codec)

    assert "Sign in" in html_text(view)
