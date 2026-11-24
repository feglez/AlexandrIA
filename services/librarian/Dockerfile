# syntax=docker/dockerfile:1

# Debian slim with glibc: the prebuilt wheels of onnxruntime, asyncpg and
# pydantic-core exist for it on amd64 and arm64 (there are no onnxruntime
# wheels for Alpine's musl).
ARG PYTHON_IMAGE=python:3.13.18-slim-trixie

# ---------------------------------------------------------------------------
# Dependencies: a virtualenv with everything except the service itself.
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS deps

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /bin/uv

# Compile to bytecode at build time, copy files out of the uv cache mount and
# always use the image's Python, so the virtualenv also works in the runtime
# stage.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /src

# Only rebuilt when pyproject.toml or uv.lock change, not when the code changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --locked --no-dev --no-install-project --no-editable

# ---------------------------------------------------------------------------
# Model: download the embedding model with the locked fastembed version, so
# the container needs no Internet access and starts fast.
# ---------------------------------------------------------------------------
FROM deps AS model

# ONNX Runtime sends usage telemetry to Microsoft unless this is set.
ENV ORT_DISABLE_TELEMETRY=1 \
    HF_HUB_DISABLE_TELEMETRY=1

ARG EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
RUN /app/.venv/bin/python -c "import sys; from fastembed import TextEmbedding; \
TextEmbedding(sys.argv[1], cache_dir='/models')" "${EMBEDDING_MODEL}" \
    && chmod -R a+rX /models

# ---------------------------------------------------------------------------
# Build: install the service into the virtualenv as a regular package.
# ---------------------------------------------------------------------------
FROM deps AS build

COPY pyproject.toml uv.lock ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---------------------------------------------------------------------------
# Runtime stage: only the Python runtime, the virtualenv and the model.
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

# Fixed numeric ids, so the container runtime can check that it is not root.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin app

# Owned by root: the service cannot modify its own code or the model.
COPY --from=build /app/.venv /app/.venv
COPY --from=model /models /models

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FASTEMBED_CACHE_PATH=/models \
    HF_HUB_OFFLINE=1 \
    ORT_DISABLE_TELEMETRY=1

USER 10001:10001
WORKDIR /app

EXPOSE 8000

CMD ["python", "-m", "librarian"]
