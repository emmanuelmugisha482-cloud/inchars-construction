"""Side menu, page header, user box, and the Field drop-down shown at the top of the data pages."""

import streamlit as st

from inchars.accounts import has
from inchars.config import CEO
from inchars.data.scope import visible_sites


def side_menu(items, counts=None):
    """Sidebar menu. items = [(key, label, material_icon)]. Remembers the choice. Returns (key, label)."""
    skey = f"menu_{st.session_state.role}"
    if st.session_state.get(skey) not in [k for k, _, _ in items]:
        st.session_state[skey] = items[0][0]
    with st.sidebar:
        for k, label, icon in items:
            n = (counts or {}).get(k)
            text = f":material/{icon}: {label}" + (f" ({n})" if n else "")
            if st.button(text, key=f"nav_{k}", type="primary" if st.session_state[skey] == k else "secondary"):
                st.session_state[skey] = k
                st.rerun()
    return st.session_state[skey], next(label for k, label, _ in items if k == st.session_state[skey])


def header(title):
    st.title(title)


def user_box():
    """Small user name at the very bottom of the sidebar. Click it to see the role and the Log out button."""
    with st.container(key="userbox"):
        with st.popover(f":material/account_circle: {st.session_state.user}"):
            st.caption(st.session_state.role)
            if st.button(":material/logout: Log out", type="primary", key="logout"):
                st.session_state.clear()
                st.rerun()


def field_bar():
    """The Field drop-down at the top of every data page (All fields, then each field). A person with one field sees its name."""
    st.session_state.ceo_scope = None
    names = visible_sites().name.tolist()
    if not names:
        return "All fields"
    if len(names) == 1 and not has(CEO):
        st.markdown(f"**Field:** {names[0]}")
        st.session_state.ceo_field = names[0]
        return names[0]
    options = ["All fields"] + names
    saved = st.session_state.get("ceo_field", "All fields")
    if saved not in options:
        saved = "All fields"
    a, _ = st.columns([2, 3])
    choice = a.selectbox("Field", options, index=options.index(saved), key="ceo_field_pick")
    st.session_state.ceo_field = choice
    return choice
