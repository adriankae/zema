FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY pyproject.toml /app/pyproject.toml
COPY app /app/app
COPY cli /app/cli

RUN python -m pip install --upgrade pip \
    && python -m pip install -e . \
    && python -m pip install /app/cli

CMD ["python", "-m", "app.telegram_runtime"]
