# syntax=docker/dockerfile:1

# Debian slim with glibc: the prebuilt wheels of asyncpg and pydantic-core
# (and onnxruntime in librarian) exist for it on amd64 and arm64.
ARG PYTHON_IMAGE=python:3.13.18-slim-trixie

# ---------------------------------------------------------------------------
# Build stage: install the dependencies and the service into a virtualenv.
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS build

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /bin/uv

# Compile to bytecode at build time, copy files out of the uv cache mount and
# always use the image's Python, so the virtualenv also works in the runtime
# stage.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /src

# Dependencies first, in their own layer: it is only rebuilt when
# pyproject.toml or uv.lock change, not when the code changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --locked --no-dev --no-install-project --no-editable

# Then the service itself, installed into the virtualenv as a regular package.
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---------------------------------------------------------------------------
# Runtime stage: only the Python runtime and the virtualenv.
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

# Fixed numeric ids, so the container runtime can check that it is not root.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin app

# Owned by root: the service cannot modify its own code.
COPY --from=build /app/.venv /app/.venv

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER 10001:10001
WORKDIR /app

# 8000: public API. 8001: internal API (never published).
EXPOSE 8000 8001

CMD ["python", "-m", "observer"]
