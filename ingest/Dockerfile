# Ingest image: upload API, queue worker, and migration runner.
#
# Build context is the repository root so the image can include both the
# Python package and db/migrations:
#
#   docker build -f ingest/Dockerfile -t transducer-ingest .
#
# The default command runs the worker. Compose overrides it for the API and the
# one-shot migration service.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MIGRATIONS_DIR=/app/db/migrations \
    SPOOL_DIR=/var/lib/transducer

WORKDIR /app

COPY ingest/pyproject.toml /app/ingest/pyproject.toml
COPY ingest/README.md /app/ingest/README.md
COPY ingest/transducer_ingest /app/ingest/transducer_ingest

RUN python -m pip install "/app/ingest[service]"

COPY db/migrations /app/db/migrations

# The spool is a named volume. Creating the directories in the image gives the
# volume the same ownership when Docker initializes it.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p \
        /var/lib/transducer/incoming \
        /var/lib/transducer/queued \
        /var/lib/transducer/processed \
        /var/lib/transducer/failed \
    && chown -R appuser:appuser /var/lib/transducer

USER appuser

CMD ["python", "-m", "transducer_ingest.service.worker"]
