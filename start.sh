#!/usr/bin/env sh
set -eu

# Railpack and many PaaS platforms provide PORT at runtime.
PORT="${PORT:-8080}"

python3 -m http.server "$PORT" --bind 0.0.0.0