import os
from pathlib import Path
import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor


_DOTENV_CACHE = None


def _clean_setting(value):
    if value is None:
        return ""
    text = str(value).strip().strip('"').strip("'").strip()
    return text


def _load_dotenv():
    global _DOTENV_CACHE
    if _DOTENV_CACHE is not None:
        return _DOTENV_CACHE

    values = {}
    dotenv_path = Path(".env")
    if dotenv_path.exists():
        for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            values[key.strip()] = _clean_setting(val)

    _DOTENV_CACHE = values
    return _DOTENV_CACHE


def _get_setting(name):
    env_value = _clean_setting(os.getenv(name))
    if env_value:
        return env_value

    lower_env_value = _clean_setting(os.getenv(name.lower()))
    if lower_env_value:
        return lower_env_value

    try:
        secret_value = _clean_setting(st.secrets.get(name))
        if secret_value:
            return secret_value
    except Exception:
        pass

    dotenv_values = _load_dotenv()
    dotenv_value = _clean_setting(dotenv_values.get(name) or dotenv_values.get(name.lower()))
    if dotenv_value:
        return dotenv_value

    return ""


def _setting_source(name):
    env_value = _clean_setting(os.getenv(name))
    if env_value:
        return "env"

    lower_env_value = _clean_setting(os.getenv(name.lower()))
    if lower_env_value:
        return "env"

    try:
        secret_value = _clean_setting(st.secrets.get(name))
        if secret_value:
            return "secrets"
    except Exception:
        pass

    dotenv_values = _load_dotenv()
    dotenv_value = _clean_setting(dotenv_values.get(name) or dotenv_values.get(name.lower()))
    if dotenv_value:
        return ".env"

    return "missing"


def get_db_key_diagnostics():
    return {
        "DATABASE_URL": _setting_source("DATABASE_URL"),
        "DATABASE_PUBLIC_URL": _setting_source("DATABASE_PUBLIC_URL"),
        "DATABASE_PRIVATE_URL": _setting_source("DATABASE_PRIVATE_URL"),
        "POSTGRES_URL": _setting_source("POSTGRES_URL"),
        "POSTGRESQL_URL": _setting_source("POSTGRESQL_URL"),
        "DB_URL": _setting_source("DB_URL"),
    }


def _get_database_url():
    return (
        _get_setting("DATABASE_URL")
        or _get_setting("DATABASE_PUBLIC_URL")
        or _get_setting("DATABASE_PRIVATE_URL")
        or _get_setting("POSTGRES_URL")
        or _get_setting("POSTGRESQL_URL")
        or _get_setting("DB_URL")
        or ""
    )


def get_connection():
    database_url = _get_database_url()

    if database_url:
        try:
            return psycopg2.connect(
                database_url,
                cursor_factory=RealDictCursor,
            )
        except Exception as e:
            st.error(f"Database connection failed: {e}")
            return None

    # Backward-compatible fallback for split credentials.
    db_host = _get_setting("DB_HOST") or _get_setting("PGHOST")
    db_port = _get_setting("DB_PORT") or _get_setting("PGPORT") or "5432"
    db_name = _get_setting("DB_NAME") or _get_setting("PGDATABASE")
    db_user = _get_setting("DB_USER") or _get_setting("PGUSER")
    db_password = _get_setting("DB_PASSWORD") or _get_setting("PGPASSWORD")

    if not all([db_host, db_name, db_user, db_password]):
        key_sources = get_db_key_diagnostics()
        st.error(
            "Database is not configured. Set DATABASE_URL or DATABASE_PUBLIC_URL "
            "(or DB_HOST/DB_NAME/DB_USER/DB_PASSWORD, or PGHOST/PGDATABASE/PGUSER/PGPASSWORD)."
        )
        st.caption(f"DB key detection (safe): {key_sources}")
        st.caption(
            "Runtime note: these vars must be set in the same environment where Streamlit is running."
        )
        return None

    try:
        return psycopg2.connect(
            host=db_host,
            port=db_port,
            dbname=db_name,
            user=db_user,
            password=db_password,
            cursor_factory=RealDictCursor,
        )
    except Exception as e:
        st.error(f"Database connection failed: {e}")
        return None

def init_db():
    conn = get_connection()
    if conn is None:
        return
    with conn.cursor() as cur:
        cur.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                category TEXT NOT NULL,
                priority TEXT DEFAULT 'medium',
                status TEXT DEFAULT 'todo',
                created_date DATE NOT NULL,
                due_date DATE,
                completed_date DATE,
                ai_suggested BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        conn.commit()
    conn.close()
