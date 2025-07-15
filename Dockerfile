# Use an official Python runtime as a parent image
FROM python:3.13-alpine
# Install UV
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set the working directory
WORKDIR /app

# Copy the lockfile and pyproject.toml into the image
COPY uv.lock /app/uv.lock
COPY pyproject.toml /app/pyproject.toml

# Install dependencies
RUN uv sync --locked --no-install-project

# Copy the project into the image
COPY src/jncep_webui ./app

# Sync the project
RUN uv sync --locked

# Run the application
CMD ["python", "-m", "app"]