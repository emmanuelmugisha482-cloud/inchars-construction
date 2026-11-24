"""Inchars PD Group - Construction Management. Start here: streamlit run app.py

The code lives in the inchars/ folder. See README.md for where to find what."""

import traceback

import streamlit as st

try:
    st.set_page_config(page_title="Inchars PD Group - Construction Management", page_icon=":material/construction:", layout="wide")
except Exception:
    st.set_page_config(page_title="Inchars PD Group - Construction Management", layout="wide")

try:
    from inchars.router import main
except Exception:  # noqa: BLE001  a core file is missing or out of date
    st.error("The app could not start because a core file is missing or out of date.")
    st.write("Upload the whole `inchars` folder from the latest update to GitHub (replacing the old files), then reboot the app.")
    st.code(traceback.format_exc())
    st.stop()

if __name__ == "__main__":
    main()
