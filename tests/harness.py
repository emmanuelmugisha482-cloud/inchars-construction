"""Shared setup for the tests: loads the app without a browser, creates a temporary database with sample data and one user per role."""
import importlib, os, pkgutil, sys, tempfile, traceback, types
from datetime import date, timedelta
from unittest.mock import MagicMock

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, PROJECT)
import stub_streamlit as stub  # must come before the app is imported
from stub_streamlit import LOG, OVERRIDE, PRESS, STW

sys.modules["altair"] = MagicMock()   # charts are drawn by Altair in the browser; here only our own code around them is checked
sys.modules.setdefault("psycopg2", None)
import pandas as pd

app = types.SimpleNamespace()
modules = {}
NAMES = ["settings", "database", "accounts", "data", "widgets", "router", "health", "construction.dashboard", "construction.materials",
         "construction.money", "construction.admin", "jobs.data", "jobs.page", "jobs.extras"]
for n in NAMES:
    mod = importlib.import_module("inchars." + n)
    modules["inchars." + n] = mod
    for k, v in vars(mod).items():
        if not k.startswith("__"):
            setattr(app, k, v)
modules["inchars.database"].DB = os.path.join(tempfile.mkdtemp(), "smoke.db")
st = app.st
app.init_db()

with app.conn() as c:
    c.execute("INSERT INTO users(username,password_hash,role,email) VALUES('ceo','x','CEO','c@x.rw')")
    c.execute("INSERT INTO users(username,password_hash,role,email) VALUES('fm','x','Field manager','f@x.rw')")
    c.execute("INSERT INTO user_roles(user_id,role) VALUES(1,'CEO')")
    c.execute("INSERT INTO user_roles(user_id,role) VALUES(2,'Field manager')")
    c.execute("INSERT INTO sites(name,client,budget,start_date) VALUES('KDBS Bugesera','KDBS',5000000,'2026-01-01')")
    c.execute("INSERT INTO sites(name,client,budget,start_date) VALUES('Kigali Heights','Acme',0,'2026-02-01')")
    cats = {r["name"]: r["id"] for r in c.execute("SELECT * FROM categories").fetchall()}
    today = date.today()
    for i in range(30):
        d = str(today - timedelta(days=i * 9))
        for sid in (1, 2):
            cid = list(cats.values())[(i + sid) % len(cats)]
            c.execute("INSERT INTO expenses(site_id,category_id,item,qty,unit_price,total,spent_on,entered_by,entered_at,source) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (sid, cid, f"Item {i%7}", 2.0, 10000.0 + i * 500, int((10000 + i * 500) * 2), d, "ceo", d, "entry"))
    c.execute("INSERT INTO funds(site_id,amount,received_on,source,entered_by) VALUES(1,3000000,?,?,?)", (str(today - timedelta(days=60)), "Client", "ceo"))
    c.execute("INSERT INTO materials(name,unit) VALUES('Cement','bag')"); c.execute("INSERT INTO materials(name,unit) VALUES('Steel bar','pc')")
    for mid, kind, q in [(1, "Received", 100), (1, "Used", -40), (2, "Received", 10), (2, "Used", -10)]:
        c.execute("INSERT INTO material_moves(site_id,material_id,kind,qty,moved_on,detail,entered_by,entered_at) VALUES(1,?,?,?,?,?,?,?)",
                  (mid, kind, q, str(today - timedelta(days=3)), "n", "store", "x"))
    c.execute("INSERT INTO material_requests(batch,site_id,material_id,qty_requested,status,needed_by,requested_on,requested_by) VALUES(1,1,1,50,'Pending',?,?,?)", (str(today + timedelta(days=3)), str(today), "fm"))
    c.execute("INSERT INTO material_requests(batch,site_id,material_id,qty_requested,qty_approved,status,needed_by,requested_on,requested_by) VALUES(2,1,2,5,5,'Approved',?,?,?)", (str(today + timedelta(days=3)), str(today), "fm"))
    c.execute("INSERT INTO money_requests(site_id,category,purpose,amount,needed_by,requested_on,requested_by) VALUES(1,'Fuel','Diesel',50000,?,?,?)", (str(today + timedelta(days=2)), str(today), "fm"))
    c.execute("INSERT INTO money_requests(site_id,category,purpose,amount,amount_approved,status,needed_by,requested_on,requested_by) VALUES(1,'Doors','Lock',20000,20000,'Approved',?,?,?)", (str(today + timedelta(days=2)), str(today), "fm"))
    c.execute("INSERT INTO money_ledger(spent_on,person,site_id,kind,category,amount,note,entered_by,entered_at) VALUES(?,?,?,?,?,?,?,?,?)", (str(today), "ceo", 1, "Business", "Fuel", 30000, "Diesel old", "ceo", "x"))
    c.execute("INSERT INTO debts(party,direction,amount,due_on,entered_by,entered_on) VALUES('Supplier','We owe',100000,?,?,?)", (str(today - timedelta(days=5)), "ceo", str(today)))
    c.execute("INSERT INTO taxes(tax_type,amount,paid_on,entered_by) VALUES('VAT',50000,?,?)", (str(today), "ceo"))


# ---- one user per role; the Accountant has two fields ----
with app.conn() as c:
    for uname, role in [("acc", app.ACCOUNTANT), ("store", app.STORE), ("rec", app.RECEPTIONIST), ("eng", app.ENGINEER), ("eng2", app.ENGINEER)]:
        c.execute("INSERT INTO users(username,password_hash,role,email) VALUES(?,?,?,?)", (uname, "x", role, uname + "@x.rw"))
    ids = {r["username"]: r["id"] for r in c.execute("SELECT id,username FROM users").fetchall()}
    for uname, role in [("acc", app.ACCOUNTANT), ("store", app.STORE), ("rec", app.RECEPTIONIST), ("eng", app.ENGINEER), ("eng2", app.ENGINEER)]:
        c.execute("INSERT INTO user_roles(user_id,role) VALUES(?,?)", (ids[uname], role))
    c.execute("DELETE FROM user_roles WHERE user_id=?", (ids["fm"],))
    c.execute("INSERT INTO user_roles(user_id,role) VALUES(?,?)", (ids["fm"], app.FIELD))
    c.execute("UPDATE users SET role=? WHERE id=?", (app.FIELD, ids["fm"]))
    for u in ("acc", "store", "fm"):
        c.execute("INSERT INTO user_sites(user_id,site_id) VALUES(?,1)", (ids[u],))
    c.execute("INSERT INTO user_sites(user_id,site_id) VALUES(?,2)", (ids["acc"],))
users = {"ceo": (1, app.CEO), "acc": (ids["acc"], app.ACCOUNTANT), "store": (ids["store"], app.STORE), "fm": (ids["fm"], app.FIELD),
         "rec": (ids["rec"], app.RECEPTIONIST), "eng": (ids["eng"], app.ENGINEER), "eng2": (ids["eng2"], app.ENGINEER)}


def sign_in(name):
    """Pretend this user is signed in."""
    uid, role = users[name]
    st.session_state.clear()
    st.session_state.update(user=name, uid=uid, roles=[role], role=role, rk=0)


def show_page(key, field="All fields"):
    """Draw one page the way the router does. Returns everything the page wrote as text."""
    st.session_state.ceo_field = field
    st.session_state.ceo_scope = None
    if app.PAGES[key][2]:
        st.session_state.ceo_scope = app.field_bar()
    LOG.clear()
    try:
        app.load_page(key)()
    except stub.Rerun:
        pass
    return " ".join(str(l[1]) for l in LOG if l[0] in ("md", "metric", "caption"))

