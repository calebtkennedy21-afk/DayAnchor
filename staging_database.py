"""Fail-closed PostgreSQL target validation for explicitly configured staging."""

import ipaddress
import os
import re
import shlex
import sys
from urllib.parse import parse_qsl, urlsplit

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo


ERROR = "Staging database configuration is invalid. Check the staging target and database variables."
ALIASES = ("DATABASE_PUBLIC_URL", "DATABASE_PRIVATE_URL", "POSTGRES_URL", "POSTGRESQL_URL")
EXPECTED = {
    "host": "DAYANCHOR_STAGING_DB_HOST",
    "port": "DAYANCHOR_STAGING_DB_PORT",
    "dbname": "DAYANCHOR_STAGING_DB_NAME",
    "user": "DAYANCHOR_STAGING_DB_USER",
}
ALLOWED_PARAMETERS = frozenset({"host", "port", "dbname", "user", "password", "sslmode", "connect_timeout"})
SECURE_SSL_MODES = frozenset({"require", "verify-ca", "verify-full"})
NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\Z")
DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")


class StagingDatabaseError(RuntimeError):
    pass


def is_staging(environ=None):
    env = os.environ if environ is None else environ
    mode = env.get("DAYANCHOR_DEPLOYMENT_ENV")
    if mode is None or mode == "production":
        return False
    if mode == "staging":
        return True
    raise StagingDatabaseError(
        "Invalid deployment mode. Use exactly 'production' or 'staging'."
    )


def _fail():
    raise StagingDatabaseError(ERROR) from None


def _host_is_valid(host):
    try:
        ipaddress.ip_address(host)
        return "%" not in host
    except ValueError:
        labels = host.split(".")
        return len(host) <= 253 and all(DNS_LABEL.fullmatch(label) for label in labels)


def _check_syntax(candidate):
    if candidate.startswith(("postgresql://", "postgres://")):
        try:
            parts = urlsplit(candidate)
            query = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            _fail()
        if parts.fragment or any(key not in ("sslmode", "connect_timeout") for key, _ in query):
            _fail()
        if len({key for key, _ in query}) != len(query):
            _fail()
    else:
        try:
            tokens = shlex.split(candidate)
        except ValueError:
            _fail()
        keys = []
        for token in tokens:
            key, separator, _ = token.partition("=")
            if not separator or key not in ALLOWED_PARAMETERS:
                _fail()
            keys.append(key)
        if len(set(keys)) != len(keys):
            _fail()


def validate_staging_candidate(candidate, environ=None):
    env = os.environ if environ is None else environ
    configured = env.get("DATABASE_URL")
    if not configured or candidate != configured or configured != configured.strip():
        _fail()
    if any(env.get(name) for name in ALIASES) or any(
        name.startswith("PG") and value for name, value in env.items()
    ):
        _fail()
    _check_syntax(candidate)
    try:
        params = conninfo_to_dict(candidate)
    except psycopg.Error:
        _fail()
    if set(params) - ALLOWED_PARAMETERS or any(not params.get(key) for key in (*EXPECTED, "password")):
        _fail()
    if params.get("sslmode") not in SECURE_SSL_MODES:
        _fail()
    expected = {key: env.get(variable) for key, variable in EXPECTED.items()}
    if any(not isinstance(value, str) or not value or value != value.strip() for value in expected.values()):
        _fail()
    if not _host_is_valid(params["host"]) or not _host_is_valid(expected["host"]):
        _fail()
    if not NAME.fullmatch(params["dbname"]) or not NAME.fullmatch(params["user"]):
        _fail()
    if len(params["port"]) > 5 or not params["port"].isascii() or not params["port"].isdigit():
        _fail()
    if len(expected["port"]) > 5 or not expected["port"].isascii() or not expected["port"].isdigit():
        _fail()
    if not (1 <= int(params["port"]) <= 65535) or int(params["port"]) != int(expected["port"]):
        _fail()
    if any(params[key] != expected[key] for key in ("host", "dbname", "user")):
        _fail()
    try:
        return make_conninfo(candidate, gssencmode="disable")
    except psycopg.Error:
        _fail()


def staging_candidate(environ=None):
    env = os.environ if environ is None else environ
    candidate = env.get("DATABASE_URL")
    validate_staging_candidate(candidate, env)
    return candidate


def connect(candidate, connector, environ=None):
    if not is_staging(environ):
        return connector(candidate)
    conninfo = validate_staging_candidate(candidate, environ)
    try:
        return connector(conninfo)
    except psycopg.Error:
        raise StagingDatabaseError(
            "Staging database connection failed. Check the staging database configuration and availability."
        ) from None


def preflight(environ=None):
    env = os.environ if environ is None else environ
    if not is_staging(env):
        raise StagingDatabaseError("Staging preflight requires DAYANCHOR_DEPLOYMENT_ENV=staging.")
    staging_candidate(env)


if __name__ == "__main__":
    try:
        preflight()
    except StagingDatabaseError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
