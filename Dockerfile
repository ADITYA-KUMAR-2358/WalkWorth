# One container for Render's free plan: the FastAPI app (which also serves the
# built web app and runs the Temporal worker) plus Temporal's dev server.
# Set TEMPORAL_ADDRESS / TEMPORAL_NAMESPACE / TEMPORAL_API_KEY to use Temporal
# Cloud instead; then the dev server isn't started.

# ---- Web app -------------------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /app
COPY web/package.json web/package-lock.json web/
RUN npm --prefix web ci
COPY fortpacks fortpacks
COPY web web
RUN npm --prefix web run build

# ---- API, worker, Temporal --------------------------------------------------------
FROM python:3.12-slim
# Pinned so a rebuild gets the same tools that were tested.
ARG TEMPORAL_CLI_VERSION=1.9.1
# Pango and HarfBuzz for WeasyPrint (PDF export); curl to fetch the Temporal CLI.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL "https://temporal.download/cli/archive/v${TEMPORAL_CLI_VERSION}?platform=linux&arch=amd64" | tar -xz -C /usr/local/bin temporal
COPY --from=ghcr.io/astral-sh/uv:0.10.6 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
COPY backend/pyproject.toml backend/uv.lock backend/
RUN uv sync --project backend --frozen --no-dev --no-install-project
COPY backend backend
RUN uv sync --project backend --frozen --no-dev
COPY walkworth.config.toml ./
COPY fortpacks fortpacks
COPY fixtures fixtures
COPY scripts/start.sh scripts/start.sh
COPY --from=web /app/web/dist web/dist

# Run unprivileged; only the data directory needs to be writable.
RUN useradd --system --uid 10001 --home-dir /app walkworth \
 && mkdir -p /app/data && chown walkworth /app/data
USER walkworth

# Caches (fontconfig for the PDF, the Temporal CLI) go to a writable HOME.
ENV WALKWORTH_EMBEDDED_WORKER=1 HOME=/tmp
EXPOSE 10000
CMD ["sh", "scripts/start.sh"]
