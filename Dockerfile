# ---- Build stage ----
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /uvx /bin/

WORKDIR /app

# Dependencies only. The project itself is not installed: runtime puts src
# on PYTHONPATH instead, so this .venv (~1GB) depends on uv.lock alone and a
# code-only deploy reuses its layer instead of stranding a fresh copy in the
# build cache.
# g++: pdqhash (repurpose.checker) ships no Linux wheel, only a C++ sdist.
# This stage only: runtime copies the compiled .venv (libstdc++ is in slim).
RUN apt-get update && \
    apt-get install -y --no-install-recommends g++ && \
    rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# ---- Test stage (dev only: docker-compose.dev.yml's `tests` service) ----
# Every check runs here, on the same Linux userland as prod, never on the
# dev host. The repo is bind-mounted at /app; the venv lives outside it so a
# host .venv can never leak in. Not built by a plain `docker build`: the
# last stage (runtime) is the default target.
FROM python:3.12-slim AS test

COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /uvx /bin/

# ffmpeg as in runtime; exiftool (Debian's libimage-exiftool-perl) to inspect
# media the app writes; git for pre-commit; node for the static-JS unit tests;
# psql to load the production snapshot the migration tests run on; g++ to
# build pdqhash (no Linux wheel), as in the build stage.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg libimage-exiftool-perl git nodejs postgresql-client ca-certificates g++ && \
    rm -rf /var/lib/apt/lists/*

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project
RUN playwright install --with-deps chromium

# Biome: the JS/CSS linter for src/ofmhelpers/web/static (biome.json). One
# static binary, pinned and checksummed -- no npm toolchain in the image.
ADD --chmod=755     --checksum=sha256:ce247fb644999ef52e5111dd6fd6e471019669fc9c4a44b5699721e39b7032c3     https://github.com/biomejs/biome/releases/download/cli%2Fv1.9.4/biome-linux-x64     /usr/local/bin/biome

# ---- Allure report CLI (scripts/test.sh, scripts/report.sh) ----
# Java lives here, never on the host. Pinned and checksummed (Maven Central's
# published sha256).
FROM eclipse-temurin:21-jre AS allure

ADD --checksum=sha256:37428164f310c8d137cfdd2c3a25d049b233261ddab13e1bd4dfd7627b5cbf8d     https://repo1.maven.org/maven2/io/qameta/allure/allure-commandline/2.46.1/allure-commandline-2.46.1.tgz     /tmp/allure.tgz
RUN tar -xzf /tmp/allure.tgz -C /opt &&     ln -s /opt/allure-2.46.1/bin/allure /usr/local/bin/allure &&     rm /tmp/allure.tgz

WORKDIR /app
ENTRYPOINT ["allure"]

# ---- Runtime stage ----
FROM python:3.12-slim AS runtime

RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --shell /bin/bash --create-home appuser

# ffmpeg for yt-dlp post-processing, curl+unzip for the deno installer
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl unzip ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Install Deno system-wide (not into root's home) so appuser can execute it
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh

WORKDIR /app

COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv

RUN mkdir -p /app/downloads /app/uploads /app/cookies && \
    chown -R appuser:appuser /app/downloads /app/uploads /app/cookies

ENV PATH="/app/.venv/bin:/usr/local/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Alembic config + migrations, so `alembic upgrade head` can run inside the
# container on deploy (see scripts/deploy.sh).
COPY --chown=appuser:appuser alembic.ini /app/alembic.ini
COPY --chown=appuser:appuser alembic /app/alembic

# Code last: it changes every deploy, so only these few-MB layers rebuild.
COPY --chown=appuser:appuser src /app/src

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Production default: no --reload. The reloader belongs to a dev box with
# ./src bind-mounted (docker-compose.dev.yml overrides this command); in
# production it only ever meant "serve half-deployed code".
# --forwarded-allow-ips: nginx sets X-Forwarded-Proto/For, but uvicorn only
# trusts them from 127.0.0.1 by default and the proxy reaches us as the Docker
# bridge gateway. Without this, url_for() builds http:// links on an https page
# (mixed content) and every client collapses into one rate-limit bucket.
# Only loopback + Docker's private range, never "*": nginx APPENDS the peer
# to X-Forwarded-For ($proxy_add_x_forwarded_for), and with "*" uvicorn takes
# the leftmost entry -- whatever the client sent -- so a made-up header per
# request escaped the failed-login limit. With a list, uvicorn takes the
# rightmost untrusted hop: the address nginx saw (prod net 172.18.0.0/16).
CMD ["uvicorn", "ofmhelpers.web.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1,172.16.0.0/12"]
