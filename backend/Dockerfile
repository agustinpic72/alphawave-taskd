# syntax=docker/dockerfile:1

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=8711 \
    DATABASE_URL=sqlite:////app/data/alphawave-taskd.sqlite \
    LOG_FILE=/app/data/alphawave-taskd.log \
    PYTHONPATH=/app/backend

WORKDIR /app

RUN groupadd --system --gid 10001 alphawave \
    && useradd --system --uid 10001 --gid alphawave --home-dir /app --shell /usr/sbin/nologin alphawave

COPY backend/pyproject.toml /app/backend/pyproject.toml
COPY backend/app /app/backend/app
RUN python -m pip install --no-cache-dir /app/backend

COPY scripts/admin/create_owner.py /app/scripts/admin/create_owner.py

RUN mkdir -p /app/data/backups /app/backups \
    && chown -R alphawave:alphawave /app/data /app/backups

USER alphawave

EXPOSE 8711

CMD ["python", "-m", "app.main"]
