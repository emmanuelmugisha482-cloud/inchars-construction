"""The page list for each role, the menu, and main()."""

import importlib
import streamlit as st
import traceback

from inchars.accounts import LOGO_URI, email_prompt, get_user, has, load_roles, login, seed_first_manager, users_exist
from inchars.database import init_db
from inchars.settings import ACCOUNTANT, ALL, CEO, ENGINEER, EVERYONE, FIELD, RECEPTIONIST, SIDEBAR_CSS, STORE
from inchars.widgets import field_bar, header, show_flash, side_menu, user_box


# ======================================================================
# from router.py
# ======================================================================

PAGES = {  # key: (file, function, Field drop-down on top?). Loaded only when the page is opened.
    "dashboard": ("inchars.construction.dashboard", "dashboard_page", True),
    "jobs": ("inchars.jobs.page", "jobs_page", False),
    "stock": ("inchars.construction.materials", "stock_page", True),
    "matreq": ("inchars.construction.materials", "matreq_page", True),
    "receive": ("inchars.construction.materials", "page_receive", False),
    "use": ("inchars.construction.materials", "page_use", False),
    "count": ("inchars.construction.materials", "page_count", False),
    "expenses": ("inchars.construction.money", "expenses_page", True),
    "moneyreq": ("inchars.construction.money", "moneyreq_page", True),
    "moneyused": ("inchars.construction.money", "moneyused_page", True),
    "funds": ("inchars.construction.money", "funds_page", True),
    "taxdebt": ("inchars.construction.money", "page_taxdebt", False),
    "import": ("inchars.construction.admin", "page_import", False),
    "setup": ("inchars.construction.admin", "page_setup", False),
    "reports": ("inchars.construction.admin", "reports_page", True),
    "users": ("inchars.construction.admin", "page_users", False),
    "health": ("inchars.health", "reset_page", False),
}

PAGE_DEFS = [  # key, label, icon, roles that get the page. A user with several roles gets all their pages.
    ("dashboard", "Dashboard", "dashboard", EVERYONE),
    ("jobs", "Jobs", "assignment", (CEO, RECEPTIONIST, ENGINEER)),
    ("stock", "Material stock", "inventory_2", ALL),
    ("matreq", "Material requests", "shopping_cart", ALL),
    ("receive", "Materials received", "move_to_inbox", (STORE,)),
    ("use", "Materials used", "outbox", (STORE,)),
    ("count", "Stock count", "fact_check", (STORE,)),
    ("expenses", "Expenses", "receipt_long", (CEO, ACCOUNTANT, FIELD)),
    ("moneyreq", "Money requests", "request_quote", EVERYONE),
    ("moneyused", "Money used", "account_balance_wallet", EVERYONE),
    ("funds", "Funds received", "payments", (CEO,)),
    ("taxdebt", "Taxes & debts", "gavel", (CEO, ACCOUNTANT)),
    ("import", "Import from Excel", "upload_file", (CEO, ACCOUNTANT)),
    ("setup", "Fields & categories", "construction", (CEO, ACCOUNTANT)),
    ("reports", "Reports", "assessment", (CEO, ACCOUNTANT, FIELD)),
    ("users", "Users", "group", (CEO,)),
    ("health", "Start fresh", "delete_forever", (CEO,)),  # one button, used once after testing; it then disappears
]


def allowed_pages():
    pages = [(key, label, icon) for key, label, icon, roles in PAGE_DEFS if has(*roles)]
    if any(k == "health" for k, _, _ in pages):
        try:
            from inchars.health import fresh_start_done
            if fresh_start_done():  # the button was used: the page is gone
                pages = [p for p in pages if p[0] != "health"]
        except Exception:  # noqa: BLE001  (never hide the menu because of this)
            pass
    return pages


def load_page(key):
    """The function that draws a page. Raises an error if the file is missing or out of date."""
    module, func, _ = PAGES[key]
    return getattr(importlib.import_module(module), func)


def page_failed(title, what, error):
    """Tell that one page has a problem, without stopping the menu or the other pages."""
    traceback.print_exc()
    st.error(f"The page '{title}' {what}. The other pages still work. Please tell the person who looks after the app.")
    if has(CEO):
        hint = "A file may be missing or out of date: upload the whole `inchars` folder again, then reboot the app. " if isinstance(error, ImportError) else ""
        st.info(f"{hint}You can see which part is broken in **System check**.")
        with st.expander("Details for the person fixing it"):
            st.code(traceback.format_exc())


def run_page(key, title):
    """Open a page. A problem in this page shows a message here and nowhere else."""
    try:
        page = load_page(key)
    except Exception as e:  # noqa: BLE001  (a file is missing or old)
        return page_failed(title, "could not be opened", e)
    try:
        page()
    except Exception as e:  # noqa: BLE001
        page_failed(title, "stopped with an error", e)


def safe_count(module, func):
    """The number on a menu item. If it cannot be read the menu simply shows no number."""
    try:
        return int(getattr(importlib.import_module(module), func)())
    except Exception:  # noqa: BLE001
        return 0


def main():
    init_db()
    if "user" not in st.session_state:
        if not users_exist():
            seed_first_manager()
        login()
        return
    me = get_user(st.session_state.uid)
    if me is None:  # the account was deleted
        st.session_state.clear()
        st.rerun()
    roles = load_roles(st.session_state.uid) or [me["role"]]  # read at every step, so new responsibilities apply at once
    st.session_state.roles, st.session_state.role = roles, ", ".join(roles)
    if not me["email"] and not st.session_state.get("skip_email"):
        email_prompt()
        return
    st.markdown(SIDEBAR_CSS, unsafe_allow_html=True)
    with st.sidebar:
        if LOGO_URI:
            st.markdown(f'<img src="{LOGO_URI}" style="width:150px">', unsafe_allow_html=True)
        st.markdown("### Inchars PD Group")
        st.caption("Construction Management")
        st.divider()
    st.session_state.ceo_scope = None  # the pending-request counts below always look at every field
    counts = {}
    if has(CEO, ACCOUNTANT):  # requests waiting for this person
        counts["matreq"] = safe_count("inchars.data", "pending_material_requests")
        counts["moneyreq"] = safe_count("inchars.data", "pending_money_requests")
    if has(CEO, ACCOUNTANT, RECEPTIONIST, ENGINEER):  # unread job notifications
        counts["jobs"] = safe_count("inchars.jobs.data", "unread_count")
    sec, title = side_menu(allowed_pages(), counts)
    header(title)
    show_flash()
    if PAGES[sec][2]:  # the Field drop-down at the top of the data pages
        st.session_state.ceo_scope = field_bar()
    run_page(sec, title)
    with st.sidebar:
        user_box()
