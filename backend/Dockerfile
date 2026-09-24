FROM ghcr.io/astral-sh/uv:0.12.9@sha256:8b940d3a9d65bed080436972241af2e21c84b5e8c9193f7014ed71479ee795ff AS uv

FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9 AS builder

COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_CONCURRENT_BUILDS=1 \
    UV_CONCURRENT_DOWNLOADS=2 \
    UV_CONCURRENT_INSTALLS=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /opt/reader
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --locked --no-dev --no-install-project --no-editable
COPY backend/src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9

LABEL io.reader.browser-runtime.component="adapter" \
      io.reader.browser-runtime.protocol-version="1" \
      io.reader.browser-runtime.adapter-policy-version="reader-browser-container-v1"

ENV PATH=/opt/reader/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 20000 reader-state \
    && groupadd --gid 10001 reader \
    && useradd --no-log-init --uid 10001 --gid 10001 --groups 20000 \
       --home-dir /home/reader --create-home reader \
    && useradd --no-log-init --uid 10002 --gid 10001 --groups 20000 \
       --home-dir /home/reader-reaper --create-home reader-reaper \
    && mkdir -p /opt/reader /var/lib/reader-browser \
    && chown 10001:20000 /var/lib/reader-browser

WORKDIR /opt/reader
COPY --from=builder /opt/reader/.venv /opt/reader/.venv
COPY backend/alembic.ini ./alembic.ini
COPY backend/migrations ./migrations
COPY backend/docker-entrypoint.py /usr/local/bin/reader-container-entrypoint
COPY backend/docker-state-init.sh /usr/local/bin/reader-state-init
COPY backend/docker-codex-auth-init.sh /usr/local/bin/reader-codex-auth-init
COPY services/browser_runtime/init-browser-task-volume /reader/init-browser-task-volume
RUN chmod 0555 \
      /usr/local/bin/reader-container-entrypoint \
      /usr/local/bin/reader-state-init \
      /usr/local/bin/reader-codex-auth-init \
      /reader/init-browser-task-volume

USER 10001:10001
ENTRYPOINT ["reader-container-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
