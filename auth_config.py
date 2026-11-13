"""Create a private Streamlit OIDC secrets file from deployment variables."""

import json
import os
import sys

from auth_boundary import GOOGLE_METADATA_URL
from streamlit import config


AUTH_VARIABLES = {
    "client_id": "DAYANCHOR_GOOGLE_CLIENT_ID",
    "client_secret": "DAYANCHOR_GOOGLE_CLIENT_SECRET",
    "redirect_uri": "DAYANCHOR_GOOGLE_REDIRECT_URI",
    "cookie_secret": "DAYANCHOR_AUTH_COOKIE_SECRET",
}


def write_auth_secrets(path, environ):
    auth = {key: environ.get(variable, "") for key, variable in AUTH_VARIABLES.items()}
    auth["server_metadata_url"] = GOOGLE_METADATA_URL
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write("[auth]\n")
        for key, value in auth.items():
            file.write(f"{key} = {json.dumps(value, ensure_ascii=True)}\n")


def streamlit_command(path, port, environ):
    config.get_config_options()
    existing_paths = config.get_option("secrets.files")
    if environ.get("STREAMLIT_SECRETS_FILES"):
        existing_paths = environ["STREAMLIT_SECRETS_FILES"].split()
    command = [
        "streamlit", "run", "streamlit_app.py",
        "--server.port", port, "--server.address", "0.0.0.0",
    ]
    for existing_path in existing_paths:
        command.extend(("--secrets.files", existing_path))
    command.extend(("--secrets.files", path))
    return command


if __name__ == "__main__":
    write_auth_secrets(sys.argv[1], os.environ)
    os.execvp("streamlit", streamlit_command(sys.argv[1], sys.argv[2], os.environ))
