# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 AS uv

FROM python:3.12.12-slim-trixie@sha256:f3fa41d74a768c2fce8016b98c191ae8c1bacd8f1152870a3f9f87d350920b7c

COPY --from=uv /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    HOME=/tmp

RUN apt-get update \
    && apt-get install --yes --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

WORKDIR /app

COPY pyproject.toml uv.lock requirements-api.lock requirements-ui.lock requirements-container.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project \
    && uv pip install --python .venv/bin/python --require-hashes \
        -r requirements-ui.lock -r requirements-container.lock \
    && uv pip check --python .venv/bin/python

COPY src ./src
COPY app ./app
COPY examples ./examples
COPY .streamlit ./.streamlit
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --inexact \
    && uv pip check --python .venv/bin/python \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000 8502

CMD [".venv/bin/python", "-m", "uvicorn", "bank_campaign.api:app", "--host", "0.0.0.0", "--port", "8000"]
