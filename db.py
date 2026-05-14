import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg2
import streamlit as st
from psycopg2.extras import RealDictCursor


TABLE_NAME = "tasks"


def _clean(value):
    if value is None:
        return ""
    return str(value).strip().strip('"').strip("'").strip()


def _get_database_url():
    return _clean(os.getenv("DATABASE_URL")) or _clean(os.getenv("DATABASE_PUBLIC_URL"))


def _ensure_sslmode_require(database_url):
    parts = urlsplit(database_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if "sslmode" not in query:
        query["sslmode"] = "require"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def get_connection():
    database_url = _get_database_url()
    if not database_url:
        st.error("Database is not configured. Set DATABASE_URL or DATABASE_PUBLIC_URL in Railway.")
        return None

    try:
        return psycopg2.connect(
            database_url,
            connect_timeout=8,
            cursor_factory=RealDictCursor,
        )
    except Exception as first_error:
        if "sslmode=" in database_url.lower():
            st.error(f"Database connection failed: {first_error}")
            return None

        try:
            return psycopg2.connect(
                _ensure_sslmode_require(database_url),
                connect_timeout=8,
                cursor_factory=RealDictCursor,
            )
        except Exception as second_error:
            st.error(f"Database connection failed: {second_error} (initial error: {first_error})")
            return None


def init_db():
    conn = get_connection()
    if conn is None:
        return

    try:
        with conn.cursor() as cur:
            cur.execute(
                f'''
                CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
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
                '''
            )
            conn.commit()
    finally:
        conn.close()
