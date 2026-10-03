ARG PYTHON_IMAGE=python:3.12.15-slim-bookworm

FROM ${PYTHON_IMAGE} AS builder

ARG PIP_VERSION=26.2.1

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY pyproject.toml README.md ./
COPY app ./app

RUN python -m pip install --upgrade "pip==${PIP_VERSION}" \
    && python -m pip wheel --wheel-dir /wheels .


FROM ${PYTHON_IMAGE} AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 10001 app \
    && useradd \
        --uid 10001 \
        --gid app \
        --no-create-home \
        --shell /usr/sbin/nologin \
        app \
    && python -m venv /opt/venv

COPY --from=builder /wheels /wheels

RUN python -m pip install \
        --no-cache-dir \
        --no-index \
        --find-links=/wheels \
        document-search-api \
    && rm -rf /wheels

WORKDIR /app

COPY --chown=10001:10001 alembic.ini ./
COPY --chown=10001:10001 migrations ./migrations

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()"]

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
