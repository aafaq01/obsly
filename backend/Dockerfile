FROM python:3.13-slim

# Pinned, not :latest — a build that changes its own toolchain is not reproducible.
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies before source, so editing a view does not reinstall the world.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY . .

# Unprivileged. staticfiles is written by collectstatic at startup, so it must be owned by the
# runtime user rather than root.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/staticfiles \
    && chown -R app:app /app
USER app

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
