import streamlit as st
from datetime import date, datetime
from db import get_connection, init_db

st.set_page_config(page_title="DayAnchor", page_icon="⛵", layout="centered")

st.title("DayAnchor")
st.markdown("""
Welcome to **DayAnchor**! Track your productivity and see your progress over time.

---
""")

# --- Productivity Data Entry ---
st.header("Add Productivity Entry")
with st.form("prod_form"):
    entry_date = st.date_input("Date", value=date.today())
    metric = st.text_input("Metric (e.g., Tasks Completed)")
    value = st.number_input("Value", min_value=0.0, step=1.0)
    submitted = st.form_submit_button("Add Entry")

if submitted and metric and value:
    conn = get_connection()
    if conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO productivity (entry_date, metric, value) VALUES (%s, %s, %s)",
                (entry_date, metric, value)
            )
            conn.commit()
        st.success("Entry added!")
        conn.close()

# --- Productivity Reports ---
st.header("Productivity Reports")
conn = get_connection()
if conn:
    with conn.cursor() as cur:
        # Monthly summary
        cur.execute('''
            SELECT to_char(entry_date, 'YYYY-MM') as month, metric, SUM(value) as total
            FROM productivity
            WHERE entry_date >= date_trunc('year', CURRENT_DATE)
            GROUP BY month, metric
            ORDER BY month DESC, metric
        ''')
        monthly = cur.fetchall()
        st.subheader("Monthly Summary (YTD)")
        if monthly:
            st.dataframe(monthly)
        else:
            st.info("No data for this year yet.")

        # YTD summary
        cur.execute('''
            SELECT metric, SUM(value) as ytd_total
            FROM productivity
            WHERE entry_date >= date_trunc('year', CURRENT_DATE)
            GROUP BY metric
        ''')
        ytd = cur.fetchall()
        st.subheader("Year-to-Date (YTD) Totals")
        if ytd:
            st.dataframe(ytd)
        else:
            st.info("No YTD data yet.")

        # Yearly recap
        cur.execute('''
            SELECT to_char(entry_date, 'YYYY') as year, metric, SUM(value) as total
            FROM productivity
            GROUP BY year, metric
            ORDER BY year DESC, metric
        ''')
        yearly = cur.fetchall()
        st.subheader("Yearly Recap")
        if yearly:
            st.dataframe(yearly)
        else:
            st.info("No yearly data yet.")
    conn.close()
