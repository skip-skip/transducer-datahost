from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from dev_fixtures import FakeReadClient, build_app, build_shell
from transducer_dashboard.read_client import ReadApiError


def test_fake_client_rejects_an_unknown_token() -> None:
    client = FakeReadClient()

    with pytest.raises(ReadApiError) as excinfo:
        client.whoami("nope")

    assert excinfo.value.status_code == 401


def test_fake_points_stay_inside_the_requested_window() -> None:
    client = FakeReadClient()
    end = datetime(2026, 11, 18, 12, 0)
    start = end - timedelta(days=7)

    rows = client.points(
        "dev",
        10,
        start=start.isoformat(),
        end=end.isoformat(),
        points=48,
    )

    assert 2 <= len(rows) <= 48
    stamps = [datetime.fromisoformat(row["ts"]) for row in rows]
    assert stamps == sorted(stamps)
    assert stamps[0] >= start
    assert stamps[-1] <= end
    assert all(isinstance(row["value"], float) for row in rows)


def test_build_shell_seeds_a_demo_scene(tmp_path) -> None:
    shell = build_shell(tmp_path, seed=True)

    assert len(shell.scene_buttons) == 1
    assert len(shell.figure_cards) == 2
    assert shell.selected_figure_id is not None
    assert shell.settings_card is not None
    assert shell.settings_card.collapsed is False


def test_build_app_accepts_the_dev_token(tmp_path) -> None:
    app = build_app(tmp_path, seed=True)

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        response = client.post(
            "/login",
            data={"token": "dev"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "transducer_dashboard" in response.cookies
