# syntax=docker/dockerfile:1
# The anonymizer: a Cloud Run service (linux/amd64) with both GLiNER models baked in at their pinned commits.
# Build:  docker build --platform linux/amd64 -t s1d1-anonymizer:dev anonymizer/
# python:3.12-slim, index digest of 2026-10-24.
ARG BASE=python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

# uv 0.11.26, the version CI uses (pinned by index digest).
FROM ghcr.io/astral-sh/uv:0.11.26@sha256:3d868e555f8f1dbc324afa005066cd11e1053fc4743b9808ca8025283e65efa5 AS uv

FROM ${BASE} AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock README.md ./
# Dependencies first (cached layer): CPU torch from the PyTorch CPU index (pyproject.toml), gliner via the
# `model` extra, no dev group. Then the project itself.
RUN uv sync --frozen --extra model --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --extra model --no-dev --no-editable
COPY scripts ./scripts
# The models: the only step that downloads. The two commits are pinned in src/anonymizer/detector.py
# (MODELS, ENCODER_PINS); the script stops if the encoder's repository moved.
RUN /app/.venv/bin/python scripts/fetch_models.py /model

FROM ${BASE}
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin anonymizer
COPY --from=build /app/.venv /app/.venv
COPY --from=build /model /model
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    GLINER_MODEL_DIRS=/model/knowledgator,/model/urchade \
    PORT=8080
USER anonymizer
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn anonymizer.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
