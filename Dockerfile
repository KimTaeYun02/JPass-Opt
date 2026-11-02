# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm AS dependencies

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false

# Keep Poetry in the build stage and the application environment at a stable path.
RUN pip install --no-cache-dir poetry==2.5.1 \
    && python -m venv /opt/venv

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY backend/pyproject.toml backend/poetry.lock ./
RUN poetry install --only main --no-root --no-ansi


FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY --from=dependencies /opt/venv /opt/venv

# Install Chromium with the exact Playwright version selected by poetry.lock.
# Headed crawling needs Xvfb, xauth and Japanese/Korean fonts at runtime.
RUN export DEBIAN_FRONTEND=noninteractive \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        tini xvfb xauth fonts-noto-cjk tzdata \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app

WORKDIR /app/backend
COPY --chown=10001:10001 backend/app ./app

USER 10001:10001
EXPOSE 8000

# Checks the HTTP server without consuming Google/Gemini quota or crawling.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/openapi.json', timeout=3).close()"]

# Forward stop signals to xvfb-run and Uvicorn, and reap browser child processes.
ENTRYPOINT ["/usr/bin/tini", "-g", "--"]
CMD ["xvfb-run", "-a", "-s", "-screen 0 1920x1080x24", "python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
