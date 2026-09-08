#!/usr/bin/env sh
set -eu

# Railpack and many PaaS platforms provide PORT at runtime.
PORT="${PORT:-8080}"

case "${ALERT_SCHEDULER_ENABLED:-true}" in
	true|TRUE|1|yes|YES|on|ON)
	python alert_scheduler.py &
	SCHEDULER_PID=$!
	trap 'kill "$SCHEDULER_PID" 2>/dev/null || true' EXIT INT TERM
	;;
esac

streamlit run streamlit_app.py --server.port "$PORT" --server.address 0.0.0.0
