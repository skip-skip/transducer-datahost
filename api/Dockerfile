# Read-only API image.
#
# Build context is the repository root:
#
#   docker build -f api/Dockerfile -t transducer-api .

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY api/pyproject.toml /app/api/pyproject.toml
COPY api/README.md /app/api/README.md
COPY api/transducer_api /app/api/transducer_api

RUN python -m pip install /app/api \
    && useradd --create-home --uid 10001 appuser

USER appuser

EXPOSE 8080

CMD ["uvicorn", "transducer_api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
