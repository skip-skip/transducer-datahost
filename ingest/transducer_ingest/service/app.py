"""FastAPI upload API for the VM-hosted ingest gateway.

The API is deliberately narrow. It streams a file into the spool, enforces a
size limit and the ``</Body_xle>`` completeness marker, and inserts a row in
``ingest.job``. Parsing and loading belong to the worker, which runs as a
separate process with the ingest role.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import JSONResponse
from psycopg import Connection

from . import jobs
from .auth import TokenIdentity, TokenRegistry, make_bearer_dependency
from .log import configure as configure_logging
from .settings import IngestSettings
from .spool import EmptyUpload, Spool, UploadTooLarge

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
STATUSES = {"queued", "processing", "done", "failed"}
MAX_LIST_LIMIT = 500

logger = logging.getLogger("transducer.ingest.api")


def sanitize_filename(value: str | None) -> str:
    name = Path(value or "").name.strip()
    name = "".join(
        character
        for character in name
        if character.isprintable() and character not in '\\/:*?"<>|'
    )
    return name[:255] or "upload.xle"


def _jsonable_job(job: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in job.items():
        if isinstance(value, datetime):
            payload[key] = value.isoformat()
        elif key == "source_ip" and value is not None:
            payload[key] = str(value)
        else:
            payload[key] = value
    return payload


def create_app(settings: IngestSettings | None = None) -> FastAPI:
    configure_logging()
    settings = settings or IngestSettings.from_env()
    registry = TokenRegistry(settings.tokens)
    require_token = make_bearer_dependency(registry)
    spool = Spool(settings.spool_dir)
    spool.ensure()

    app = FastAPI(
        title="Transducer ingest API",
        version="1.0.0",
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
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

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.get("/health")
    def health() -> JSONResponse:
        database = "ok"
        worker = "unknown"
        latest: datetime | None = None
        try:
            with settings.database.connect() as conn:
                conn.execute("SELECT 1")
                latest = jobs.latest_heartbeat(conn)
        except Exception:  # noqa: BLE001 - health reports, never raises
            database = "unavailable"

        if latest is not None:
            age = (datetime.now(UTC) - latest).total_seconds()
            worker = (
                "ok"
                if age <= settings.worker_heartbeat_timeout_seconds
                else "stale"
            )

        writable = spool.writable()
        status = "ok"
        code = 200
        if database != "ok" or not writable:
            status = "unavailable"
            code = 503
        elif worker == "stale":
            status = "degraded"

        return JSONResponse(
            status_code=code,
            content={
                "status": status,
                "database": database,
                "spool": "ok" if writable else "unavailable",
                "worker": worker,
                "worker_last_seen": latest.isoformat() if latest else None,
                "disk_free_bytes": spool.disk_free_bytes(),
            },
        )

    @router.post("/v1/uploads", status_code=201)
    def upload(
        request: Request,
        file: UploadFile = File(...),
        identity: TokenIdentity = Depends(require_token),
        conn: Connection = Depends(get_conn),
    ) -> JSONResponse:
        filename = sanitize_filename(file.filename)
        if not filename.lower().endswith(".xle"):
            raise HTTPException(status_code=400, detail="only .xle files are accepted")

        try:
            saved = spool.save_stream(file.file, settings.max_upload_bytes)
        except UploadTooLarge as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except EmptyUpload as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            logger.exception("spool write failed")
            raise HTTPException(status_code=500, detail="spool write failed") from exc

        try:
            if not spool.is_complete(saved.path):
                raise HTTPException(
                    status_code=400,
                    detail="upload is incomplete: final content must be </Body_xle>",
                )

            existing = jobs.get_job_by_sha(conn, saved.sha256)
            if existing is not None:
                saved.path.unlink(missing_ok=True)
                return JSONResponse(
                    status_code=200,
                    content=_jsonable_job(existing),
                )

            spool.finalize(saved)
            source_ip = request.client.host if request.client else None
            job, created = jobs.enqueue_job(
                conn,
                sha256=saved.sha256,
                filename=filename,
                byte_size=saved.byte_size,
                source_client=identity.label,
                source_ip=source_ip,
            )
            conn.commit()

            if not created:
                # Another upload created the job between our HEAD check and
                # this insert. The spool path is content-addressed, so the
                # queued file is shared: keep it while the job still needs it,
                # and match the terminal state otherwise.
                if job["status"] == "done":
                    spool.move(saved.sha256, "queued", "processed")
                elif job["status"] == "failed":
                    spool.move(saved.sha256, "queued", "failed")

            logger.info(
                "upload accepted",
                extra={
                    "request_id": getattr(request.state, "request_id", None),
                    "token_label": identity.label,
                    "source_ip": source_ip,
                    "sha256": saved.sha256,
                    "source_filename": filename,
                    "status": job["status"],
                },
            )
            return JSONResponse(
                status_code=201 if created else 200,
                content=_jsonable_job(job),
            )
        except HTTPException:
            saved.path.unlink(missing_ok=True)
            raise
        except Exception:
            # The file may already be in queued/. The worker's orphan sweep
            # reconciles it on the next start.
            saved.path.unlink(missing_ok=True)
            logger.exception("enqueue failed")
            raise HTTPException(
                status_code=500, detail="could not enqueue upload"
            ) from None

    @router.head("/v1/files/{sha256}")
    def file_exists(
        sha256: str,
        conn: Connection = Depends(get_conn),
    ) -> Response:
        if not SHA256_RE.fullmatch(sha256):
            raise HTTPException(status_code=400, detail="invalid sha256")
        if jobs.get_job_by_sha(conn, sha256) is not None:
            return Response(status_code=204)
        if jobs.file_known(conn, sha256):
            return Response(status_code=204)
        return Response(status_code=404)

    @router.get("/v1/uploads/{job_id}")
    def get_upload(
        job_id: int,
        conn: Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        job = jobs.get_job(conn, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return _jsonable_job(job)

    @router.get("/v1/uploads")
    def list_uploads(
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        conn: Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        if status is not None and status not in STATUSES:
            raise HTTPException(status_code=400, detail="invalid status filter")
        limit = max(1, min(limit, MAX_LIST_LIMIT))
        offset = max(0, offset)
        rows = jobs.list_jobs(conn, status=status, limit=limit, offset=offset)
        return {
            "data": [_jsonable_job(row) for row in rows],
            "pagination": {"limit": limit, "offset": offset, "count": len(rows)},
        }

    @router.post("/v1/uploads/{job_id}/retry")
    def retry_upload(
        job_id: int,
        conn: Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        job = jobs.get_job(conn, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        retried = jobs.retry_job(conn, job_id)
        if retried is None:
            raise HTTPException(
                status_code=409, detail="only failed jobs can be retried"
            )
        spool.move(job["sha256"], "failed", "queued")
        conn.commit()
        return _jsonable_job(retried)

    app.include_router(router)
    return app
