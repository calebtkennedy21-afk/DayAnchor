# Database connection and schema setup for DayAnchor
import os
import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import date

# Get DB connection info from environment variables
DB_HOST = os.getenv("DB_HOST", "")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "")
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

@st.cache_resource
def get_connection():
    if not all([DB_HOST, DB_NAME, DB_USER, DB_PASSWORD]):
        st.error("Database credentials are not set. Please set them as environment variables.")
        return None
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            cursor_factory=RealDictCursor
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
            CREATE TABLE IF NOT EXISTS productivity (
                id SERIAL PRIMARY KEY,
                entry_date DATE NOT NULL,
                metric TEXT NOT NULL,
                value NUMERIC NOT NULL
            );
        ''')
        conn.commit()
    conn.close()

# Call this at app startup
init_db()
