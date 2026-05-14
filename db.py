import os
import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor


def _get_database_url():
    return (
        os.getenv("DATABASE_URL")
        or os.getenv("DATABASE_PUBLIC_URL")
        or os.getenv("POSTGRES_URL")
        or os.getenv("POSTGRESQL_URL")
        or os.getenv("DB_URL")
        or ""
    )


def get_connection():
    database_url = _get_database_url()
    sslmode = os.getenv("DATABASE_SSLMODE", os.getenv("DB_SSLMODE", "require"))

    if database_url:
        try:
            return psycopg2.connect(
                database_url,
                sslmode=sslmode,
                cursor_factory=RealDictCursor,
            )
        except Exception as e:
            st.error(f"Database connection failed: {e}")
            return None

    # Backward-compatible fallback for split credentials.
    db_host = os.getenv("DB_HOST") or os.getenv("PGHOST", "")
    db_port = os.getenv("DB_PORT") or os.getenv("PGPORT", "5432")
    db_name = os.getenv("DB_NAME") or os.getenv("PGDATABASE", "")
    db_user = os.getenv("DB_USER") or os.getenv("PGUSER", "")
    db_password = os.getenv("DB_PASSWORD") or os.getenv("PGPASSWORD", "")

    if not all([db_host, db_name, db_user, db_password]):
        st.error(
            "Database is not configured. Set DATABASE_URL and DATABASE_SSLMODE "
            "(or DB_HOST/DB_NAME/DB_USER/DB_PASSWORD, or PGHOST/PGDATABASE/PGUSER/PGPASSWORD)."
        )
        return None

    try:
        return psycopg2.connect(
            host=db_host,
            port=db_port,
            dbname=db_name,
            user=db_user,
            password=db_password,
            sslmode=sslmode,
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
