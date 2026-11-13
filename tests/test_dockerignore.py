import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def matches_pattern(pattern, path):
    expression = ""
    index = 0
    while index < len(pattern):
        if pattern[index:index + 3] == "**/":
            expression += "(?:.*/)?"
            index += 3
        elif pattern[index:index + 2] == "**":
            expression += ".*"
            index += 2
        elif pattern[index] == "*":
            expression += "[^/]*"
            index += 1
        else:
            expression += re.escape(pattern[index])
            index += 1
    return re.fullmatch(expression, path) is not None


def excluded_from_context(path):
    patterns = (ROOT / ".dockerignore").read_text().splitlines()
    components = path.split("/")
    for end in range(1, len(components) + 1):
        current = "/".join(components[:end])
        excluded = False
        for line in patterns:
            if not line or line.startswith("#"):
                continue
            negated = line.startswith("!")
            pattern = line[1:] if negated else line
            if matches_pattern(pattern.strip("/"), current):
                excluded = not negated
        if excluded:
            return True
    return False


@pytest.mark.parametrize(
    "path",
    [
        ".git/config", ".streamlit/secrets.toml", "nested/.streamlit/secrets.toml",
        ".env", ".env.production", "nested/.env.local", "prod.env",
        ".venv/bin/python", "nested/venv/secret", "__pycache__/app.pyc", "nested/app.pyc",
        ".pytest_cache/value", "nested/.mypy_cache/value", "logs/app.log",
        "temp.tmp", "private.db", "private.db-wal", "backup.dump.gz",
        "data.sql.gz", "backups/archive", "backup/archive", "client_secret_google.json",
        "credentials.json", "private.pem",
    ],
)
def test_sensitive_and_unneeded_paths_are_excluded(path):
    assert excluded_from_context(path)


@pytest.mark.parametrize(
    "path",
    [
        "Dockerfile", "requirements.txt", "start.sh", "streamlit_app.py",
        "auth_config.py", "auth_boundary.py", ".streamlit/config.toml",
        ".env.example", ".env.staging.example", "nested/.env.example",
    ],
)
def test_application_inputs_and_safe_examples_remain_available(path):
    assert not excluded_from_context(path)
