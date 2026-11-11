import json

import pytest
from fastapi.testclient import TestClient

from transducer_dashboard.app import create_app
from transducer_dashboard.read_client import ReadApiError
from transducer_dashboard.settings import DashboardSettings

LOGGERS = [
    {
        "serial_number": "1096043",
        "instrument_type": "L5_LTC",
        "model_number": "LTC",
        "series_count": 1,
    }
]
LOCATIONS = [
    {
        "location_id": 7,
        "name": "MCMW-04",
        "base_location": None,
        "is_aquifer_test": False,
        "latitude": None,
        "longitude": None,
        "timezone": "America/New_York",
    }
]
SERIES = [
    {
        "series_id": 42,
        "serial_number": "1096043",
        "channel_no": 1,
        "identification": "LEVEL",
        "unit": "m",
        "data_kind": "uncompensated",
        "reading_count": 3,
        "first_reading": "2026-01-01T00:00:00",
        "last_reading": "2026-01-01T00:10:00",
    }
]
DETAIL = {
    "series_id": 42,
    "serial_number": "1096043",
    "channel_no": 1,
    "identification": "LEVEL",
    "unit": "m",
    "data_kind": "uncompensated",
    "reading_count": 3,
    "first_reading": "2026-01-01T00:00:00",
    "last_reading": "2026-01-01T00:10:00",
}
POINTS = [
    {
        "ts": "2026-01-01T00:00:00",
        "ts_utc": "2026-01-01T05:00:00+00:00",
        "value": 1.25,
        "min_value": 1.2,
        "max_value": 1.3,
        "n": 2,
    }
]


class FakeReadClient:
    def __init__(self) -> None:
        self.points_calls: list[dict[str, object]] = []

    def whoami(self, token: str) -> str:
        labels = {"good": "alice", "viewer": "bob"}
        if token not in labels:
            raise ReadApiError(401, "invalid or expired token")
        return labels[token]

    def loggers(self, token: str):
        self._check(token)
        return LOGGERS

    def locations(self, token: str):
        self._check(token)
        return LOCATIONS

    def series(self, token: str, *, serial=None, limit=1000):
        self._check(token)
        if serial is None or serial == "1096043":
            return SERIES
        return []

    def series_at_location(self, token: str, location_id: int, *, limit=1000):
        self._check(token)
        if location_id == 7:
            return SERIES
        if location_id == 999:
            raise ReadApiError(404, "not found")
        return []

    def series_detail(self, token: str, series_id: int):
        self._check(token)
        if series_id != 42:
            raise ReadApiError(404, "not found")
        return DETAIL

    def points(
        self,
        token: str,
        series_id: int,
        *,
        start,
        end,
        points,
        tz="local",
        location_id=None,
    ):
        self._check(token)
        self.points_calls.append(
            {"series_id": series_id, "location_id": location_id}
        )
        return POINTS

    @staticmethod
    def _check(token: str) -> None:
        if token not in {"good", "viewer"}:
            raise ReadApiError(401, "invalid or expired token")


def make_settings(tmp_path, shared_editors=frozenset()) -> DashboardSettings:
    return DashboardSettings(
        read_api_url="http://read-api:8080",
        secret_key="x" * 32,
        state_dir=tmp_path,
        shared_editors=shared_editors,
        cookie_secure=False,
    )


@pytest.fixture
def client(tmp_path):
    app = create_app(make_settings(tmp_path), FakeReadClient())
    with TestClient(app) as test_client:
        yield test_client


def login(test_client: TestClient, token: str = "good"):
    return test_client.post(
        "/login", data={"token": token}, follow_redirects=False
    )


def read_state(tmp_path):
    return json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))


def test_health_is_public(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_static_assets_are_served(client: TestClient) -> None:
    for path in ("app.css", "app.js", "vendor/htmx.min.js", "vendor/plotly.min.js"):
        response = client.get(f"/static/{path}")
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith("text/") or path.endswith(
            ".js"
        )


def test_index_redirects_to_login_without_session(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].endswith("/login")


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
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/" in cookie


def test_authenticated_index_renders(client: TestClient) -> None:
    login(client)

    response = client.get("/")

    assert response.status_code == 200
    assert "No figure selected" in response.text
    assert "alice" in response.text


def test_create_update_and_delete_figure(client: TestClient, tmp_path) -> None:
    login(client)

    created = client.post(
        "/figures", data={"scope": "personal"}, follow_redirects=False
    )
    assert created.status_code == 303

    state = read_state(tmp_path)
    figures = state["personal"]["alice"]["figures"]
    assert len(figures) == 1
    figure_id = figures[0]["id"]
    assert figures[0]["name"] == "Untitled figure"

    updated = client.post(
        f"/figures/personal/{figure_id}",
        data={"name": "GC-1980 water level", "series_id": "42", "location_id": "7"},
        follow_redirects=False,
    )
    assert updated.status_code == 303

    state = read_state(tmp_path)
    figure = state["personal"]["alice"]["figures"][0]
    assert figure["name"] == "GC-1980 water level"
    assert figure["series_id"] == 42
    assert figure["location_id"] == 7

    page = client.get("/")
    assert page.status_code == 200
    assert "GC-1980 water level" in page.text
    assert "chart-data" in page.text
    assert "MCMW-04" in page.text
    assert "LEVEL (m)" in page.text

    deleted = client.post(
        f"/figures/personal/{figure_id}/delete", follow_redirects=False
    )
    assert deleted.status_code == 303
    assert read_state(tmp_path)["personal"]["alice"]["figures"] == []


def test_location_scoped_chart_requests_location_points(tmp_path) -> None:
    fake = FakeReadClient()
    app = create_app(make_settings(tmp_path), fake)
    with TestClient(app) as test_client:
        login(test_client)
        test_client.post("/figures", data={"scope": "personal"}, follow_redirects=False)
        figure_id = read_state(tmp_path)["personal"]["alice"]["figures"][0]["id"]
        saved = test_client.post(
            f"/figures/personal/{figure_id}",
            data={"name": "MCMW-04 level", "series_id": "42", "location_id": "7"},
            follow_redirects=False,
        )
        assert saved.status_code == 303

        page = test_client.get("/")
        assert page.status_code == 200
        assert "chart-data" in page.text

        assert fake.points_calls, "the chart render should request points"
        assert all(call["location_id"] == 7 for call in fake.points_calls)


def test_legacy_figure_without_location_charts_whole_series(
    client: TestClient, tmp_path
) -> None:
    login(client)
    client.post("/figures", data={"scope": "personal"}, follow_redirects=False)
    figure_id = read_state(tmp_path)["personal"]["alice"]["figures"][0]["id"]

    saved = client.post(
        f"/figures/personal/{figure_id}",
        data={"name": "Legacy", "series_id": "42"},
        follow_redirects=False,
    )
    assert saved.status_code == 303

    figure = read_state(tmp_path)["personal"]["alice"]["figures"][0]
    assert figure["series_id"] == 42
    assert figure.get("location_id") is None

    page = client.get("/")
    assert page.status_code == 200
    assert "All locations" in page.text
    assert "chart-data" in page.text


def test_update_rejects_blank_name(client: TestClient, tmp_path) -> None:
    login(client)
    client.post("/figures", data={"scope": "personal"}, follow_redirects=False)
    figure_id = read_state(tmp_path)["personal"]["alice"]["figures"][0]["id"]

    response = client.post(
        f"/figures/personal/{figure_id}",
        data={"name": "   ", "series_id": ""},
        follow_redirects=False,
    )

    assert response.status_code == 400


def test_update_rejects_unknown_series(client: TestClient, tmp_path) -> None:
    login(client)
    client.post("/figures", data={"scope": "personal"}, follow_redirects=False)
    figure_id = read_state(tmp_path)["personal"]["alice"]["figures"][0]["id"]

    response = client.post(
        f"/figures/personal/{figure_id}",
        data={"name": "Broken", "series_id": "999"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "does not exist" in response.text


def test_update_rejects_series_not_at_location(client: TestClient, tmp_path) -> None:
    login(client)
    client.post("/figures", data={"scope": "personal"}, follow_redirects=False)
    figure_id = read_state(tmp_path)["personal"]["alice"]["figures"][0]["id"]

    response = client.post(
        f"/figures/personal/{figure_id}",
        data={"name": "Broken", "series_id": "42", "location_id": "8"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "no readings at this location" in response.text


def test_update_rejects_unknown_location(client: TestClient, tmp_path) -> None:
    login(client)
    client.post("/figures", data={"scope": "personal"}, follow_redirects=False)
    figure_id = read_state(tmp_path)["personal"]["alice"]["figures"][0]["id"]

    response = client.post(
        f"/figures/personal/{figure_id}",
        data={"name": "Broken", "series_id": "42", "location_id": "999"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "location does not exist" in response.text


def test_shared_figure_permissions(tmp_path) -> None:
    app = create_app(
        make_settings(tmp_path, shared_editors=frozenset({"alice"})),
        FakeReadClient(),
    )
    with TestClient(app) as test_client:
        login(test_client, "good")
        allowed = test_client.post(
            "/figures", data={"scope": "shared"}, follow_redirects=False
        )
        assert allowed.status_code == 303
        assert len(read_state(tmp_path)["shared"]["figures"]) == 1

        login(test_client, "viewer")
        denied = test_client.post(
            "/figures", data={"scope": "shared"}, follow_redirects=False
        )
        assert denied.status_code == 403

        test_client.post("/ui/view", data={"scope": "shared"}, follow_redirects=False)
        page = test_client.get("/")
        assert page.status_code == 200
        assert "Read-only shared view." in page.text


def test_toggle_folders_persists(client: TestClient, tmp_path) -> None:
    login(client)

    response = client.post(
        "/ui/toggle-folders",
        data={"scope": "personal"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    ui = read_state(tmp_path)["personal"]["alice"]["ui"]
    assert ui["personal"]["folders_collapsed"] is True


def test_analytes_partial_scoped_to_location(client: TestClient) -> None:
    login(client)

    response = client.get("/partials/analytes", params={"location_id": "7"})

    assert response.status_code == 200
    assert "LEVEL (m)" in response.text
    assert "1096043" in response.text


def test_analytes_partial_empty_location_reports_hint(client: TestClient) -> None:
    login(client)

    response = client.get("/partials/analytes", params={"location_id": "8"})

    assert response.status_code == 200
    assert "No analytes at this location." in response.text


def test_analytes_partial_all_locations_lists_every_series(
    client: TestClient,
) -> None:
    login(client)

    response = client.get("/partials/analytes", params={"location_id": ""})

    assert response.status_code == 200
    assert "LEVEL (m)" in response.text
    assert "No analytes at this location." not in response.text


def test_chart_partial(client: TestClient, tmp_path) -> None:
    login(client)
    client.post("/figures", data={"scope": "personal"}, follow_redirects=False)
    figure_id = read_state(tmp_path)["personal"]["alice"]["figures"][0]["id"]
    client.post(
        f"/figures/personal/{figure_id}",
        data={"name": "Chart", "series_id": "42"},
        follow_redirects=False,
    )

    response = client.get(
        "/partials/chart",
        params={"figure_id": figure_id, "scope": "personal", "range": "24h"},
    )

    assert response.status_code == 200
    assert "chart-data" in response.text
    assert "1.25" in response.text


def test_chart_partial_requires_the_owning_scope(client: TestClient, tmp_path) -> None:
    login(client)
    client.post("/figures", data={"scope": "personal"}, follow_redirects=False)
    figure_id = read_state(tmp_path)["personal"]["alice"]["figures"][0]["id"]

    response = client.get(
        "/partials/chart",
        params={"figure_id": figure_id, "scope": "shared", "range": "24h"},
    )

    assert response.status_code == 404
