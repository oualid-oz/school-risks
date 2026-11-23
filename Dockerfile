# syntax=docker/dockerfile:1
# Build:  docker build --build-arg BUILD_VERSION=$(git describe --always) -t school-risks-api .
# Run:    see docker-compose.yml (the API needs Postgres + PostGIS and Redis).

FROM python:3.14-slim AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.txt requirements.lock /tmp/
RUN python -m venv /venv \
    && /venv/bin/pip install -r /tmp/requirements.txt -c /tmp/requirements.lock

FROM python:3.14-slim
ARG BUILD_VERSION=dev
ENV PATH=/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SRT_LOG_FORMAT=json \
    SRT_BUILD_VERSION=$BUILD_VERSION \
    WEB_CONCURRENCY=2
RUN useradd --system --uid 10001 --no-create-home --home-dir /app app
WORKDIR /app
COPY --from=deps /venv /venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY frontend ./frontend
COPY data/colleges.parquet data/colleges_risques.parquet data/colleges_secteurs.geojson ./data/
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=4)"]
# WEB_CONCURRENCY sets the number of worker processes. Access logs come from the app
# (one JSON line per request with its request id), so uvicorn's own are turned off.
# The log config makes uvicorn's supervisor process log JSON too.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", \
     "--log-config", "app/core/uvicorn_log.json"]
