import streamlit as st

st.set_page_config(page_title="DayAnchor", page_icon="⛵", layout="centered")

st.title("DayAnchor")
st.markdown("""
Welcome to **DayAnchor**! This is your starting point for a Streamlit app.

- Edit this file (`streamlit_app.py`) to add your own features.
- Deploy on Streamlit Community Cloud for instant sharing.

---

### Example: Simple Echo Tool

Type something below and see it echoed back!
""")

user_input = st.text_input("Enter something:")
if user_input:
    st.success(f"You typed: {user_input}")

st.info("""
#### Next Steps
- Add your own widgets and logic
- Connect to data sources or APIs
- Customize the UI with Streamlit components
""")
