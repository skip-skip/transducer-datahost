"""HTTP client for the existing read-only API."""

from __future__ import annotations

from typing import Any

import httpx


class ReadApiError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class ReadClient:
    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def _get(
        self, token: str, path: str, params: dict[str, Any] | None = None
    ) -> Any:
        try:
            response = self._client.get(
                path,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise ReadApiError(502, "read API is unreachable") from exc

        if response.status_code == 401:
            raise ReadApiError(401, "invalid or expired token")
        if response.status_code == 404:
            raise ReadApiError(404, "not found")
        if response.status_code >= 400:
            detail = "read API rejected the request"
            try:
                payload = response.json()
                if isinstance(payload, dict) and payload.get("detail"):
                    detail = str(payload["detail"])
            except ValueError:
                pass
            raise ReadApiError(response.status_code, detail)

        try:
            return response.json()
        except ValueError as exc:
            raise ReadApiError(502, "read API returned invalid JSON") from exc

    def whoami(self, token: str) -> str:
        payload = self._get(token, "/whoami")
        label = payload.get("label") if isinstance(payload, dict) else None
        if not isinstance(label, str) or not label:
            raise ReadApiError(502, "read API did not return a token label")
        return label

    def loggers(self, token: str) -> list[dict[str, Any]]:
        payload = self._get(token, "/loggers")
        data = payload.get("data") if isinstance(payload, dict) else None
        return data if isinstance(data, list) else []

    def locations(self, token: str) -> list[dict[str, Any]]:
        payload = self._get(token, "/locations")
        data = payload.get("data") if isinstance(payload, dict) else None
        return data if isinstance(data, list) else []

    def series_at_location(
        self, token: str, location_id: int, *, limit: int = 1000
    ) -> list[dict[str, Any]]:
        payload = self._get(
            token, f"/locations/{location_id}/series", params={"limit": limit}
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        return data if isinstance(data, list) else []

    def series(
        self,
        token: str,
        *,
        serial: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if serial is not None:
            params["serial"] = serial
        payload = self._get(token, "/series", params=params)
        data = payload.get("data") if isinstance(payload, dict) else None
        return data if isinstance(data, list) else []

    def series_detail(self, token: str, series_id: int) -> dict[str, Any]:
        payload = self._get(token, f"/series/{series_id}")
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise ReadApiError(404, "series not found")
        return data

    def points(
        self,
        token: str,
        series_id: int,
        *,
        start: str,
        end: str,
        points: int,
        tz: str = "local",
        location_id: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "start": start,
            "end": end,
            "points": points,
            "tz": tz,
        }
        if location_id is not None:
            params["location_id"] = location_id
        payload = self._get(
            token,
            f"/series/{series_id}/points",
            params=params,
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        return data if isinstance(data, list) else []
