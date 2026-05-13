#!/usr/bin/env sh
set -eu

# Railpack and many PaaS platforms provide PORT at runtime.
PORT="${PORT:-8080}"

streamlit run streamlit_app.py --server.port "$PORT" --server.address 0.0.0.0