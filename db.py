# Database connection and schema setup for DayAnchor
import os
import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor

# Support Railway URL-style variables first, then PG* and DB* variable styles.
DATABASE_URL = (
    os.getenv("DATABASE_URL")
    or os.getenv("DATABASE_PUBLIC_URL")
    or os.getenv("POSTGRES_URL")
    or os.getenv("POSTGRESQL_URL")
    or ""
)

DB_HOST = os.getenv("DB_HOST") or os.getenv("PGHOST", "")
DB_PORT = os.getenv("DB_PORT") or os.getenv("PGPORT", "5432")
DB_NAME = os.getenv("DB_NAME") or os.getenv("PGDATABASE", "")
DB_USER = os.getenv("DB_USER") or os.getenv("PGUSER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD") or os.getenv("PGPASSWORD", "")

@st.cache_resource
def get_connection():
    try:
        if DATABASE_URL:
            conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
            return conn

        if not all([DB_HOST, DB_NAME, DB_USER, DB_PASSWORD]):
            st.error(
                "Database credentials are not set. Use DATABASE_URL/DATABASE_PUBLIC_URL "
                "or PGHOST/PGDATABASE/PGUSER/PGPASSWORD."
            )
            return None

        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            cursor_factory=RealDictCursor,
        )
        return conn
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

# Call this at app startup
init_db()

