import httpx
import pytest

from transducer_dashboard.read_client import ReadApiError, ReadClient


def make_client(handler) -> ReadClient:
    client = ReadClient("http://read-api:8080")
    client._client = httpx.Client(
        base_url="http://read-api:8080", transport=httpx.MockTransport(handler)
    )
    return client


def test_whoami_forwards_bearer_token() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"label": "alice"})

    client = make_client(handler)

    assert client.whoami("secret") == "alice"
    assert seen == {"path": "/whoami", "auth": "Bearer secret"}


def test_401_becomes_read_api_error() -> None:
    client = make_client(lambda request: httpx.Response(401, json={"detail": "no"}))

    with pytest.raises(ReadApiError) as excinfo:
        client.whoami("bad")

    assert excinfo.value.status_code == 401


def test_loggers_series_and_points() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/loggers":
            return httpx.Response(200, json={"data": [{"serial_number": "123"}]})
        if request.url.path == "/series":
            assert request.url.params["serial"] == "123"
            return httpx.Response(200, json={"data": [{"series_id": 7}]})
        if request.url.path == "/series/7":
            return httpx.Response(200, json={"data": {"series_id": 7, "unit": "m"}})
        if request.url.path == "/series/7/points":
            assert request.url.params["points"] == "500"
            assert request.url.params["tz"] == "local"
            return httpx.Response(
                200, json={"data": [{"ts": "2026-01-01T00:00:00", "value": 1.5}]}
            )
        return httpx.Response(404)

    client = make_client(handler)

    assert client.loggers("t")[0]["serial_number"] == "123"
    assert client.series("t", serial="123")[0]["series_id"] == 7
    assert client.series_detail("t", 7)["unit"] == "m"
    assert client.points(
        "t", 7, start="a", end="b", points=500
    )[0]["value"] == 1.5


def test_missing_series_raises_404() -> None:
    client = make_client(lambda request: httpx.Response(404, json={"detail": "x"}))

    with pytest.raises(ReadApiError) as excinfo:
        client.series_detail("t", 99)

    assert excinfo.value.status_code == 404


def test_locations_and_location_scoped_series() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/locations":
            return httpx.Response(
                200, json={"data": [{"location_id": 3, "name": "MCMW-04"}]}
            )
        if request.url.path == "/locations/3/series":
            assert request.url.params["limit"] == "1000"
            return httpx.Response(200, json={"data": [{"series_id": 9}]})
        return httpx.Response(404)

    client = make_client(handler)

    assert client.locations("t")[0]["name"] == "MCMW-04"
    assert client.series_at_location("t", 3)[0]["series_id"] == 9


def test_points_forward_location_scope() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/series/9/points":
            seen["location_id"] = request.url.params.get("location_id")
            return httpx.Response(200, json={"data": [{"value": 1.0}]})
        return httpx.Response(404)

    client = make_client(handler)

    assert client.points(
        "t", 9, start="a", end="b", points=5, location_id=3
    )[0]["value"] == 1.0
    assert seen["location_id"] == "3"

    client.points("t", 9, start="a", end="b", points=5)
    assert seen["location_id"] is None


def test_transport_failure_becomes_502() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    client = make_client(handler)

    with pytest.raises(ReadApiError) as excinfo:
        client.loggers("t")

    assert excinfo.value.status_code == 502
