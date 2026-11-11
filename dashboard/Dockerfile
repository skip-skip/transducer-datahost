# Dashboard image.
#
# Build context is the repository root:
#
#   docker build -f dashboard/Dockerfile -t transducer-dashboard .

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY dashboard/pyproject.toml /app/dashboard/pyproject.toml
COPY dashboard/README.md /app/dashboard/README.md
COPY dashboard/transducer_dashboard /app/dashboard/transducer_dashboard

RUN python -m pip install /app/dashboard \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p /var/lib/transducer-dashboard \
    && chown -R appuser:appuser /var/lib/transducer-dashboard

USER appuser

EXPOSE 8080

CMD ["uvicorn", "transducer_dashboard.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
