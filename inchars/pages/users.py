"""Users page (CEO)."""

import pandas as pd
import streamlit as st

from inchars.accounts import (
    add_user_roles,
    delete_user_full,
    has,
    set_user_password,
    set_user_roles,
    set_user_sites,
)
from inchars.components.common import K, done, match, search_box, wide
from inchars.config import CEO, FIELD, FIELD_ROLES, ROLES
from inchars.db import df


def page_users():
    if not has(CEO):
        return st.error("Only the CEO can manage users.")
    me = st.session_state.uid
    sites = df("SELECT id, name FROM sites ORDER BY name")
    site_ids = dict(zip(sites.name, sites.id.astype(int)))
    users = df("SELECT id, username, role, email FROM users ORDER BY username")
    links, rl = df("SELECT u.user_id, s.name FROM user_sites u JOIN sites s ON s.id=u.site_id"), df("SELECT user_id, role FROM user_roles")
    fields_of = {int(k): sorted(v.tolist()) for k, v in links.groupby("user_id")["name"]} if len(links) else {}
    roles_of = {int(k): sorted(v.tolist()) for k, v in rl.groupby("user_id")["role"]} if len(rl) else {}
    show = pd.DataFrame({
        "User name": users.username.values,
        "Roles": [", ".join(roles_of.get(int(i), [r])) for i, r in zip(users.id, users.role)],
        "Email": users.email.fillna("").values,
        "Fields": ["All fields" if CEO in roles_of.get(int(i), [r]) else ", ".join(fields_of.get(int(i), []))
                   for i, r in zip(users.id, users.role)]})
    q = search_box("q_users", "Search user", "Type part of a name…")
    view = match(show, ["User name", "Roles", "Email", "Fields"], q).reset_index(drop=True)
    st.caption("Click a row to edit that user, or choose the user from the list below the table.")
    try:
        ev = wide(st.dataframe, view, hide_index=True, on_select="rerun", selection_mode="single-row", key="users_tbl")
        rows = [r for r in (ev.selection.rows if ev is not None else []) if r < len(view)]  # a row that is no longer in the list is ignored
    except TypeError:  # an older Streamlit without row selection: the list below is used
        wide(st.dataframe, view, hide_index=True)
        rows = []
    with st.expander("Add a user"):
        st.caption("Choose one or more roles. The CEO sees all fields. Accountant, Store manager and Field manager need at least one field. "
                   "Receptionist and Engineer work on services (jobs for customers) and need no field.")
        a1, a2 = st.columns(2)
        nu = a1.text_input("User name", key=K("nu"))
        nroles = a2.multiselect("Roles", ROLES, default=[FIELD], key=K("nr"))
        ne = a1.text_input("Email (optional, used to reset a forgotten password)", key=K("ne"))
        npw = a2.text_input("Temporary password (at least 8 characters)", type="password", key=K("np"))
        ns = st.multiselect("Fields this user works on", list(site_ids), key=K("ns")) if nroles and CEO not in nroles and any(r in FIELD_ROLES for r in nroles) else []
        if st.button("Add user"):
            err = None
            if not nroles:
                err = "Choose at least one role."
            elif CEO not in nroles and any(r in FIELD_ROLES for r in nroles) and not ns:
                err = "Choose at least one field for this user." if site_ids else "Create a field first (Fields & categories)."
            err = err or add_user_roles(nu, nroles, ne, npw)
            if err:
                st.error(err)
            else:
                set_user_sites(int(df("SELECT id FROM users WHERE lower(username)=lower(?)", (nu.strip(),))["id"][0]),
                               [site_ids[n] for n in ns])
                done(f"User {nu.strip()} added.")
    labels = {f"{u} ({show.Roles[i]})": int(users.id[i]) for i, u in enumerate(users.username)}
    who = st.selectbox("Or choose a user from the list", list(labels), index=None, placeholder="Search and choose a user", key=K("wu"))
    uid = labels[who] if who else (int(users[users.username == view["User name"].iloc[rows[0]]].id.iloc[0]) if rows else None)
    if uid is None:
        return
    uname = users[users.id == uid].username.iloc[0]
    st.subheader(f"Edit {uname}")
    st.caption("The user gets the pages of every role you tick the next time they open a page.")
    cur_roles, cur_fields = roles_of.get(uid, []), fields_of.get(uid, [])
    nr = st.multiselect("Roles", ROLES, default=[r for r in cur_roles if r in ROLES], key=K(f"er{uid}"))
    nf = st.multiselect("Fields this user works on", list(site_ids), default=[f for f in cur_fields if f in site_ids],
                        key=K(f"ef{uid}")) if CEO not in nr and any(r in FIELD_ROLES for r in nr) else cur_fields
    if st.button("Save roles and fields"):
        ceos = [i for i, r in roles_of.items() if CEO in r]
        if not nr:
            st.error("Choose at least one role.")
        elif CEO not in nr and any(r in FIELD_ROLES for r in nr) and not nf:
            st.error("An Accountant, Store manager or Field manager needs at least one field.")
        elif uid in ceos and CEO not in nr and len(ceos) <= 1:
            st.error("There must be at least one CEO.")
        else:
            set_user_roles(uid, nr)
            if CEO not in nr:
                set_user_sites(uid, [site_ids[n] for n in nf] if any(r in FIELD_ROLES for r in nr) else [])
            done(f"Roles and fields saved for {uname}.")
    p1, p2 = st.columns(2)
    pw = p1.text_input("New password (at least 8 characters)", type="password", key=K("wp"))
    if p1.button("Set new password"):
        err = set_user_password(uid, pw)
        if err:
            st.error(err)
        else:
            done(f"Password changed for {uname}.")
    sure = p2.checkbox("I am sure I want to delete this user", key=K("ws"))
    if p2.button("Delete this user"):
        err = delete_user_full(uid, me) if sure else "Tick the box to confirm."
        if err:
            st.error(err)
        else:
            done(f"{uname} deleted.")
