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


def scenes_for(state, scope="personal", label="alice"):
    if scope == "shared":
        return state["shared"]["scenes"]
    return state["personal"][label]["scenes"]


def find_scene(tmp_path, scene_id, scope="personal", label="alice"):
    state = read_state(tmp_path)
    return next(
        scene
        for scene in scenes_for(state, scope, label)
        if scene["id"] == scene_id
    )


def find_figure(tmp_path, scene_id, figure_id, scope="personal", label="alice"):
    scene = find_scene(tmp_path, scene_id, scope, label)
    return next(figure for figure in scene["figures"] if figure["id"] == figure_id)


def create_scene(
    test_client: TestClient,
    tmp_path,
    *,
    scope="personal",
    name="Scene",
    label="alice",
):
    response = test_client.post(
        "/scenes", data={"scope": scope, "name": name}, follow_redirects=False
    )
    assert response.status_code == 303
    return scenes_for(read_state(tmp_path), scope, label)[-1]


def create_figure(
    test_client: TestClient,
    tmp_path,
    scene,
    *,
    scope="personal",
    name="",
    label="alice",
):
    response = test_client.post(
        f"/scenes/{scope}/{scene['id']}/figures",
        data={"scope": scope, "name": name},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return find_scene(tmp_path, scene["id"], scope, label)["figures"][-1]


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


def test_authenticated_index_renders_scene_shell(client: TestClient) -> None:
    login(client)

    response = client.get("/")

    assert response.status_code == 200
    assert "No scene selected" in response.text
    assert "My scenes" in response.text
    assert "Options" in response.text
    assert "alice" in response.text


def test_create_scene_and_figure(client: TestClient, tmp_path) -> None:
    login(client)

    scene = create_scene(client, tmp_path, name="Well field")
    assert scene["name"] == "Well field"
    assert scene["grid"] == {"cols": 12, "row_height": 220}
    assert scene["figures"] == []

    ui = read_state(tmp_path)["personal"]["alice"]["ui"]
    assert ui["personal"]["selected_scene_id"] == scene["id"]
    assert ui["personal"]["selected_figure_id"] is None

    figure = create_figure(client, tmp_path, scene)
    assert figure["name"] == "Untitled figure"
    assert figure["layout"] == {"x": 0, "y": 0, "w": 6, "h": 2}

    ui = read_state(tmp_path)["personal"]["alice"]["ui"]
    assert ui["personal"]["selected_figure_id"] == figure["id"]

    page = client.get("/")
    assert page.status_code == 200
    assert "Well field" in page.text
    assert "Untitled figure" in page.text
    assert 'class="scene-grid"' in page.text
    assert f'data-scene-id="{scene["id"]}"' in page.text
    assert "panel-chart" in page.text
    assert (
        f"/scenes/personal/{scene['id']}/figures/{figure['id']}" in page.text
    )


def test_update_and_delete_figure(client: TestClient, tmp_path) -> None:
    login(client)
    scene = create_scene(client, tmp_path)
    figure = create_figure(client, tmp_path, scene)

    updated = client.post(
        f"/scenes/personal/{scene['id']}/figures/{figure['id']}",
        data={"name": "GC-1980 water level", "series_id": "42", "location_id": "7"},
        follow_redirects=False,
    )
    assert updated.status_code == 303

    stored = find_figure(tmp_path, scene["id"], figure["id"])
    assert stored["name"] == "GC-1980 water level"
    assert stored["series_id"] == 42
    assert stored["location_id"] == 7

    chart = client.get(
        "/partials/chart",
        params={
            "scene_id": scene["id"],
            "figure_id": figure["id"],
            "scope": "personal",
            "range": "24h",
        },
    )
    assert chart.status_code == 200
    assert "chart-data" in chart.text
    assert "1.25" in chart.text
    assert "MCMW-04" in chart.text
    assert "LEVEL (m)" in chart.text

    page = client.get("/")
    assert page.status_code == 200
    assert "GC-1980 water level" in page.text

    deleted = client.post(
        f"/scenes/personal/{scene['id']}/figures/{figure['id']}/delete",
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    assert find_scene(tmp_path, scene["id"])["figures"] == []


def test_location_scoped_chart_requests_location_points(tmp_path) -> None:
    fake = FakeReadClient()
    app = create_app(make_settings(tmp_path), fake)
    with TestClient(app) as test_client:
        login(test_client)
        scene = create_scene(test_client, tmp_path)
        figure = create_figure(test_client, tmp_path, scene)
        saved = test_client.post(
            f"/scenes/personal/{scene['id']}/figures/{figure['id']}",
            data={"name": "MCMW-04 level", "series_id": "42", "location_id": "7"},
            follow_redirects=False,
        )
        assert saved.status_code == 303

        chart = test_client.get(
            "/partials/chart",
            params={
                "scene_id": scene["id"],
                "figure_id": figure["id"],
                "scope": "personal",
            },
        )
        assert chart.status_code == 200
        assert "chart-data" in chart.text

        assert fake.points_calls, "the chart render should request points"
        assert all(call["location_id"] == 7 for call in fake.points_calls)


def test_legacy_figure_without_location_charts_whole_series(
    client: TestClient, tmp_path
) -> None:
    login(client)
    scene = create_scene(client, tmp_path)
    figure = create_figure(client, tmp_path, scene)

    saved = client.post(
        f"/scenes/personal/{scene['id']}/figures/{figure['id']}",
        data={"name": "Legacy", "series_id": "42"},
        follow_redirects=False,
    )
    assert saved.status_code == 303

    stored = find_figure(tmp_path, scene["id"], figure["id"])
    assert stored["series_id"] == 42
    assert stored.get("location_id") is None

    page = client.get("/")
    assert page.status_code == 200
    assert "All locations" in page.text

    chart = client.get(
        "/partials/chart",
        params={
            "scene_id": scene["id"],
            "figure_id": figure["id"],
            "scope": "personal",
        },
    )
    assert chart.status_code == 200
    assert "chart-data" in chart.text


def test_update_rejects_blank_name(client: TestClient, tmp_path) -> None:
    login(client)
    scene = create_scene(client, tmp_path)
    figure = create_figure(client, tmp_path, scene)

    response = client.post(
        f"/scenes/personal/{scene['id']}/figures/{figure['id']}",
        data={"name": "   ", "series_id": ""},
        follow_redirects=False,
    )

    assert response.status_code == 400


def test_update_rejects_unknown_series(client: TestClient, tmp_path) -> None:
    login(client)
    scene = create_scene(client, tmp_path)
    figure = create_figure(client, tmp_path, scene)

    response = client.post(
        f"/scenes/personal/{scene['id']}/figures/{figure['id']}",
        data={"name": "Broken", "series_id": "999"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "does not exist" in response.text


def test_update_rejects_series_not_at_location(client: TestClient, tmp_path) -> None:
    login(client)
    scene = create_scene(client, tmp_path)
    figure = create_figure(client, tmp_path, scene)

    response = client.post(
        f"/scenes/personal/{scene['id']}/figures/{figure['id']}",
        data={"name": "Broken", "series_id": "42", "location_id": "8"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "no readings at this location" in response.text


def test_update_rejects_unknown_location(client: TestClient, tmp_path) -> None:
    login(client)
    scene = create_scene(client, tmp_path)
    figure = create_figure(client, tmp_path, scene)

    response = client.post(
        f"/scenes/personal/{scene['id']}/figures/{figure['id']}",
        data={"name": "Broken", "series_id": "42", "location_id": "999"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "location does not exist" in response.text


def test_shared_scene_permissions(tmp_path) -> None:
    app = create_app(
        make_settings(tmp_path, shared_editors=frozenset({"alice"})),
        FakeReadClient(),
    )
    with TestClient(app) as test_client:
        login(test_client, "good")
        scene = create_scene(test_client, tmp_path, scope="shared", name="Shared ops")
        assert len(scenes_for(read_state(tmp_path), "shared")) == 1

        login(test_client, "viewer")
        denied = test_client.post(
            "/scenes", data={"scope": "shared"}, follow_redirects=False
        )
        assert denied.status_code == 403

        denied_figure = test_client.post(
            f"/scenes/shared/{scene['id']}/figures",
            data={"scope": "shared"},
            follow_redirects=False,
        )
        assert denied_figure.status_code == 403

        test_client.post("/ui/view", data={"scope": "shared"}, follow_redirects=False)
        page = test_client.get("/")
        assert page.status_code == 200
        assert "Read-only shared view." in page.text
        assert "Shared ops" in page.text


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


def test_toggle_options_persists(client: TestClient, tmp_path) -> None:
    login(client)

    response = client.post(
        "/ui/toggle-options",
        data={"scope": "personal"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    ui = read_state(tmp_path)["personal"]["alice"]["ui"]
    assert ui["personal"]["options_collapsed"] is True

    page = client.get("/")
    assert "options-collapsed" in page.text


def test_select_scene_and_figure(client: TestClient, tmp_path) -> None:
    login(client)
    first_scene = create_scene(client, tmp_path, name="First")
    second_scene = create_scene(client, tmp_path, name="Second")
    first_figure = create_figure(client, tmp_path, first_scene)
    create_figure(client, tmp_path, second_scene)

    selected = client.post(
        "/ui/select",
        data={"scope": "personal", "scene_id": first_scene["id"], "figure_id": ""},
        follow_redirects=False,
    )
    assert selected.status_code == 303
    ui = read_state(tmp_path)["personal"]["alice"]["ui"]["personal"]
    assert ui["selected_scene_id"] == first_scene["id"]
    assert ui["selected_figure_id"] is None

    selected = client.post(
        "/ui/select",
        data={
            "scope": "personal",
            "scene_id": first_scene["id"],
            "figure_id": first_figure["id"],
        },
        follow_redirects=False,
    )
    assert selected.status_code == 303
    ui = read_state(tmp_path)["personal"]["alice"]["ui"]["personal"]
    assert ui["selected_scene_id"] == first_scene["id"]
    assert ui["selected_figure_id"] == first_figure["id"]

    page = client.get("/")
    assert "figure-panel active" in page.text
    assert f"/scenes/personal/{first_scene['id']}/figures/{first_figure['id']}" in page.text


def test_select_rejects_figure_from_another_scene(
    client: TestClient, tmp_path
) -> None:
    login(client)
    first_scene = create_scene(client, tmp_path, name="First")
    second_scene = create_scene(client, tmp_path, name="Second")
    other_figure = create_figure(client, tmp_path, second_scene)

    response = client.post(
        "/ui/select",
        data={
            "scope": "personal",
            "scene_id": first_scene["id"],
            "figure_id": other_figure["id"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 404


def test_update_scene_grid_and_name(client: TestClient, tmp_path) -> None:
    login(client)
    scene = create_scene(client, tmp_path, name="Before")
    figure = create_figure(client, tmp_path, scene)

    response = client.post(
        f"/scenes/personal/{scene['id']}",
        data={"name": "After", "cols": "6", "row_height": "180"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    stored = find_scene(tmp_path, scene["id"])
    assert stored["name"] == "After"
    assert stored["grid"] == {"cols": 6, "row_height": 180}
    assert stored["figures"][0]["id"] == figure["id"]
    assert stored["figures"][0]["layout"]["w"] <= 6

    invalid = client.post(
        f"/scenes/personal/{scene['id']}",
        data={"name": "After", "cols": "0", "row_height": "180"},
        follow_redirects=False,
    )
    assert invalid.status_code == 400


def test_delete_scene(client: TestClient, tmp_path) -> None:
    login(client)
    scene = create_scene(client, tmp_path)
    figure = create_figure(client, tmp_path, scene)

    response = client.post(
        f"/scenes/personal/{scene['id']}/delete",
        follow_redirects=False,
    )
    assert response.status_code == 303

    state = read_state(tmp_path)
    assert scenes_for(state) == []
    ui = state["personal"]["alice"]["ui"]["personal"]
    assert ui["selected_scene_id"] is None
    assert ui["selected_figure_id"] is None
    assert figure["id"] not in json.dumps(state)

    page = client.get("/")
    assert "No scene selected" in page.text


def test_create_figure_rejects_unknown_scene(client: TestClient) -> None:
    login(client)

    response = client.post(
        "/scenes/personal/missing/figures",
        data={"scope": "personal"},
        follow_redirects=False,
    )

    assert response.status_code == 404


def test_scene_update_rejects_foreign_scope(client: TestClient, tmp_path) -> None:
    login(client)
    scene = create_scene(client, tmp_path)

    response = client.post(
        f"/scenes/shared/{scene['id']}",
        data={"name": "Nope", "cols": "12", "row_height": "220"},
        follow_redirects=False,
    )

    assert response.status_code == 404


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
    scene = create_scene(client, tmp_path)
    figure = create_figure(client, tmp_path, scene)
    client.post(
        f"/scenes/personal/{scene['id']}/figures/{figure['id']}",
        data={"name": "Chart", "series_id": "42"},
        follow_redirects=False,
    )

    response = client.get(
        "/partials/chart",
        params={
            "scene_id": scene["id"],
            "figure_id": figure["id"],
            "scope": "personal",
            "range": "24h",
        },
    )

    assert response.status_code == 200
    assert "chart-data" in response.text
    assert "1.25" in response.text


def test_chart_partial_requires_the_owning_scope(client: TestClient, tmp_path) -> None:
    login(client)
    scene = create_scene(client, tmp_path)
    figure = create_figure(client, tmp_path, scene)

    response = client.get(
        "/partials/chart",
        params={
            "scene_id": scene["id"],
            "figure_id": figure["id"],
            "scope": "shared",
            "range": "24h",
        },
    )

    assert response.status_code == 404


def test_chart_partial_rejects_figure_from_another_scene(
    client: TestClient, tmp_path
) -> None:
    login(client)
    first_scene = create_scene(client, tmp_path, name="First")
    second_scene = create_scene(client, tmp_path, name="Second")
    other_figure = create_figure(client, tmp_path, second_scene)

    response = client.get(
        "/partials/chart",
        params={
            "scene_id": first_scene["id"],
            "figure_id": other_figure["id"],
            "scope": "personal",
        },
    )

    assert response.status_code == 404
