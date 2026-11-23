"""System check (CEO): shows which parts of the app can start, so a broken part is found in one minute."""

import importlib
import pkgutil

import pandas as pd
import streamlit as st

import inchars
from inchars.components.common import wide
from inchars.db import df


def check_parts():
    """Try to load every file of the app. Returns a table: File, Status, Detail."""
    rows = []
    for m in pkgutil.walk_packages(inchars.__path__, "inchars."):
        try:
            importlib.import_module(m.name)
            rows.append({"File": m.name.replace("inchars.", "").replace(".", "/") + ".py", "Status": "OK", "Detail": ""})
        except Exception as e:  # noqa: BLE001
            rows.append({"File": m.name.replace("inchars.", "").replace(".", "/") + ".py", "Status": "FAILED", "Detail": f"{type(e).__name__}: {e}"})
    out = pd.DataFrame(rows, columns=["File", "Status", "Detail"])
    return out.sort_values(["Status", "File"], ascending=[True, True]).reset_index(drop=True)  # FAILED first


def system_check_page():
    st.caption("Checks that every part of the app can start. After an update, open this page first.")
    parts = check_parts()
    bad = int((parts.Status == "FAILED").sum())
    if bad:
        st.error(f"{bad} part(s) cannot start. The other parts still work. The most common cause after an update is a file that was not "
                 "uploaded or not replaced: upload the whole `inchars` folder again, then reboot the app.")
    else:
        st.success(f"All {len(parts)} parts of the app can start.")
    wide(st.dataframe, parts, hide_index=True)
    st.subheader("Database")
    try:
        df("SELECT 1 AS ok")
        st.markdown("Database: **connected**")
    except Exception as e:  # noqa: BLE001
        st.error(f"Database: not reachable ({type(e).__name__}).")
    for table, label in (("jobs", "Jobs (services)"), ("expenses", "Expenses"), ("material_requests", "Material requests")):
        try:
            n = int(df(f"SELECT COUNT(*) AS n FROM {table}").n.iloc[0])
            st.markdown(f"{label}: table is there, {n:,} record(s)")
        except Exception:  # noqa: BLE001
            st.warning(f"{label}: the table is missing or cannot be read.")
    st.subheader("Email")
    try:
        mail_part()
    except Exception as e:  # noqa: BLE001
        st.error(f"The email check could not run ({type(e).__name__}: {e}). The rest of the app works.")


def mail_part():
    from inchars.accounts import send_email, smtp_conf
    cfg = smtp_conf()
    if not cfg:
        return st.warning("Email is **not set up**: customers and staff are not emailed. Add an [smtp] section in Streamlit Secrets (see README, 'Email'), then reboot the app.")
    st.markdown(f"Email is **set up**: {cfg['host']}, port {cfg['port']}, sending as {cfg['sender'] or cfg['user']}. Send yourself a test to be sure it works:")
    to = st.text_input("Your email address", placeholder="you@example.com", key="mail_test_to")
    if st.button("Send a test email", key="mail_test_go"):
        if "@" not in to:
            return st.error("Write a valid email address first.")
        try:
            send_email(to.strip(), "Test from Inchars PD Group", "Hello,\n\nThis is a test email from the Inchars app. It works.\n\nInchars PD Group", cfg)
            st.success(f"Sent to {to.strip()}. If it does not arrive in a minute, look in the spam folder.")
        except Exception as e:  # noqa: BLE001
            st.error(f"The email could not be sent: {type(e).__name__}: {e}")
            st.caption("Common causes: for Gmail use an App Password (not your normal password) with host smtp.gmail.com and port 465; a wrong port; a blocked account.")
