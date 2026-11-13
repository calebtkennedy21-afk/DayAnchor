import ast
import json
import os
import stat
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from streamlit.runtime.secrets import Secrets
from streamlit.web import cli

import auth_boundary
import auth_config


OWNER = "synthetic-owner-subject"
CLIENT_ID = "synthetic-client"
AUTH = {
    "client_id": CLIENT_ID,
    "client_secret": "synthetic-secret",
    "cookie_secret": "x" * 40,
    "redirect_uri": "https://staging.example.test/oauth2callback",
    "server_metadata_url": auth_boundary.GOOGLE_METADATA_URL,
}
ENV = {"DAYANCHOR_OWNER_GOOGLE_SUB": OWNER}


def identity(**overrides):
    claims = {
        "is_logged_in": True,
        "sub": OWNER,
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "exp": int(time.time()) + 3600,
    }
    claims.update(overrides)
    return claims


def fake_streamlit(claims=None, auth=AUTH, clicked=None):
    st = SimpleNamespace(
        secrets={"auth": auth},
        user=claims if claims is not None else {"is_logged_in": False},
        session_state={"tasks": ["synthetic-sensitive-data"], "current_page": "Settings"},
        info=Mock(),
        error=Mock(),
        login=Mock(),
        logout=Mock(),
        get_option=Mock(return_value={}),
        button=Mock(side_effect=lambda label, key: key == clicked),
    )
    st.sidebar = SimpleNamespace(button=st.button)
    return st


def test_anonymous_is_denied_and_can_request_google_login():
    st = fake_streamlit(clicked="dayanchor_auth_login")
    assert not auth_boundary.enforce_owner_access(st, ENV)
    st.login.assert_called_once_with()
    assert not st.session_state


def test_approved_owner_is_allowed():
    st = fake_streamlit(identity())
    assert auth_boundary.enforce_owner_access(st, ENV)
    assert st.session_state["current_page"] == "Settings"
    st.login.assert_not_called()


@pytest.mark.parametrize(
    "claims",
    [
        {"sub": "different-google-subject"},
        {"sub": None},
        {"iss": None},
        {"iss": "https://untrusted.example"},
        {"aud": None},
        {"aud": "different-client"},
        {"exp": None},
        {"exp": 0},
        {"exp": True},
        {"is_logged_in": False},
    ],
)
def test_wrong_or_incomplete_identity_is_denied(claims):
    st = fake_streamlit(identity(**claims))
    assert not auth_boundary.enforce_owner_access(st, ENV)
    assert not st.session_state


@pytest.mark.parametrize("subject", [None, "", " ", "owner,other", " owner", ["owner"], 123])
def test_missing_or_malformed_owner_subject_denies(subject):
    st = fake_streamlit(identity())
    assert not auth_boundary.enforce_owner_access(st, {"DAYANCHOR_OWNER_GOOGLE_SUB": subject})
    st.error.assert_called_once()


@pytest.mark.parametrize(
    "changes",
    [
        {"client_id": ""},
        {"client_secret": ""},
        {"cookie_secret": ""},
        {"cookie_secret": "too-short"},
        {"redirect_uri": ""},
        {"redirect_uri": "http://staging.example.test/oauth2callback"},
        {"redirect_uri": "https://staging.example.test/other"},
        {"redirect_uri": "https://user@staging.example.test/oauth2callback"},
        {"server_metadata_url": "https://untrusted.example/metadata"},
    ],
)
def test_missing_or_invalid_oidc_configuration_denies(changes):
    st = fake_streamlit(identity(), {**AUTH, **changes})
    assert not auth_boundary.enforce_owner_access(st, ENV)
    st.error.assert_called_once()
    st.login.assert_not_called()


def test_missing_secrets_file_denies():
    class MissingSecrets:
        def get(self, key):
            raise auth_boundary.StreamlitSecretNotFoundError("missing")

    st = fake_streamlit(identity())
    st.secrets = MissingSecrets()
    assert not auth_boundary.enforce_owner_access(st, ENV)
    st.error.assert_called_once()


def test_trusted_headers_configuration_denies_even_an_approved_identity():
    st = fake_streamlit(identity())
    st.get_option.return_value = {"x-user": "sub"}
    assert not auth_boundary.enforce_owner_access(st, ENV)
    st.error.assert_called_once()


def test_logout_clears_application_state_before_streamlit_logout():
    st = fake_streamlit(identity(), clicked="dayanchor_auth_logout")

    def check_cleared():
        assert not st.session_state

    st.logout.side_effect = check_cleared
    assert not auth_boundary.enforce_owner_access(st, ENV)
    st.logout.assert_called_once_with()


def test_each_rerun_checks_identity_instead_of_trusting_session_state():
    st = fake_streamlit(identity())
    assert auth_boundary.enforce_owner_access(st, ENV)
    st.session_state["authorized"] = True
    st.user = identity(sub="another-subject")
    assert not auth_boundary.enforce_owner_access(st, ENV)
    assert "authorized" not in st.session_state
    assert "tasks" not in st.session_state


def test_runtime_secrets_are_private_and_retain_values(tmp_path, capsys):
    env = {variable: f"synthetic-{key}" for key, variable in auth_config.AUTH_VARIABLES.items()}
    env["DAYANCHOR_GOOGLE_CLIENT_SECRET"] = 'synthetic-"quoted"\\secret'
    path = tmp_path / "secrets.toml"
    auth_config.write_auth_secrets(path, env)
    with path.open("rb") as file:
        auth = tomllib.load(file)["auth"]
    assert auth["client_secret"] == env["DAYANCHOR_GOOGLE_CLIENT_SECRET"]
    assert auth["server_metadata_url"] == auth_boundary.GOOGLE_METADATA_URL
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not capsys.readouterr().out
    with pytest.raises(FileExistsError):
        auth_config.write_auth_secrets(path, env)


def test_runtime_file_is_readable_by_streamlit_secrets(tmp_path, monkeypatch):
    env = {
        "DAYANCHOR_GOOGLE_CLIENT_ID": AUTH["client_id"],
        "DAYANCHOR_GOOGLE_CLIENT_SECRET": AUTH["client_secret"],
        "DAYANCHOR_GOOGLE_REDIRECT_URI": AUTH["redirect_uri"],
        "DAYANCHOR_AUTH_COOKIE_SECRET": AUTH["cookie_secret"],
    }
    path = tmp_path / "secrets.toml"
    auth_config.write_auth_secrets(path, env)
    monkeypatch.setattr("streamlit.runtime.secrets.config.get_option", lambda key: [str(path)])
    secrets = Secrets()
    secrets._file_watchers_installed = True
    assert auth_boundary.valid_configuration(secrets.get("auth"), OWNER)


def test_streamlit_cli_passes_runtime_secrets_file_without_starting_app(monkeypatch, tmp_path):
    run = Mock()
    monkeypatch.setattr(cli, "_main_run", run)
    path = str(tmp_path / "secrets.toml")
    result = CliRunner().invoke(
        cli.main,
        ["run", "streamlit_app.py", "--secrets.files", path],
    )
    assert result.exit_code == 0, result.output
    assert run.call_args.kwargs["flag_options"]["secrets_files"] == (path,)


@pytest.mark.parametrize("env_paths", [None, "env-one.toml env-two.toml"])
def test_launcher_preserves_sources_and_auth_takes_precedence(tmp_path, monkeypatch, env_paths):
    existing = tmp_path / "existing.toml"
    existing.write_text(
        'non_auth = "preserved"\n[auth]\nclient_id = "untrusted-client"\n',
        encoding="utf-8",
    )
    generated = tmp_path / "generated.toml"
    env = {variable: AUTH[key] for key, variable in auth_config.AUTH_VARIABLES.items()}
    auth_config.write_auth_secrets(generated, env)
    monkeypatch.setattr(auth_config.config, "get_config_options", Mock())
    monkeypatch.setattr(auth_config.config, "get_option", lambda key: [str(existing)])
    if env_paths:
        env["STREAMLIT_SECRETS_FILES"] = f"{existing} {tmp_path / 'missing.toml'}"

    command = auth_config.streamlit_command(str(generated), "8080", env)
    paths = [command[i + 1] for i, part in enumerate(command) if part == "--secrets.files"]
    assert paths[0] == str(existing)
    assert paths[-1] == str(generated)
    assert len(paths) == (3 if env_paths else 2)

    run = Mock()
    monkeypatch.setattr(cli, "_main_run", run)
    result = CliRunner().invoke(cli.main, command[1:])
    assert result.exit_code == 0, result.output
    assert run.call_args.kwargs["flag_options"]["secrets_files"] == tuple(paths)

    monkeypatch.setattr("streamlit.runtime.secrets.config.get_option", lambda key: paths)
    secrets = Secrets()
    secrets._file_watchers_installed = True
    assert secrets["non_auth"] == "preserved"
    assert secrets["auth"]["client_id"] == AUTH["client_id"]
    assert auth_boundary.valid_configuration(secrets["auth"], OWNER)


def test_missing_generated_auth_cannot_fall_back_to_existing_auth(tmp_path, monkeypatch):
    existing = tmp_path / "existing.toml"
    existing.write_text(
        "[auth]\n" + "".join(f'{key} = "{value}"\n' for key, value in AUTH.items()),
        encoding="utf-8",
    )
    generated = tmp_path / "generated.toml"
    auth_config.write_auth_secrets(generated, {})
    monkeypatch.setattr(
        "streamlit.runtime.secrets.config.get_option", lambda key: [str(existing), str(generated)]
    )
    secrets = Secrets()
    secrets._file_watchers_installed = True
    assert not auth_boundary.valid_configuration(secrets["auth"], OWNER)


@pytest.mark.parametrize("env_source", [False, True])
def test_launcher_reads_configured_secrets_sources_without_starting_streamlit(tmp_path, env_source):
    project = tmp_path / "project"
    project.mkdir()
    streamlit_dir = project / ".streamlit"
    streamlit_dir.mkdir()
    (streamlit_dir / "config.toml").write_text(
        '[secrets]\nfiles = ["custom-secrets.toml"]\n', encoding="utf-8"
    )
    env = os.environ.copy()
    env.pop("STREAMLIT_SECRETS_FILES", None)
    if env_source:
        env["STREAMLIT_SECRETS_FILES"] = "env-one.toml env-two.toml"
    env["HOME"] = str(tmp_path)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(__file__).resolve().parents[1])]
        + [path for path in sys.path if path and os.path.isabs(path)]
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [
            sys.executable, "-c",
            "import json, os, socket\n"
            "def blocked(*args, **kwargs): raise RuntimeError('Network blocked')\n"
            "socket.socket.connect = blocked\n"
            "socket.socket.connect_ex = blocked\n"
            "socket.create_connection = blocked\n"
            "from auth_config import streamlit_command\n"
            "print(json.dumps(streamlit_command('generated.toml', '8080', os.environ)))",
        ],
        cwd=project, env=env, capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr
    command = json.loads(result.stdout)
    paths = [command[i + 1] for i, part in enumerate(command) if part == "--secrets.files"]
    expected = ["env-one.toml", "env-two.toml"] if env_source else ["custom-secrets.toml"]
    assert paths == [*expected, "generated.toml"]


@pytest.mark.parametrize(
    ("setting", "enabled"),
    [(None, True), ("true", True), ("1", True), ("YES", True), ("on", True),
     ("false", False), ("0", False), ("", True)],
)
def test_scheduler_environment_isolated_without_starting_scheduler(tmp_path, setting, enabled):
    fake_python = tmp_path / "python"
    fake_python.write_text(
        """#!/bin/sh
case "$1" in
  alert_scheduler.py) env > "$SCHEDULER_ENV";;
  auth_config.py)
    if [ "$EXPECT_SCHEDULER" = yes ]; then
      i=0
      while [ ! -f "$SCHEDULER_ENV" ]; do
        i=$((i + 1))
        [ "$i" -lt 200 ] || exit 1
        sleep 0.01
      done
    fi
    ;;
  *) exit 1;;
esac
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    scheduler_env = tmp_path / "scheduler-env"
    env = os.environ.copy()
    env.update({variable: "synthetic-only" for variable in auth_config.AUTH_VARIABLES.values()})
    env.update({
        "DAYANCHOR_OWNER_GOOGLE_SUB": OWNER,
        "SCHEDULER_TEST_SETTING": "preserved",
        "SCHEDULER_ENV": str(scheduler_env),
        "EXPECT_SCHEDULER": "yes" if enabled else "no",
        "PATH": f"{tmp_path}:{env['PATH']}",
        "TMPDIR": str(tmp_path),
    })
    if setting is None:
        env.pop("ALERT_SCHEDULER_ENABLED", None)
    else:
        env["ALERT_SCHEDULER_ENABLED"] = setting
    result = subprocess.run(
        ["sh", str(Path(__file__).resolve().parents[1] / "start.sh")],
        env=env, capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert scheduler_env.exists() == enabled
    if enabled:
        inherited = dict(line.split("=", 1) for line in scheduler_env.read_text().splitlines())
        assert inherited["SCHEDULER_TEST_SETTING"] == "preserved"
        for variable in (*auth_config.AUTH_VARIABLES.values(), "DAYANCHOR_OWNER_GOOGLE_SUB"):
            assert variable not in inherited


class Stopped(Exception):
    pass


@pytest.mark.parametrize("claims", [{"is_logged_in": False}, identity(sub="other")])
def test_real_entry_guard_stops_before_database_and_rendering(monkeypatch, claims):
    source = Path(__file__).resolve().parents[1] / "streamlit_app.py"
    tree = ast.parse(source.read_text())
    csv_import = next(
        index for index, node in enumerate(tree.body)
        if isinstance(node, ast.Import) and any(alias.name == "csv" for alias in node.names)
    )
    prefix = ast.Module(body=tree.body[:csv_import], type_ignores=[])
    assert any(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and isinstance(node.value.func.value, ast.Name)
        and node.value.func.value.id == "app_bootstrap"
        and node.value.func.attr == "run_app"
        for node in tree.body[csv_import:]
    )
    st = fake_streamlit(claims)
    st.set_page_config = Mock()
    st.stop = Mock(side_effect=Stopped)
    module = ModuleType("streamlit")
    module.__dict__.update(vars(st))
    monkeypatch.setitem(sys.modules, "streamlit", module)
    monkeypatch.setenv("DAYANCHOR_OWNER_GOOGLE_SUB", OWNER)
    initialize_database = Mock()
    render_protected_page = Mock()
    with pytest.raises(Stopped):
        exec(compile(prefix, str(source), "exec"), {})
        initialize_database()
        render_protected_page()
    initialize_database.assert_not_called()
    render_protected_page.assert_not_called()
    st.stop.assert_called_once()
