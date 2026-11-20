"""System check (CEO): shows which parts of the app can start, so a broken part is found in one minute."""

import importlib
import pkgutil

import pandas as pd
import streamlit as st

import inchars
from inchars.accounts import smtp_conf
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
    st.subheader("Database and email")
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
    st.markdown("Email: " + ("**set up** (notifications are also sent by email)" if smtp_conf() else "**not set up** (notifications appear in the app only)"))
