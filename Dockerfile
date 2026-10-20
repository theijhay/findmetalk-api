# Keep Python on 3.12 and use Bookworm's supported package repositories.
FROM python:3.12-slim-bookworm

# No .pyc files, and output is logged instantly (not buffered).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt .
# The wheel cache persists between builds, so a changed pin downloads only what
# changed and an interrupted build keeps what it already fetched; the long
# timeout and retries let a build finish on a slow line.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 120 --retries 10 -r requirements.txt

COPY . .

# Never run the API as root.
RUN useradd --system --uid 10001 --no-create-home app
USER app

EXPOSE 8000

# Let the platform's SIGTERM reach Uvicorn directly so FastAPI lifespan can
# close Postgres and Redis cleanly.
STOPSIGNAL SIGTERM

# Migrations are applied as a separate step before a deploy (README), never by
# container start. `--proxy-headers` because the platform terminates TLS.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${APP_PORT:-8000} --proxy-headers --no-access-log"]
