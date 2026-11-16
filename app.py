"""Inchars PD Group - Construction Management. Start here: streamlit run app.py

The code lives in the inchars/ folder. See README.md for where to find what."""
import streamlit as st

try:
    st.set_page_config(page_title="Inchars PD Group - Construction Management", page_icon=":material/construction:",
                       layout="wide")
except Exception:
    st.set_page_config(page_title="Inchars PD Group - Construction Management", layout="wide")

from inchars.router import main  # noqa: E402  (after set_page_config)

if __name__ == "__main__":
    main()
