#!/bin/sh
# Container entry point (Render). Starts Temporal's dev server in memory unless
# Temporal Cloud is configured, then the API, which also runs the worker.
set -e

if [ -z "$TEMPORAL_API_KEY" ]; then
  temporal server start-dev --ip 127.0.0.1 --headless --log-level error &
  export TEMPORAL_ADDRESS=127.0.0.1:7233
fi

exec backend/.venv/bin/uvicorn walkworth.api:app --host 0.0.0.0 --port "${PORT:-10000}" --proxy-headers --forwarded-allow-ips='*'
