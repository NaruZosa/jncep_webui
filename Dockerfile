# Reference: https://github.com/astral-sh/uv-docker-example/blob/main/multistage.Dockerfile
# Use an official Python runtime as a parent image
FROM python:3.13-alpine AS base

FROM base AS builder
# Install UV
COPY --from=ghcr.io/astral-sh/uv:0.7.21 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Disable Python downloads, because we want to use the system interpreter
# across both images. If using a managed Python version, it needs to be
# copied from the build image into the final image; see `standalone.Dockerfile`
# for an example.
ENV UV_PYTHON_DOWNLOADS=0

WORKDIR /app
COPY uv.lock pyproject.toml readme.md /app/
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev
COPY ./src /app/src
RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --locked --no-dev

# Then, use a final image without uv
FROM base
# Copy the application from the builder
COPY --from=builder --chown=app:app /app /app
# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"
# Run the application
CMD ["python", "-m", "jncep_webui"]