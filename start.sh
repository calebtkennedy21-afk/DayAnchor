#!/usr/bin/env sh
set -eu

# Railpack and many PaaS platforms provide PORT at runtime.
PORT="${PORT:-8080}"
SCHEDULER_PID=""
AUTH_SECRETS_DIR=""

cleanup() {
	if [ -n "$SCHEDULER_PID" ]; then
		kill "$SCHEDULER_PID" 2>/dev/null || true
	fi
	if [ -n "$AUTH_SECRETS_DIR" ]; then
		rm -f "$AUTH_SECRETS_DIR/secrets.toml"
		rmdir "$AUTH_SECRETS_DIR"
	fi
}
trap cleanup EXIT INT TERM

case "${ALERT_SCHEDULER_ENABLED:-true}" in
	true|TRUE|1|yes|YES|on|ON)
	(
		unset DAYANCHOR_GOOGLE_CLIENT_ID DAYANCHOR_GOOGLE_CLIENT_SECRET \
			DAYANCHOR_GOOGLE_REDIRECT_URI DAYANCHOR_AUTH_COOKIE_SECRET \
			DAYANCHOR_OWNER_GOOGLE_SUB
		exec python alert_scheduler.py
	) &
	SCHEDULER_PID=$!
	;;
esac

AUTH_SECRETS_DIR="$(mktemp -d)"
python auth_config.py "$AUTH_SECRETS_DIR/secrets.toml" "$PORT"
