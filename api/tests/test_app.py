from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from transducer_api.app import (
    _jsonable,
    _limit,
    _parse_local_bound,
    _window,
    create_app,
)
from transducer_api.settings import ReadSettings


class FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, *args, **kwargs):
        return None

    def fetchall(self):
        return []

    def fetchone(self):
        return None


class FakeConnection:
    def cursor(self, row_factory=None):
        return FakeCursor()

    def execute(self, *args, **kwargs):
        return None

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeDatabase:
    def connect(self):
        return FakeConnection()


def make_settings() -> ReadSettings:
    return ReadSettings(database=FakeDatabase(), tokens={"test": "token"})


def test_parse_local_bound_naive() -> None:
    assert _parse_local_bound("2026-01-15T12:00:00", "UTC") == datetime(
        2026, 1, 15, 12, 0
    )


def test_parse_local_bound_converts_utc() -> None:
    assert _parse_local_bound("2026-01-15T12:00:00Z", "UTC") == datetime(
        2026, 1, 15, 12, 0
    )


def test_parse_local_bound_rejects_garbage() -> None:
    with pytest.raises(HTTPException) as excinfo:
        _parse_local_bound("not-a-date", "UTC")
    assert excinfo.value.status_code == 400


def test_window_enforces_limit() -> None:
    start = datetime(2026, 1, 1)
    _window(start, start + timedelta(days=31), 31)
    with pytest.raises(HTTPException):
        _window(start, start + timedelta(days=32), 31)
    with pytest.raises(HTTPException):
        _window(start, start - timedelta(days=1), 31)


def test_limit_clamps_to_maximum() -> None:
    assert _limit(5, 100) == 5
    assert _limit(500, 100) == 100
    with pytest.raises(HTTPException):
        _limit(0, 100)


def test_jsonable_serializes_database_types() -> None:
    assert _jsonable(Decimal("1.25")) == 1.25
    assert _jsonable(datetime(2026, 1, 15, 12, 0)) == "2026-01-15T12:00:00"


def test_health_does_not_require_a_token() -> None:
    client = TestClient(create_app(make_settings()))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_endpoints_require_a_bearer_token() -> None:
    client = TestClient(create_app(make_settings()))

    response = client.get("/loggers")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_unauthenticated_request_does_not_open_a_database_connection() -> None:
    class ExplodingDatabase:
        def connect(self):
            raise AssertionError("database connection opened before authentication")

    settings = ReadSettings(database=ExplodingDatabase(), tokens={"test": "token"})
    client = TestClient(create_app(settings))

    response = client.get("/loggers")

    assert response.status_code == 401


def test_authenticated_request_reaches_the_database() -> None:
    client = TestClient(create_app(make_settings()))

    response = client.get(
        "/loggers", headers={"Authorization": "Bearer token"}
    )

    assert response.status_code == 200
    assert response.json() == {"data": []}


def test_readings_rejects_unknown_timezone() -> None:
    client = TestClient(create_app(make_settings()))

    response = client.get(
        "/readings",
        params={
            "start": "2026-01-01",
            "end": "2026-01-02",
            "tz": "bogus",
        },
        headers={"Authorization": "Bearer token"},
    )

    assert response.status_code == 400


def test_whoami_returns_the_token_label() -> None:
    client = TestClient(create_app(make_settings()))

    response = client.get("/whoami", headers={"Authorization": "Bearer token"})

    assert response.status_code == 200
    assert response.json() == {"label": "test"}


def test_whoami_requires_a_token() -> None:
    client = TestClient(create_app(make_settings()))

    assert client.get("/whoami").status_code == 401


def test_series_detail_returns_404_when_missing() -> None:
    client = TestClient(create_app(make_settings()))

    response = client.get(
        "/series/999", headers={"Authorization": "Bearer token"}
    )

    assert response.status_code == 404


def test_location_series_returns_404_when_location_missing() -> None:
    client = TestClient(create_app(make_settings()))

    response = client.get(
        "/locations/999/series", headers={"Authorization": "Bearer token"}
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "location not found"


def test_location_series_scopes_counts_to_the_location() -> None:
    statements: list[str] = []
    params_log: list[tuple] = []

    class LoggingCursor(FakeCursor):
        def execute(self, statement, params=None):
            statements.append(statement)
            params_log.append(params)

        def fetchone(self):
            return {"ok": 1}

        def fetchall(self):
            return [
                {
                    "series_id": 7,
                    "serial_number": "1096043",
                    "channel_no": 1,
                    "identification": "LEVEL",
                    "unit": "m",
                    "data_kind": "uncompensated",
                    "reading_count": 5,
                    "first_reading": datetime(2026, 1, 1),
                    "last_reading": datetime(2026, 1, 2),
                }
            ]

    class LoggingConnection(FakeConnection):
        def cursor(self, row_factory=None):
            return LoggingCursor()

    class LoggingDatabase:
        def connect(self):
            return LoggingConnection()

    settings = ReadSettings(database=LoggingDatabase(), tokens={"test": "token"})
    client = TestClient(create_app(settings))

    response = client.get(
        "/locations/3/series", headers={"Authorization": "Bearer token"}
    )

    assert response.status_code == 200
    rows = response.json()["data"]
    assert rows[0]["series_id"] == 7
    assert rows[0]["reading_count"] == 5
    # The series query reaches readings through the read-role view and is
    # parameterized by the requested location.
    assert "v_reading" in statements[1]
    assert "vr.location_id = %s" in statements[1]
    assert params_log[1] == (3, 1000, 0)


def test_series_points_rejects_unknown_timezone() -> None:
    client = TestClient(create_app(make_settings()))

    response = client.get(
        "/series/1/points",
        params={
            "start": "2026-01-01",
            "end": "2026-01-02",
            "tz": "bogus",
        },
        headers={"Authorization": "Bearer token"},
    )

    assert response.status_code == 400


def test_series_points_rejects_oversized_window() -> None:
    client = TestClient(create_app(make_settings()))

    response = client.get(
        "/series/1/points",
        params={
            "start": "2026-01-01",
            "end": "2026-03-01",
        },
        headers={"Authorization": "Bearer token"},
    )

    assert response.status_code == 400


def test_series_points_returns_bounded_bucket_count() -> None:
    captured: dict[str, object] = {}

    class CapturingCursor(FakeCursor):
        def execute(self, statement, params=None):
            captured["statement"] = statement
            captured["params"] = params

    class CapturingConnection(FakeConnection):
        def cursor(self, row_factory=None):
            return CapturingCursor()

    class CapturingDatabase:
        def connect(self):
            return CapturingConnection()

    settings = ReadSettings(
        database=CapturingDatabase(), tokens={"test": "token"}
    )
    client = TestClient(create_app(settings))

    response = client.get(
        "/series/1/points",
        params={
            "start": "2026-01-01T00:00:00",
            "end": "2026-01-02T00:00:00",
            "points": 100,
        },
        headers={"Authorization": "Bearer token"},
    )

    assert response.status_code == 200
    # One day spread over at most 100 inclusive buckets => ~873 s per bucket.
    assert response.json()["bucket_seconds"] == 873
    assert captured["params"][0] == "873 seconds"
    assert "location_id = %s" not in captured["statement"]


def test_series_points_accepts_a_location_scope() -> None:
    captured: dict[str, object] = {}

    class CapturingCursor(FakeCursor):
        def execute(self, statement, params=None):
            captured["statement"] = statement
            captured["params"] = params

    class CapturingConnection(FakeConnection):
        def cursor(self, row_factory=None):
            return CapturingCursor()

    class CapturingDatabase:
        def connect(self):
            return CapturingConnection()

    settings = ReadSettings(
        database=CapturingDatabase(), tokens={"test": "token"}
    )
    client = TestClient(create_app(settings))

    response = client.get(
        "/series/1/points",
        params={
            "start": "2026-01-01T00:00:00",
            "end": "2026-01-02T00:00:00",
            "points": 100,
            "location_id": 5,
        },
        headers={"Authorization": "Bearer token"},
    )

    assert response.status_code == 200
    assert "location_id = %s" in captured["statement"]
    assert 5 in captured["params"]
    assert response.json()["location_id"] == 5


def test_series_points_location_scope_works_in_utc_mode() -> None:
    captured: dict[str, object] = {}

    class CapturingCursor(FakeCursor):
        def execute(self, statement, params=None):
            captured["statement"] = statement
            captured["params"] = params

    class CapturingConnection(FakeConnection):
        def cursor(self, row_factory=None):
            return CapturingCursor()

    class CapturingDatabase:
        def connect(self):
            return CapturingConnection()

    settings = ReadSettings(
        database=CapturingDatabase(), tokens={"test": "token"}
    )
    client = TestClient(create_app(settings))

    response = client.get(
        "/series/1/points",
        params={
            "start": "2026-01-01T00:00:00",
            "end": "2026-01-02T00:00:00",
            "points": 100,
            "tz": "utc",
            "location_id": 5,
        },
        headers={"Authorization": "Bearer token"},
    )

    assert response.status_code == 200
    assert "location_id = %s" in captured["statement"]
    assert captured["params"][-1] == 5

