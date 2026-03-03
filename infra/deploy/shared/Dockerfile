FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ripgrep git openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies + package in an isolated project venv.
COPY pyproject.toml uv.lock ./
COPY kb ./kb
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:${PATH}"

# Runtime repo is mounted at /repo (must include .git and data/).
WORKDIR /repo

CMD ["kb", "mcp-server", "--project-root", "/repo", "--data-root", "/repo/data", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8001", "--path", "/mcp"]
