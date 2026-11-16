"""Which fields a person can see, and the shared category list."""

import streamlit as st

from inchars.accounts import has
from inchars.config import SEE_ALL
from inchars.db import df


def visible_sites():
    """CEO sees every field. Everybody else only the fields given to them. On the new pages the Field drop-down at the
    top narrows this to one field (ceo_scope is set by main)."""
    if has(*SEE_ALL):
        sites = df("SELECT * FROM sites ORDER BY name")
    else:
        sites = df("""SELECT s.* FROM sites s JOIN user_sites u ON u.site_id=s.id WHERE u.user_id=? ORDER BY s.name""",
                   (st.session_state.uid,))
    chosen = st.session_state.get("ceo_scope")
    if chosen and chosen != "All fields":
        sites = sites[sites.name == chosen].reset_index(drop=True)
    return sites


def categories_df():
    return df("SELECT id, name FROM categories ORDER BY name")


def no_field_message():
    if has(*SEE_ALL):
        st.info("There is no field yet. Create the first one in Fields & categories.")
    else:
        st.info("No field is linked to your account yet. Please ask the CEO to give you access to a field.")
