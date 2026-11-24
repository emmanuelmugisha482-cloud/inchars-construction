"""System check: shows which file works and which does not, and tests the email. Kept on its own so it opens even when other files are broken."""

import importlib
import pandas as pd
import streamlit as st



MODULES = ["settings", "database", "accounts", "data", "widgets", "router", "construction.dashboard", "construction.materials", "construction.money",
           "construction.admin", "jobs.data", "jobs.page", "jobs.extras"]  # every file of the app except this one and app.py


def check_parts():
    """Try to load every file of the app. Returns a table: File, Status, Detail."""
    rows = []
    for name in MODULES:
        try:
            importlib.import_module("inchars." + name)
            rows.append({"File": "inchars/" + name.replace(".", "/") + ".py", "Status": "OK", "Detail": ""})
        except Exception as e:  # noqa: BLE001
            rows.append({"File": "inchars/" + name.replace(".", "/") + ".py", "Status": "FAILED", "Detail": f"{type(e).__name__}: {e}"})
    out = pd.DataFrame(rows, columns=["File", "Status", "Detail"])
    return out.sort_values(["Status", "File"], ascending=[True, True]).reset_index(drop=True)  # FAILED first


def _show(table):
    try:
        st.dataframe(table, hide_index=True, width="stretch")
    except TypeError:
        st.dataframe(table, hide_index=True, use_container_width=True)


def system_check_page():
    st.caption("Checks that every part of the app can start. After an update, open this page first.")
    parts = check_parts()
    bad = int((parts.Status == "FAILED").sum())
    if bad:
        st.error(f"{bad} part(s) cannot start. The other parts still work. The most common cause after an update is a file that was not "
                 "uploaded or not replaced: upload the whole `inchars` folder again, then reboot the app.")
    else:
        st.success(f"All {len(parts)} parts of the app can start.")
    _show(parts)
    st.subheader("Database")
    try:
        from inchars.database import df
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
