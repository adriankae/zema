FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY pyproject.toml /app/pyproject.toml
COPY app /app/app
COPY cli /app/cli
COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic
COPY tests /app/tests

RUN python -m pip install --upgrade pip \
    && python -m pip install -e ".[dev]" \
    && python -m pip install /app/cli

EXPOSE 28173

CMD ["sh", "-c", "alembic upgrade head && python -m app.server"]
