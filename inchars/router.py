"""The page list for each role, the menu logic, and main()."""

import streamlit as st

from inchars.accounts import get_user, has, load_roles, seed_first_manager, users_exist
from inchars.components.common import show_flash
from inchars.components.layout import field_bar, header, side_menu, user_box
from inchars.config import ACCOUNTANT, ALL, CEO, FIELD, STORE
from inchars.data.materials import pending_material_requests
from inchars.data.money import pending_money_requests
from inchars.db import init_db
from inchars.login import LOGO_URI, email_prompt, login
from inchars.pages.dashboard import dashboard_page
from inchars.pages.expenses import expenses_page
from inchars.pages.funds import funds_page
from inchars.pages.import_costs import page_import
from inchars.pages.material_requests import matreq_page
from inchars.pages.money_requests import moneyreq_page
from inchars.pages.money_used import moneyused_page
from inchars.pages.reports import reports_page
from inchars.pages.setup import page_setup
from inchars.pages.stock import stock_page
from inchars.pages.store import page_count, page_receive, page_use
from inchars.pages.taxes_debts import page_taxdebt
from inchars.pages.users import page_users
from inchars.styles import SIDEBAR_CSS


NEW_RENDER = {"dashboard": dashboard_page, "stock": stock_page, "matreq": matreq_page, "expenses": expenses_page,
              "moneyreq": moneyreq_page, "moneyused": moneyused_page, "funds": funds_page, "reports": reports_page}


PAGE_DEFS = [  # key, label, icon, roles that get the page. A user with several roles gets all their pages.
    ("dashboard", "Dashboard", "dashboard", ALL),
    ("stock", "Material stock", "inventory_2", ALL),
    ("matreq", "Material requests", "shopping_cart", ALL),
    ("receive", "Materials received", "move_to_inbox", (STORE,)),
    ("use", "Materials used", "outbox", (STORE,)),
    ("count", "Stock count", "fact_check", (STORE,)),
    ("expenses", "Expenses", "receipt_long", (CEO, ACCOUNTANT, FIELD)),
    ("moneyreq", "Money requests", "request_quote", ALL),
    ("moneyused", "Money used", "account_balance_wallet", ALL),
    ("funds", "Funds received", "payments", (CEO,)),
    ("taxdebt", "Taxes & debts", "gavel", (CEO, ACCOUNTANT)),
    ("import", "Import from Excel", "upload_file", (CEO, ACCOUNTANT)),
    ("setup", "Fields & categories", "construction", (CEO, ACCOUNTANT)),
    ("reports", "Reports", "assessment", (CEO, ACCOUNTANT, FIELD)),
    ("users", "Users", "group", (CEO,)),
]


RENDER = {**NEW_RENDER, "import": page_import, "setup": page_setup, "users": page_users,
          "receive": page_receive, "use": page_use, "count": page_count, "taxdebt": page_taxdebt}


def allowed_pages():
    out = []
    for key, label, icon, roles in PAGE_DEFS:
        if has(*roles):
            out.append((key, label, icon))
    return out


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
        counts["matreq"], counts["moneyreq"] = pending_material_requests(), pending_money_requests()
    sec, title = side_menu(allowed_pages(), counts)
    header(title)
    show_flash()
    if sec in NEW_RENDER:  # the new design, the same for every role: Field drop-down on top, Excel-style tables
        st.session_state.ceo_scope = field_bar()
    RENDER[sec]()
    with st.sidebar:
        user_box()
