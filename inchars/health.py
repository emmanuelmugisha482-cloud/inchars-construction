"""Start fresh: after testing, one button deletes every record and all the history, except the users, so the real data can start clean.
Kept on its own file on purpose. Once it has been used, the page disappears from the menu (a note is kept in the table app_flags)."""

from datetime import datetime

import streamlit as st

from inchars.accounts import has
from inchars.database import IS_PG, cached, conn, df
from inchars.settings import CEO

# every table that holds records or history. NOT in the list (they stay): users, user_roles, app_flags.
WIPE = ["sites", "user_sites", "categories", "expenses", "funds", "reset_codes", "materials", "material_moves", "material_requests",
        "money_categories", "money_requests", "money_ledger", "taxes", "debts", "debt_payments",
        "customers", "jobs", "job_payments", "job_files", "job_file_data", "job_events", "job_notices"]


@cached(ttl=15)
def fresh_start_done():
    """True once the button has been used (then the page is no longer in the menu)."""
    try:
        return len(df("SELECT value FROM app_flags WHERE name='fresh_start'")) > 0
    except Exception:  # noqa: BLE001
        return False


def wipe_everything_but_users():
    """Delete all records and history except the users and their roles. Returns an error text, or None when done."""
    if not has(CEO):
        return "Only the CEO can do this."
    with conn() as c:
        if IS_PG:
            c.execute("TRUNCATE " + ", ".join(WIPE) + " RESTART IDENTITY")
        else:
            for t in WIPE:
                c.execute(f"DELETE FROM {t}")
            try:  # the numbers (job numbers, ids) start again from 1
                c.execute("DELETE FROM sqlite_sequence WHERE name IN (" + ",".join("?" * len(WIPE)) + ")", tuple(WIPE))
            except Exception:  # noqa: BLE001
                pass
        c.execute("INSERT INTO app_flags(name, value) VALUES('fresh_start', ?)", (f"{datetime.now().isoformat(timespec='seconds')} by {st.session_state.user}",))
    for k in ("_cache", "jb_open", "jb_saved", "jb_sig", "jb_page"):
        st.session_state.pop(k, None)
    return None


def reset_page():
    if not has(CEO):
        return st.error("Only the CEO can use this page.")
    if fresh_start_done():
        return st.success("The data was already cleared. The real data can be recorded now.")
    st.warning("**Use this only once, when the testing is finished.** Everything below is deleted and **cannot be brought back**.")
    a, b = st.columns(2)
    a.markdown("**Deleted**\n\n- all jobs, customers, payments, files and their history\n- all expenses, funds, taxes and debts\n- all materials, stock moves and requests\n- all money requests and the money ledger\n"
               "- the fields (sites) and the categories, and which user belongs to which field")
    b.markdown("**Kept**\n\n- all users: names, passwords and roles\n\nAfter this you add your fields and categories again (Fields & categories), give each user his or her field, and start recording the real data.")
    word = st.text_input("To be sure, type DELETE here", key="fresh_word", placeholder="DELETE")
    if st.button(":material/delete_forever: Delete all data and start fresh", type="primary", key="fresh_go", disabled=word.strip() != "DELETE") and word.strip() == "DELETE":
        err = wipe_everything_but_users()
        if err:
            return st.error(err)
        from inchars.widgets import done
        done("All data and history were deleted. Only the users remain. You can start recording the real data.")
