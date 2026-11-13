import ast
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import psycopg
import pytest

import alert_scheduler
import data_access
import staging_database


PASSWORD = "synthetic-password-never-display"
URL = f"postgresql://stage_user:{PASSWORD}@stage-db.internal:5432/stage_db?sslmode=require"
ENV = {
    "DAYANCHOR_DEPLOYMENT_ENV": "staging",
    "DATABASE_URL": URL,
    "DAYANCHOR_STAGING_DB_HOST": "stage-db.internal",
    "DAYANCHOR_STAGING_DB_PORT": "5432",
    "DAYANCHOR_STAGING_DB_NAME": "stage_db",
    "DAYANCHOR_STAGING_DB_USER": "stage_user",
}


def test_repository_has_no_direct_psycopg_connection_calls():
    root = Path(__file__).resolve().parents[1]
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "psycopg":
                assert all(alias.name not in ("connect", "Connection", "AsyncConnection") for alias in node.names), path
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert not (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "psycopg"
                    and node.func.attr == "connect"
                ), path


def app_functions():
    path = Path(__file__).resolve().parents[1] / "streamlit_app.py"
    tree = ast.parse(path.read_text())
    names = {
        "normalize_database_url", "ensure_sslmode", "database_url_candidates",
        "get_connection", "initialize_database",
    }
    definitions = [
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    assert {node.name for node in definitions} == names
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    namespace = {
        "os": staging_database.os, "psycopg": psycopg, "staging_database": staging_database,
        "parse_qsl": parse_qsl, "urlencode": urlencode, "urlsplit": urlsplit, "urlunsplit": urlunsplit,
        "DB_URL": None, "DB_ERROR": None, "DB_CANDIDATE_SOURCE": None,
        "ai_envelope": SimpleNamespace(AI_EVENTS_SCHEMA_SQL=()),
    }
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


@pytest.fixture
def staging_env(monkeypatch):
    for key in list(staging_database.os.environ):
        if key.startswith("PG") or key in ("DATABASE_URL", *staging_database.ALIASES):
            monkeypatch.delenv(key, raising=False)
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)


def test_valid_staging_url_and_keyword_dsn_reach_connection_boundary():
    connector = Mock(return_value=object())
    staging_database.connect(URL, connector, ENV)
    params = psycopg.conninfo.conninfo_to_dict(connector.call_args.args[0])
    assert params["host"] == "stage-db.internal"
    assert params["sslmode"] == "require"
    dsn = f"host=stage-db.internal port=5432 dbname=stage_db user=stage_user password='{PASSWORD}' sslmode=require"
    staging_database.connect(dsn, connector, {**ENV, "DATABASE_URL": dsn})
    assert connector.call_count == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"DAYANCHOR_STAGING_DB_HOST": ""},
        {"DAYANCHOR_STAGING_DB_PORT": "not-a-port"},
        {"DAYANCHOR_STAGING_DB_PORT": "9" * 5000},
        {"DAYANCHOR_STAGING_DB_NAME": ""},
        {"DAYANCHOR_STAGING_DB_USER": None},
        {"DATABASE_URL": ""},
        {"DAYANCHOR_STAGING_DB_HOST": "production.example"},
        {"DAYANCHOR_STAGING_DB_PORT": "5433"},
        {"DAYANCHOR_STAGING_DB_NAME": "production_db"},
        {"DAYANCHOR_STAGING_DB_USER": "production_user"},
        {"DATABASE_PUBLIC_URL": URL},
        {"DATABASE_PRIVATE_URL": URL},
        {"POSTGRES_URL": URL},
        {"POSTGRESQL_URL": URL},
        {"PGHOST": "stage-db.internal"},
        {"PGPASSWORD": "other"},
        {"PGSERVICE": "indirect"},
        {"PGSERVICEFILE": "/tmp/synthetic"},
        {"PGHOSTADDR": "127.0.0.1"},
    ],
)
def test_bad_configuration_never_reaches_connector_or_leaks_password(changes):
    env = {**ENV, **changes}
    connector = Mock()
    with pytest.raises(staging_database.StagingDatabaseError) as error:
        staging_database.connect(env.get("DATABASE_URL"), connector, env)
    connector.assert_not_called()
    assert PASSWORD not in str(error.value)
    assert "postgresql://" not in str(error.value)


@pytest.mark.parametrize(
    "candidate",
    [
        f"postgresql://stage_user:{PASSWORD}@production.example:5432/stage_db?sslmode=require",
        f"postgresql://stage_user:{PASSWORD}@stage-db.internal,other.internal:5432/stage_db?sslmode=require",
        f"postgresql://stage_user:{PASSWORD}@stage-db.internal:5432/stage_db?sslmode=require&hostaddr=127.0.0.1",
        f"postgresql://stage_user:{PASSWORD}@stage-db.internal:5432/stage_db?sslmode=require&service=indirect",
        f"postgresql://stage_user:{PASSWORD}@stage-db.internal:5432/stage_db?sslmode=require&host=other",
        f"postgresql://stage_user:{PASSWORD}@stage-db.internal:5432/stage_db?sslmode=require&sslmode=disable",
        f"host=stage-db.internal host=other port=5432 dbname=stage_db user=stage_user password={PASSWORD} sslmode=require",
        f"host=stage-db.internal port=5432 dbname=stage_db user=stage_user password={PASSWORD} service=indirect sslmode=require",
        f"host=stage-db.internal port=5432 dbname=stage_db user=stage_user password={PASSWORD} hostaddr=127.0.0.1 sslmode=require",
        f"host=/tmp port=5432 dbname=stage_db user=stage_user password={PASSWORD} sslmode=require",
        f"host=stage-db.internal port=5432 dbname=stage_db user=stage_user password={PASSWORD} options=-csearch_path=other sslmode=require",
        f"postgresql://stage_user:{PASSWORD}@stage-db.internal/stage_db?sslmode=require",
        "postgresql://[malformed",
        "host='unterminated",
    ],
)
def test_unsafe_or_malformed_conninfo_is_rejected(candidate):
    connector = Mock()
    with pytest.raises(staging_database.StagingDatabaseError) as error:
        staging_database.connect(candidate, connector, {**ENV, "DATABASE_URL": candidate})
    connector.assert_not_called()
    assert PASSWORD not in str(error.value)


def test_candidate_substitution_is_rejected():
    connector = Mock()
    with pytest.raises(staging_database.StagingDatabaseError):
        staging_database.connect(URL.replace("sslmode=require", "sslmode=verify-full"), connector, ENV)
    connector.assert_not_called()


def test_production_mode_delegates_without_validation_or_rewriting():
    connector = Mock(return_value="connection")
    for mode in ({}, {"DAYANCHOR_DEPLOYMENT_ENV": "production"}):
        assert staging_database.connect("unmodified-production-conninfo", connector, mode) == "connection"
        connector.assert_called_with("unmodified-production-conninfo")


@pytest.mark.parametrize("mode", ["", "stagng", "development", " staging", "staging ", "Production", " "])
def test_invalid_mode_fails_closed_at_all_connection_boundaries(mode, staging_env, monkeypatch):
    monkeypatch.setenv("DAYANCHOR_DEPLOYMENT_ENV", mode)
    connector = Mock()
    monkeypatch.setattr(psycopg, "connect", connector)
    app = app_functions()
    with pytest.raises(staging_database.StagingDatabaseError):
        app["initialize_database"]()
    app["DB_URL"] = URL
    with pytest.raises(staging_database.StagingDatabaseError):
        app["get_connection"]()
    with pytest.raises(staging_database.StagingDatabaseError):
        alert_scheduler.run_once()
    with pytest.raises(staging_database.StagingDatabaseError):
        alert_scheduler.run_once(connection=Mock())
    connector.assert_not_called()


@pytest.mark.parametrize("mode", ["disable", "allow", "prefer", "", "invalid"])
def test_insecure_or_missing_tls_modes_fail_before_connect(mode):
    candidate = URL.replace("sslmode=require", f"sslmode={mode}") if mode else URL.split("?")[0]
    connector = Mock()
    with pytest.raises(staging_database.StagingDatabaseError) as error:
        staging_database.connect(candidate, connector, {**ENV, "DATABASE_URL": candidate})
    connector.assert_not_called()
    assert PASSWORD not in str(error.value)


@pytest.mark.parametrize("mode", ["require", "verify-ca", "verify-full"])
def test_secure_tls_modes_pass_unchanged_with_gss_disabled(mode):
    candidate = URL.replace("sslmode=require", f"sslmode={mode}")
    connector = Mock()
    staging_database.connect(candidate, connector, {**ENV, "DATABASE_URL": candidate})
    params = psycopg.conninfo.conninfo_to_dict(connector.call_args.args[0])
    assert params["sslmode"] == mode
    assert params["gssencmode"] == "disable"


@pytest.mark.parametrize("option", ["sslrootcert=/tmp/ca", "sslcert=/tmp/cert", "gssencmode=prefer"])
def test_unsupported_tls_overrides_are_rejected(option):
    candidate = URL + "&" + option
    connector = Mock()
    with pytest.raises(staging_database.StagingDatabaseError):
        staging_database.connect(candidate, connector, {**ENV, "DATABASE_URL": candidate})
    connector.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [
        {"DAYANCHOR_DEPLOYMENT_ENV": None},
        {"DAYANCHOR_DEPLOYMENT_ENV": "production"},
        {"DAYANCHOR_DEPLOYMENT_ENV": "stagng"},
        {"DATABASE_URL": ""},
        {"DAYANCHOR_STAGING_DB_NAME": "wrong"},
    ],
)
def test_preflight_fails_closed_without_a_connection(changes, monkeypatch):
    connector = Mock()
    monkeypatch.setattr(psycopg, "connect", connector)
    with pytest.raises(staging_database.StagingDatabaseError) as error:
        staging_database.preflight({**ENV, **changes})
    assert PASSWORD not in str(error.value)
    connector.assert_not_called()


def test_preflight_accepts_valid_configuration_without_a_connection(monkeypatch):
    connector = Mock()
    monkeypatch.setattr(psycopg, "connect", connector)
    staging_database.preflight(ENV)
    connector.assert_not_called()


@pytest.mark.parametrize("valid", [False, True])
def test_preflight_command_exits_safely_without_app_start(valid, staging_env, tmp_path):
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(path for path in sys.path if path and os.path.isabs(path))
    if not valid:
        env["DAYANCHOR_STAGING_DB_NAME"] = "wrong"
    result = subprocess.run(
        [sys.executable, "-m", "staging_database"],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == (0 if valid else 1)
    assert PASSWORD not in result.stdout + result.stderr


@pytest.mark.parametrize("success", [False, True])
def test_staging_launcher_runs_preflight_before_fake_application(tmp_path, success):
    fake_python = tmp_path / "python"
    fake_sh = tmp_path / "sh"
    log = tmp_path / "calls"
    fake_python.write_text(
        '#!/bin/sh\nprintf "preflight:%s %s\\n" "$1" "$2" >> "$TEST_LOG"\n'
        'if [ "$TEST_PREFLIGHT" = ok ]; then exit 0; else exit 1; fi\n'
    )
    fake_sh.write_text('#!/bin/sh\nprintf "app:%s\\n" "$1" >> "$TEST_LOG"\n')
    fake_python.chmod(0o755)
    fake_sh.chmod(0o755)
    env = os.environ.copy()
    env.update({
        "PATH": f"{tmp_path}:{env['PATH']}", "TEST_LOG": str(log),
        "TEST_PREFLIGHT": "ok" if success else "fail",
    })
    result = subprocess.run(
        ["/bin/sh", str(Path(__file__).resolve().parents[1] / "start-staging.sh")],
        env=env, capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == (0 if success else 1)
    assert log.read_text().splitlines() == (
        ["preflight:-m staging_database", "app:./start.sh"] if success
        else ["preflight:-m staging_database"]
    )


def test_connection_error_does_not_disclose_conninfo():
    connector = Mock(side_effect=psycopg.OperationalError(f"failure {URL}"))
    with pytest.raises(staging_database.StagingDatabaseError) as error:
        staging_database.connect(URL, connector, ENV)
    assert PASSWORD not in str(error.value)
    assert error.value.__cause__ is None


def test_app_initialization_and_normal_reads_validate_before_connect(staging_env, monkeypatch):
    app = app_functions()
    connector = Mock()
    monkeypatch.setattr(psycopg, "connect", connector)
    monkeypatch.setenv("DAYANCHOR_STAGING_DB_HOST", "production.example")
    with pytest.raises(staging_database.StagingDatabaseError):
        app["initialize_database"]()
    app["DB_URL"] = URL
    with pytest.raises(staging_database.StagingDatabaseError):
        app["get_connection"]()
    connector.assert_not_called()


def test_app_does_not_try_another_candidate_after_connection_failure(staging_env, monkeypatch):
    app = app_functions()
    connector = Mock(side_effect=psycopg.OperationalError("synthetic failure"))
    monkeypatch.setattr(psycopg, "connect", connector)
    with pytest.raises(staging_database.StagingDatabaseError):
        app["initialize_database"]()
    assert connector.call_count == 1


def test_app_initialization_and_reads_use_guarded_connection(staging_env, monkeypatch):
    app = app_functions()
    connector = Mock(return_value=MagicMock())
    monkeypatch.setattr(psycopg, "connect", connector)
    app["initialize_database"]()
    assert app["DB_CANDIDATE_SOURCE"] == "DATABASE_URL"
    app["get_connection"]()
    assert connector.call_count == 2
    for call in connector.call_args_list:
        assert psycopg.conninfo.conninfo_to_dict(call.args[0])["host"] == ENV["DAYANCHOR_STAGING_DB_HOST"]


def test_app_initialization_sanitizes_database_context_errors(staging_env, monkeypatch):
    app = app_functions()
    connection = MagicMock()
    connection.__enter__.side_effect = psycopg.OperationalError(f"failure {URL}")
    connector = Mock(return_value=connection)
    monkeypatch.setattr(psycopg, "connect", connector)
    with pytest.raises(staging_database.StagingDatabaseError) as error:
        app["initialize_database"]()
    assert connector.call_count == 1
    assert PASSWORD not in str(error.value)


def test_ai_event_persistence_cannot_bypass_app_guard(staging_env, monkeypatch):
    app = app_functions()
    app["DB_URL"] = URL
    connector = Mock()
    monkeypatch.setattr(psycopg, "connect", connector)
    monkeypatch.setenv("DAYANCHOR_STAGING_DB_NAME", "production_db")
    envelope = SimpleNamespace(task_ids=lambda: [])
    with pytest.raises(staging_database.StagingDatabaseError):
        data_access.log_ai_event(
            envelope, None, db_enabled_fn=lambda: True, get_connection_fn=app["get_connection"]
        )
    connector.assert_not_called()


def test_scheduler_guard_and_injected_connections_cannot_bypass_validation(staging_env, monkeypatch):
    connector = Mock()
    monkeypatch.setattr(psycopg, "connect", connector)
    monkeypatch.setenv("DAYANCHOR_STAGING_DB_HOST", "production.example")
    with pytest.raises(staging_database.StagingDatabaseError):
        alert_scheduler.run_once()
    with pytest.raises(staging_database.StagingDatabaseError):
        alert_scheduler.run_once(connection=Mock())
    connector.assert_not_called()


def test_scheduler_valid_target_reaches_guarded_connector(staging_env, monkeypatch):
    connection = Mock()
    connector = Mock(return_value=connection)
    monkeypatch.setattr(psycopg, "connect", connector)
    monkeypatch.setattr(alert_scheduler, "load_state", Mock(return_value=([], {})))
    monkeypatch.setattr(alert_scheduler, "run_autopilot_rollover", Mock(return_value=0))
    result = alert_scheduler.run_once()
    assert result["skipped"] is True
    assert connector.call_count == 1
    connection.close.assert_called_once()


def test_production_candidate_fallback_remains_unchanged(monkeypatch):
    for key in list(staging_database.os.environ):
        if key.startswith("PG") or key in ("DATABASE_URL", *staging_database.ALIASES):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("DAYANCHOR_DEPLOYMENT_ENV", raising=False)
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("DATABASE_PUBLIC_URL", "postgresql://u:p@other.internal:5432/other")
    app = app_functions()
    assert [name for name, _ in app["database_url_candidates"]()] == [
        "DATABASE_URL", "DATABASE_PUBLIC_URL"
    ]
    assert [name for name, _ in alert_scheduler.database_url_candidates()] == [
        "DATABASE_URL", "DATABASE_PUBLIC_URL"
    ]
    connector = Mock(side_effect=[psycopg.OperationalError("first failed"), MagicMock()])
    monkeypatch.setattr(psycopg, "connect", connector)
    app["initialize_database"]()
    assert connector.call_count == 2
    assert app["DB_CANDIDATE_SOURCE"] == "DATABASE_PUBLIC_URL"
