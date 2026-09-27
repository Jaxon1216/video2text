# Both stages share one base image so the build only needs a single pull (or none if it is cached).
# Behind a restricted network, point BASE_IMAGE at a registry mirror you can reach.
ARG BASE_IMAGE=python:3.12-slim
# Optional mirrors, e.g. APT_MIRROR=https://mirrors.aliyun.com
#   PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
#   PLAYWRIGHT_DOWNLOAD_HOST=<mirror> (npmmirror often lacks the newest linux-arm64 builds; cdn.playwright.dev is usually reachable)
ARG APT_MIRROR=""
ARG PIP_INDEX_URL=""
ARG PLAYWRIGHT_DOWNLOAD_HOST=""

# ── 1. Build the React web UI ─────────────────────────────
FROM ${BASE_IMAGE} AS web
ARG APT_MIRROR
RUN if [ -n "$APT_MIRROR" ]; then sed -i "s|http://deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources; fi \
    && echo 'Acquire::Retries "5";' > /etc/apt/apt.conf.d/80retries
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ── 2. Python runtime: API + ASR + Douyin browser ─────────
FROM ${BASE_IMAGE}
ARG APT_MIRROR
ARG PIP_INDEX_URL
ARG PLAYWRIGHT_DOWNLOAD_HOST
RUN if [ -n "$APT_MIRROR" ]; then sed -i "s|http://deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources; fi \
    && echo 'Acquire::Retries "5";' > /etc/apt/apt.conf.d/80retries

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir ${PIP_INDEX_URL:+--index-url "$PIP_INDEX_URL"} "uv>=0.8"

WORKDIR /app
ARG EXTRAS="--extra faster-whisper --extra web --extra douyin --extra volcengine"
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project ${EXTRAS}
COPY src ./src
RUN uv sync --frozen --no-dev ${EXTRAS}

# Chromium plus its system libraries for Douyin page capture.
RUN PLAYWRIGHT_DOWNLOAD_HOST="${PLAYWRIGHT_DOWNLOAD_HOST}" uv run --no-sync playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY --from=web /web/dist ./web/dist

ENV PATH="/app/.venv/bin:$PATH" \
    V2T_HOME=/data \
    HF_HOME=/data/models \
    V2T_WEB_DIST=/app/web/dist \
    V2T_DOUYIN_BROWSER=chromium \
    V2T_TASK_WORKERS=1

VOLUME /data
EXPOSE 8000
CMD ["video2text", "server", "--host", "0.0.0.0", "--port", "8000"]
