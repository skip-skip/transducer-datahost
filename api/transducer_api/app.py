"""Read-only FastAPI service over the ``transducer_read`` role.

All endpoints are parameterized SQL over the curated views. The service never
sees ``transducer_ingest`` or the admin credentials, and it has no mutation
routes.
"""

from __future__ import annotations

import ipaddress
import math
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg import Connection
from psycopg.rows import dict_row

from .auth import TokenIdentity, TokenRegistry, make_bearer_dependency
from .settings import ReadSettings

TZ_CHOICES = {"local", "utc"}
IMPORT_STATUSES = {
    "imported",
    "warning",
    "empty",
    "error",
    "skipped_duplicate",
    "skipped_compensated",
}
INTERVALS = {"hour", "day"}


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return str(value)
    return value


def _rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: _jsonable(value) for key, value in row.items()} for row in rows]


def _parse_local_bound(value: str, site_timezone: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"invalid timestamp {value!r}; use ISO-8601",
        ) from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(ZoneInfo(site_timezone)).replace(tzinfo=None)
    return parsed


def _window(start: datetime, end: datetime, max_days: int) -> None:
    if end < start:
        raise HTTPException(status_code=400, detail="end must be on or after start")
    if end - start > timedelta(days=max_days):
        raise HTTPException(
            status_code=400,
            detail=f"time window exceeds the {max_days}-day limit",
        )


def _limit(value: int, maximum: int) -> int:
    if value < 1:
        raise HTTPException(status_code=400, detail="limit must be positive")
    return min(value, maximum)


def create_app(settings: ReadSettings | None = None) -> FastAPI:
    settings = settings or ReadSettings.from_env()
    registry = TokenRegistry(settings.tokens)
    require_token = make_bearer_dependency(registry)

    app = FastAPI(
        title="Transducer read API",
        version="1.0.0",
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET"],
            allow_headers=["Authorization"],
        )

    def get_conn() -> Generator[Connection, None, None]:
        conn = settings.database.connect()
        try:
            yield conn
        finally:
            conn.close()

    # Router-level authentication runs before the per-request database
    # connection, so an unauthenticated caller cannot consume connections.
    router = APIRouter(dependencies=[Depends(require_token)])

    @app.get("/health")
    def health() -> JSONResponse:
        try:
            with settings.database.connect() as conn:
                conn.execute("SELECT 1")
        except Exception:  # noqa: BLE001 - health reports, never raises
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "database": "unavailable"},
            )
        return JSONResponse(content={"status": "ok", "database": "ok"})

    @router.get("/whoami")
    def whoami(
        identity: TokenIdentity = Depends(require_token),
    ) -> dict[str, str]:
        return {"label": identity.label}

    @router.get("/loggers")
    def loggers(
        conn: Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                    l.serial_number,
                    l.instrument_type,
                    l.model_number,
                    l.firmware,
                    l.project_id,
                    l.first_seen,
                    l.last_seen,
                    count(DISTINCT s.series_id) AS series_count,
                    max(r.ts) AS latest_reading
                FROM transducer.logger l
                LEFT JOIN transducer.series s
                    ON s.serial_number = l.serial_number
                LEFT JOIN transducer.reading r
                    ON r.series_id = s.series_id
                GROUP BY
                    l.serial_number, l.instrument_type, l.model_number,
                    l.firmware, l.project_id, l.first_seen, l.last_seen
                ORDER BY l.serial_number
                """
            )
            return {"data": _rows(list(cur.fetchall()))}

    @router.get("/locations")
    def locations(
        conn: Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT location_id, name, base_location, is_aquifer_test,
                       latitude, longitude, timezone
                FROM transducer.location
                ORDER BY name
                """
            )
            return {"data": _rows(list(cur.fetchall()))}

    @router.get("/series")
    def series(
        serial: str | None = None,
        identification: str | None = None,
        unit: str | None = None,
        data_kind: str | None = None,
        limit: int = Query(1000),
        offset: int = 0,
        conn: Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        limit = _limit(limit, settings.max_rows)
        offset = max(0, offset)
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("s.serial_number", serial),
            ("s.identification", identification),
            ("s.unit", unit),
            ("s.data_kind", data_kind),
        ):
            if value is not None:
                clauses.append(f"{column} = %s")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])

        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT
                    s.series_id, s.serial_number, s.channel_no,
                    s.identification, s.unit, s.data_kind,
                    count(r.*) AS reading_count,
                    min(r.ts) AS first_reading,
                    max(r.ts) AS last_reading
                FROM transducer.series s
                LEFT JOIN transducer.reading r USING (series_id)
                {where}
                GROUP BY s.series_id, s.serial_number, s.channel_no,
                         s.identification, s.unit, s.data_kind
                ORDER BY s.serial_number, s.channel_no, s.identification
                LIMIT %s OFFSET %s
                """,
                tuple(params),
            )
            rows = list(cur.fetchall())
        return {
            "data": _rows(rows),
            "pagination": {"limit": limit, "offset": offset, "count": len(rows)},
        }

    @router.get("/readings")
    def readings(
        start: str,
        end: str,
        serial: str | None = None,
        channel: int | None = None,
        identification: str | None = None,
        unit: str | None = None,
        location: str | None = None,
        series_id: int | None = None,
        data_kind: str | None = None,
        tz: str = "local",
        limit: int = Query(1000),
        offset: int = 0,
        conn: Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        if tz not in TZ_CHOICES:
            raise HTTPException(status_code=400, detail="tz must be local or utc")
        limit = _limit(limit, settings.max_rows)
        offset = max(0, offset)
        start_dt = _parse_local_bound(start, settings.site_timezone)
        end_dt = _parse_local_bound(end, settings.site_timezone)
        _window(start_dt, end_dt, settings.max_window_days)

        clauses = ["ts >= %s", "ts <= %s"]
        params: list[Any] = [start_dt, end_dt]
        for column, value in (
            ("serial_number", serial),
            ("channel_no", channel),
            ("identification", identification),
            ("unit", unit),
            ("location", location),
            ("series_id", series_id),
            ("data_kind", data_kind),
        ):
            if value is not None:
                clauses.append(f"{column} = %s")
                params.append(value)

        order_column = "ts_utc" if tz == "utc" else "ts"
        params.extend([limit + 1, offset])
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT
                    series_id, serial_number, location_id, location,
                    is_aquifer_test, timezone, data_kind, channel_no,
                    identification, unit, ts, ts_utc, value, file_id,
                    filename, download_time
                FROM transducer.v_reading_utc
                WHERE {' AND '.join(clauses)}
                ORDER BY {order_column}, series_id
                LIMIT %s OFFSET %s
                """,
                tuple(params),
            )
            rows = list(cur.fetchall())

        has_more = len(rows) > limit
        rows = rows[:limit]
        return {
            "data": _rows(rows),
            "pagination": {
                "limit": limit,
                "offset": offset,
                "count": len(rows),
                "has_more": has_more,
            },
            "tz": tz,
        }

    @router.get("/readings/latest")
    def latest_readings(
        serial: str | None = None,
        channel: int | None = None,
        data_kind: str | None = None,
        limit: int = Query(1000),
        conn: Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        limit = _limit(limit, settings.max_rows)
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("serial_number", serial),
            ("channel_no", channel),
            ("data_kind", data_kind),
        ):
            if value is not None:
                clauses.append(f"{column} = %s")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)

        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                WITH latest AS (
                    SELECT DISTINCT ON (series_id) *
                    FROM transducer.v_reading_utc
                    {where}
                    ORDER BY series_id, ts DESC
                )
                SELECT *
                FROM latest
                ORDER BY serial_number, channel_no
                LIMIT %s
                """,
                tuple(params),
            )
            rows = list(cur.fetchall())
        return {
            "data": _rows(rows),
            "pagination": {"limit": limit, "count": len(rows)},
        }

    @router.get("/imports")
    def imports(
        status: str | None = None,
        serial: str | None = None,
        location: str | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int = Query(1000),
        offset: int = 0,
        conn: Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        if status is not None and status not in IMPORT_STATUSES:
            raise HTTPException(status_code=400, detail="invalid status filter")
        limit = _limit(limit, settings.max_rows)
        offset = max(0, offset)

        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("status", status),
            ("serial_number", serial),
            ("location", location),
        ):
            if value is not None:
                clauses.append(f"{column} = %s")
                params.append(value)
        if start is not None:
            clauses.append("download_time >= %s")
            params.append(_parse_local_bound(start, settings.site_timezone))
        if end is not None:
            clauses.append("download_time <= %s")
            params.append(_parse_local_bound(end, settings.site_timezone))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit + 1, offset])

        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT
                    file_id, filename, sha256, download_time, serial_number,
                    location_id, location, is_aquifer_test, start_time,
                    stop_time, num_log, parsed_log, is_compensated,
                    data_kind, status, error, imported_at
                FROM transducer.v_import
                {where}
                ORDER BY download_time DESC, file_id DESC
                LIMIT %s OFFSET %s
                """,
                tuple(params),
            )
            rows = list(cur.fetchall())

        has_more = len(rows) > limit
        rows = rows[:limit]
        return {
            "data": _rows(rows),
            "pagination": {
                "limit": limit,
                "offset": offset,
                "count": len(rows),
                "has_more": has_more,
            },
        }

    @router.get("/series/{series_id}")
    def series_detail(
        series_id: int,
        conn: Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                    s.series_id, s.serial_number, s.channel_no,
                    s.identification, s.unit, s.data_kind,
                    count(r.*) AS reading_count,
                    min(r.ts) AS first_reading,
                    max(r.ts) AS last_reading
                FROM transducer.series s
                LEFT JOIN transducer.reading r USING (series_id)
                WHERE s.series_id = %s
                GROUP BY s.series_id, s.serial_number, s.channel_no,
                         s.identification, s.unit, s.data_kind
                """,
                (series_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="series not found")
        return {"data": _jsonable(row)}

    @router.get("/series/{series_id}/points")
    def series_points(
        series_id: int,
        start: str,
        end: str,
        points: int = Query(2000),
        tz: str = "local",
        conn: Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        if tz not in TZ_CHOICES:
            raise HTTPException(status_code=400, detail="tz must be local or utc")
        points = _limit(points, settings.max_rows)
        start_dt = _parse_local_bound(start, settings.site_timezone)
        end_dt = _parse_local_bound(end, settings.site_timezone)
        _window(start_dt, end_dt, settings.max_window_days)

        # Bucket so that the inclusive [start, end] range yields at most
        # ``points`` rows, no matter how many raw readings it contains.
        span_seconds = (end_dt - start_dt).total_seconds()
        if points == 1:
            width_seconds = max(1, math.ceil(span_seconds))
        else:
            width_seconds = max(1, math.ceil(span_seconds / (points - 1)))
        width = f"{width_seconds} seconds"

        if tz == "utc":
            origin = start_dt.replace(
                tzinfo=ZoneInfo(settings.site_timezone)
            ).astimezone(UTC)
            sql = """
                WITH bucketed AS (
                    SELECT
                        date_bin(%s::interval, ts_utc, %s::timestamptz) AS bucket,
                        timezone,
                        value
                    FROM transducer.v_reading_utc
                    WHERE series_id = %s AND ts >= %s AND ts <= %s
                )
                SELECT
                    bucket AT TIME ZONE max(timezone) AS ts,
                    bucket AS ts_utc,
                    avg(value) AS value,
                    min(value) AS min_value,
                    max(value) AS max_value,
                    count(*) AS n
                FROM bucketed
                GROUP BY bucket
                ORDER BY bucket
            """
            params: tuple[Any, ...] = (width, origin, series_id, start_dt, end_dt)
        else:
            sql = """
                WITH bucketed AS (
                    SELECT
                        date_bin(%s::interval, ts, %s::timestamp) AS bucket,
                        timezone,
                        value
                    FROM transducer.v_reading_utc
                    WHERE series_id = %s AND ts >= %s AND ts <= %s
                )
                SELECT
                    bucket AS ts,
                    bucket AT TIME ZONE max(timezone) AS ts_utc,
                    avg(value) AS value,
                    min(value) AS min_value,
                    max(value) AS max_value,
                    count(*) AS n
                FROM bucketed
                GROUP BY bucket
                ORDER BY bucket
            """
            params = (width, start_dt, series_id, start_dt, end_dt)

        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            rows = list(cur.fetchall())
        return {
            "series_id": series_id,
            "points": points,
            "bucket_seconds": width_seconds,
            "tz": tz,
            "data": _rows(rows),
        }

    @router.get("/series/{series_id}/summary")
    def series_summary(
        series_id: int,
        start: str,
        end: str,
        interval: str = "day",
        tz: str = "local",
        conn: Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        if interval not in INTERVALS:
            raise HTTPException(status_code=400, detail="interval must be hour or day")
        if tz not in TZ_CHOICES:
            raise HTTPException(status_code=400, detail="tz must be local or utc")
        start_dt = _parse_local_bound(start, settings.site_timezone)
        end_dt = _parse_local_bound(end, settings.site_timezone)
        _window(start_dt, end_dt, settings.max_window_days)

        bucket_column = "ts_utc" if tz == "utc" else "ts"
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT
                    date_trunc(%s, {bucket_column}) AS bucket,
                    count(*) AS reading_count,
                    min(value) AS min_value,
                    max(value) AS max_value,
                    avg(value) AS avg_value
                FROM transducer.v_reading_utc
                WHERE series_id = %s AND ts >= %s AND ts <= %s
                GROUP BY bucket
                ORDER BY bucket
                """,
                (interval, series_id, start_dt, end_dt),
            )
            rows = list(cur.fetchall())
        return {
            "series_id": series_id,
            "interval": interval,
            "tz": tz,
            "data": _rows(rows),
        }

    app.include_router(router)
    return app


def main() -> int:
    import uvicorn

    uvicorn.run(
        "transducer_api.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=8080,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
