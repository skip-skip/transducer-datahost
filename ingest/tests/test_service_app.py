"""App-level tests for the ingest API.

These use a fake database so they exercise routing, authentication, and spool
handling without PostgreSQL. The queue SQL itself is covered by
``test_service_jobs.py``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from transducer_ingest.service.app import _jsonable_job, create_app, sanitize_filename
from transducer_ingest.service.settings import IngestSettings

TOKEN = "token"
SHA = "a" * 64
COMPLETE_XLE = b"<Body_xle><Log id=\"1\"></Log></Body_xle>"


class FakeResult:
    def fetchone(self):
        return None

    def fetchall(self):
        return []


class FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, *args, **kwargs):
        return FakeResult()

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class FakeConnection:
    def cursor(self, row_factory=None):
        return FakeCursor()

    def execute(self, *args, **kwargs):
        return FakeResult()

    def commit(self):
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


def make_settings(spool_dir: Path) -> IngestSettings:
    return IngestSettings(
        database=FakeDatabase(),
        tokens={"workstation": TOKEN},
        spool_dir=spool_dir,
    )


def make_job(status: str = "queued", **overrides) -> dict:
    job = {
        "job_id": 7,
        "sha256": SHA,
        "filename": "logger.xle",
        "byte_size": len(COMPLETE_XLE),
        "source_client": "workstation",
        "source_ip": "127.0.0.1",
        "status": status,
        "import_status": None,
        "file_id": None,
        "attempt_count": 0,
        "next_attempt_at": None,
        "locked_at": None,
        "received_at": None,
        "started_at": None,
        "finished_at": None,
        "error": None,
    }
    job.update(overrides)
    return job


def test_sanitize_filename_strips_paths_and_reserved_characters() -> None:
    assert sanitize_filename(r"C:\Users\me\export\file.xle") == "file.xle"
    assert sanitize_filename("a/b:c*d?.xle") == "bcd.xle"
    assert sanitize_filename(None) == "upload.xle"


def test_jsonable_job_serializes_dates_and_addresses() -> None:
    from datetime import datetime

    payload = _jsonable_job(
        {
            "job_id": 1,
            "received_at": datetime(2026, 1, 15, 12, 0),
            "source_ip": "127.0.0.1",
        }
    )

    assert payload["received_at"] == "2026-01-15T12:00:00"
    assert payload["source_ip"] == "127.0.0.1"


def test_health_does_not_require_a_token(tmp_path: Path) -> None:
    client = TestClient(create_app(make_settings(tmp_path)))

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["database"] == "ok"
    assert body["spool"] == "ok"
    assert body["worker"] == "unknown"


def test_endpoints_require_a_bearer_token(tmp_path: Path) -> None:
    client = TestClient(create_app(make_settings(tmp_path)))

    response = client.get("/v1/uploads")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_invalid_token_is_rejected(tmp_path: Path) -> None:
    client = TestClient(create_app(make_settings(tmp_path)))

    response = client.get(
        "/v1/uploads", headers={"Authorization": "Bearer wrong"}
    )

    assert response.status_code == 401


def test_unauthenticated_request_does_not_open_a_database_connection(
    tmp_path: Path,
) -> None:
    class ExplodingDatabase:
        def connect(self):
            raise AssertionError("database connection opened before authentication")

    settings = IngestSettings(
        database=ExplodingDatabase(),
        tokens={"workstation": TOKEN},
        spool_dir=tmp_path,
    )
    client = TestClient(create_app(settings))

    response = client.get("/v1/uploads")

    assert response.status_code == 401


def test_list_uploads_returns_an_empty_page(tmp_path: Path) -> None:
    client = TestClient(create_app(make_settings(tmp_path)))

    response = client.get(
        "/v1/uploads", headers={"Authorization": f"Bearer {TOKEN}"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "data": [],
        "pagination": {"limit": 50, "offset": 0, "count": 0},
    }


def test_head_rejects_an_invalid_sha256(tmp_path: Path) -> None:
    client = TestClient(create_app(make_settings(tmp_path)))

    response = client.head(
        "/v1/files/not-a-hash", headers={"Authorization": f"Bearer {TOKEN}"}
    )

    assert response.status_code == 400


def test_head_reports_unknown_and_known_files(
    tmp_path: Path, monkeypatch
) -> None:
    from transducer_ingest.service import jobs

    client = TestClient(create_app(make_settings(tmp_path)))
    headers = {"Authorization": f"Bearer {TOKEN}"}

    assert client.head(f"/v1/files/{SHA}", headers=headers).status_code == 404

    monkeypatch.setattr(jobs, "file_known", lambda conn, sha: True)
    assert client.head(f"/v1/files/{SHA}", headers=headers).status_code == 204


def test_upload_rejects_a_non_xle_file(tmp_path: Path) -> None:
    client = TestClient(create_app(make_settings(tmp_path)))

    response = client.post(
        "/v1/uploads",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert response.status_code == 400
    assert "only .xle" in response.json()["detail"]


def test_upload_rejects_an_incomplete_file(tmp_path: Path) -> None:
    client = TestClient(create_app(make_settings(tmp_path)))

    response = client.post(
        "/v1/uploads",
        files={"file": ("partial.xle", b"<Body_xle>", "application/xml")},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert response.status_code == 400
    assert "incomplete" in response.json()["detail"]


def test_upload_enqueues_a_complete_file(tmp_path: Path, monkeypatch) -> None:
    import hashlib

    from transducer_ingest.service import jobs

    client = TestClient(create_app(make_settings(tmp_path)))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    digest = hashlib.sha256(COMPLETE_XLE).hexdigest()

    def fake_enqueue(conn, **kwargs):
        return (
            make_job(
                sha256=kwargs["sha256"],
                filename=kwargs["filename"],
                byte_size=kwargs["byte_size"],
                source_client=kwargs["source_client"],
                source_ip=kwargs["source_ip"],
            ),
            True,
        )

    monkeypatch.setattr(jobs, "enqueue_job", fake_enqueue)

    response = client.post(
        "/v1/uploads",
        files={"file": ("logger.xle", COMPLETE_XLE, "application/xml")},
        headers=headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["job_id"] == 7
    assert body["filename"] == "logger.xle"
    assert body["source_client"] == "workstation"
    assert body["status"] == "queued"

    queued = list((tmp_path / "queued").glob("*.xle"))
    assert len(queued) == 1
    assert queued[0].name == f"{digest}.xle"
    assert queued[0].read_bytes() == COMPLETE_XLE


def _upload_race(tmp_path: Path, monkeypatch, status: str):
    import hashlib

    from transducer_ingest.service import jobs

    client = TestClient(create_app(make_settings(tmp_path)))
    digest = hashlib.sha256(COMPLETE_XLE).hexdigest()

    def fake_enqueue(conn, **kwargs):
        return make_job(status=status, sha256=kwargs["sha256"]), False

    monkeypatch.setattr(jobs, "enqueue_job", fake_enqueue)
    response = client.post(
        "/v1/uploads",
        files={"file": ("logger.xle", COMPLETE_XLE, "application/xml")},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    return response, digest


def test_lost_upload_race_keeps_the_file_while_queued(
    tmp_path: Path, monkeypatch
) -> None:
    response, digest = _upload_race(tmp_path, monkeypatch, "queued")

    assert response.status_code == 200
    # The winning job still needs this file, so it must not be deleted.
    assert (tmp_path / "queued" / f"{digest}.xle").exists()


def test_lost_upload_race_moves_the_file_for_a_done_job(
    tmp_path: Path, monkeypatch
) -> None:
    response, digest = _upload_race(tmp_path, monkeypatch, "done")

    assert response.status_code == 200
    assert not (tmp_path / "queued" / f"{digest}.xle").exists()
    assert (tmp_path / "processed" / f"{digest}.xle").exists()


def test_lost_upload_race_moves_the_file_for_a_failed_job(
    tmp_path: Path, monkeypatch
) -> None:
    response, digest = _upload_race(tmp_path, monkeypatch, "failed")

    assert response.status_code == 200
    assert not (tmp_path / "queued" / f"{digest}.xle").exists()
    assert (tmp_path / "failed" / f"{digest}.xle").exists()
