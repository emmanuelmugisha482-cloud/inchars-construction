import base64
import calendar
import difflib
import hashlib
import io
import os
import re
import secrets
import smtplib
import sqlite3
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from email.message import EmailMessage

import pandas as pd
import streamlit as st

try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.pool
except ImportError:  # only needed online (when DATABASE_URL is set)
    psycopg2 = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE_DIR, "construction.db")  # used only when no DATABASE_URL is set (local testing)
BG_IMAGE = os.path.join(BASE_DIR, "assets", "background.jpg")  # optional photo for the login page
LOGO = os.path.join(BASE_DIR, "assets", "logo.png")

CEO, ACCOUNTANT, STORE, FIELD = "CEO", "Accountant", "Store manager", "Field manager"
ROLES = [CEO, ACCOUNTANT, STORE, FIELD]
SEE_ALL = (CEO,)  # only the CEO sees every field; everybody else works on the fields given to them
FUNDS_VISIBLE = (CEO,)  # funds received from clients are shown to these roles only

DEFAULT_CATEGORIES = ["Wages & Salaries", "Cements & Steel", "Plumbing, Petrol & Electricity", "Painting & Transport",
                      "Stones, Sand & Bricks", "Parafo (Ceiling) & Water", "Welding & Ironmongery",
                      "Iron Sheets & Metals", "Doors", "Carpentry & Joinery"]
SHEET_TO_CATEGORY = {  # sheet names used in the KDBS cost workbook -> category names
    "wagessalaries": "Wages & Salaries", "cementssteelsbar": "Cements & Steel",
    "plumbingpetrolelectricity": "Plumbing, Petrol & Electricity", "paintingstransport": "Painting & Transport",
    "stonessandconcasebricks": "Stones, Sand & Bricks", "ceilingfixandwaterbill": "Parafo (Ceiling) & Water",
    "weldingironmongary": "Welding & Ironmongery", "ironsheetsothermetals": "Iron Sheets & Metals",
    "doors": "Doors", "carpentryandjoinery": "Carpentry & Joinery"}
ALIASES = {"item": ("nameofitems", "nameofitem", "namesoftransaction", "nameoftransaction", "item", "description"),
           "qty": ("units", "qty", "quantity"), "price": ("priceunitfrw", "punitfrw", "punit", "priceunit", "unitprice"),
           "total": ("total",), "date": ("date",)}

try:
    st.set_page_config(page_title="Inchars PD Group - Construction Management", page_icon=":material/construction:",
                       layout="wide")
except Exception:
    st.set_page_config(page_title="Inchars PD Group - Construction Management", layout="wide")


def _secret(name):
    try:
        v = st.secrets[name]
    except Exception:
        return None
    return v if isinstance(v, str) else None


DATABASE_URL = _secret("DATABASE_URL") or os.getenv("DATABASE_URL")


IS_PG = bool(DATABASE_URL)


PK = "SERIAL PRIMARY KEY" if IS_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"


IntegrityErrors = (sqlite3.IntegrityError,) + ((psycopg2.IntegrityError,) if psycopg2 else ())


_last_used = {}


@st.cache_resource
def _pool():
    if psycopg2 is None:
        raise RuntimeError("psycopg2 is missing. Add psycopg2-binary to requirements.txt.")
    return psycopg2.pool.ThreadedConnectionPool(1, 10, dsn=DATABASE_URL, connect_timeout=20,
                                                 cursor_factory=psycopg2.extras.RealDictCursor)


def _checkout():
    """Get a working connection. Neon sleeps when idle, so test connections that have been unused for a while."""
    pool, last = _pool(), None
    for _ in range(3):
        raw = None
        try:
            raw = pool.getconn()
            if raw.closed or time.time() - _last_used.get(id(raw), 0) > 90:
                with raw.cursor() as cur:
                    cur.execute("SELECT 1")
                raw.rollback()
            return pool, raw
        except Exception as e:
            last = e
            if raw is not None:
                pool.putconn(raw, close=True)
            time.sleep(1.5)
    raise RuntimeError(f"Cannot reach the database right now. Please try again in a moment. ({last})")


class Conn:
    """Lets the same SQL (written with ? marks) run on SQLite and on PostgreSQL."""

    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, params=()):
        params = tuple(p.item() if hasattr(p, "item") else p for p in params)  # numpy numbers -> python
        cur = self.raw.cursor()
        cur.execute(sql.replace("?", "%s") if IS_PG else sql, params)
        return cur


@contextmanager
def conn():
    if IS_PG:
        pool, raw = _checkout()
        try:
            yield Conn(raw)
            raw.commit()
        except BaseException:
            try:
                raw.rollback()
            except Exception:
                pass
            raise
        finally:
            _last_used[id(raw)] = time.time()
            pool.putconn(raw)
    else:
        raw = sqlite3.connect(DB)
        raw.row_factory = sqlite3.Row
        try:
            yield Conn(raw)
            raw.commit()
        except BaseException:
            raw.rollback()
            raise
        finally:
            raw.close()


def first_manager():
    """The very first Store manager account comes from the app secrets (never from the code)."""
    try:
        s = dict(st.secrets["first_manager"])
    except Exception:
        s = {}
    return (s.get("username") or os.getenv("FIRST_MANAGER_USER"), s.get("password") or os.getenv("FIRST_MANAGER_PASSWORD"),
            s.get("email") or os.getenv("FIRST_MANAGER_EMAIL"))


def df(sql, params=()):
    with conn() as c:
        cur = c.execute(sql, params)
        rows, cols = cur.fetchall(), [x[0] for x in cur.description]
    return pd.DataFrame([list(r.values()) if isinstance(r, dict) else list(r) for r in rows], columns=cols)


def run(sql, params=()):
    with conn() as c:
        c.execute(sql, params)


def users_exist():
    return int(df("SELECT COUNT(*) AS n FROM users")["n"][0]) > 0


def to_excel(sheets):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for name, frame in sheets.items():
            frame.to_excel(w, sheet_name=name[:31], index=False)
            ws = w.sheets[name[:31]]
            ws.freeze_panes = "A2"
            for i, col in enumerate(frame.columns, 1):
                width = max([len(str(col))] + [len(str(v)) for v in frame[col].head(300)]) + 2
                ws.column_dimensions[ws.cell(1, i).column_letter].width = min(width, 60)
    return buf.getvalue()


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def add_user(username, role, email, password):
    username, email = username.strip(), email.strip().lower()
    if not username or " " in username:
        return "Enter a user name without spaces."
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if email and not EMAIL_RE.match(email):
        return "Please enter a valid email address."
    try:
        with conn() as c:
            if c.execute("SELECT id FROM users WHERE lower(username)=lower(?)", (username,)).fetchone():
                return "That user name is already used."
            c.execute("INSERT INTO users(username, password_hash, role, email) VALUES(?,?,?,?)",
                      (username, hash_pw(password), role, email or None))
    except IntegrityErrors:
        return "That email is already used by another account."
    return None


def set_user_password(uid, password):
    if len(password) < 8:
        return "Password must be at least 8 characters."
    run("UPDATE users SET password_hash=? WHERE id=?", (hash_pw(password), uid))
    return None


def delete_user(uid, me):
    if uid == me:
        return "You cannot delete your own account."
    with conn() as c:
        row = c.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()
        if row is None:
            return "User not found."
        if row["role"] == CEO and c.execute("SELECT COUNT(*) AS n FROM users WHERE role=?",
                                                (CEO,)).fetchone()["n"] <= 1:
            return "You cannot delete the last Store manager."
        c.execute("DELETE FROM reset_codes WHERE user_id=?", (uid,))
        c.execute("DELETE FROM users WHERE id=?", (uid,))
    return None


def search_box(key, label="Search drug", placeholder="Type part of a name…"):
    """A visible search bar for a table. Returns the typed text in lower case (press Enter to apply)."""
    return st.text_input(label, placeholder=placeholder, key=key).strip().lower()


def match(frame, cols, text):
    """Keep the rows where any of the columns contains the text (capitals are ignored)."""
    if not text or frame.empty:
        return frame
    hit = pd.Series(False, index=frame.index)
    for c in cols:
        hit |= frame[c].astype(str).str.lower().str.contains(text, regex=False)
    return frame[hit]


def wide(fn, *a, **kw):
    """Full-width table/editor that works on old and new Streamlit versions."""
    try:
        return fn(*a, width="stretch", **kw)
    except Exception:
        return fn(*a, use_container_width=True, **kw)


def K(name):  # widget keys that change after each save, so the form clears itself
    return f"{name}{st.session_state.get('rk', 0)}"


def done(msg):
    st.session_state.flash = msg
    st.session_state.rk = st.session_state.get("rk", 0) + 1
    st.rerun()


def show_flash():
    m = st.session_state.pop("flash", None)
    if m:
        st.success(m)


def hash_pw(pw):
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
    return salt.hex() + "$" + dk.hex()


def check_pw(pw, stored):
    try:
        salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), 200_000)
        return secrets.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def authenticate(username, password):
    with conn() as c:
        u = c.execute("SELECT * FROM users WHERE lower(username)=lower(?)", (username.strip(),)).fetchone()
    return u if u and check_pw(password, u["password_hash"]) else None


def get_user(uid):
    with conn() as c:
        return c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def set_email(uid, email):
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        return "Please enter a valid email address."
    try:
        run("UPDATE users SET email=? WHERE id=?", (email, uid))
    except IntegrityErrors:
        return "That email is already used by another account."
    return None


def smtp_conf():
    try:
        s = dict(st.secrets["smtp"])
    except Exception:
        s = {}
    s = {
        "host": s.get("host") or os.getenv("SMTP_HOST"),
        "port": int(s.get("port") or os.getenv("SMTP_PORT") or 465),
        "user": s.get("user") or os.getenv("SMTP_USER"),
        "password": s.get("password") or os.getenv("SMTP_PASSWORD"),
        "sender": s.get("sender") or os.getenv("SMTP_SENDER"),
    }
    return s if s["host"] and s["user"] and s["password"] else None


def send_email(to, subject, body):
    cfg = smtp_conf()
    if not cfg:
        raise RuntimeError("Email is not configured")
    msg = EmailMessage()
    msg["Subject"], msg["To"] = subject, to
    msg["From"] = cfg["sender"] or cfg["user"]
    msg.set_content(body)
    if cfg["port"] == 465:
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=20) as s:
            s.login(cfg["user"], cfg["password"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as s:
            s.starttls()
            s.login(cfg["user"], cfg["password"])
            s.send_message(msg)


def request_reset(email):
    """Create a 6-digit code (valid 15 min) and email it. Returns (found, code, error)."""
    with conn() as c:
        u = c.execute("SELECT id, username FROM users WHERE lower(email)=lower(?)", (email.strip(),)).fetchone()
        if not u:
            return False, None, None
        code = f"{secrets.randbelow(1_000_000):06d}"
        c.execute("INSERT INTO reset_codes(user_id, code_hash, expires_at, attempts) VALUES(?,?,?,0) "
                  "ON CONFLICT(user_id) DO UPDATE SET code_hash=excluded.code_hash, "
                  "expires_at=excluded.expires_at, attempts=0",
                  (u["id"], hash_pw(code), (datetime.now() + timedelta(minutes=15)).isoformat()))
    try:
        send_email(
            email.strip(),
            "Inchars PD Group - password reset code",
            f"Hello {u['username']},\n\nYour password reset code is: {code}\n"
            "It expires in 15 minutes. If you did not ask for this, ignore this email.\n",
        )
    except Exception as e:
        return True, code, str(e)
    return True, code, None


def reset_password(email, code, new_pw):
    with conn() as c:
        u = c.execute("SELECT id FROM users WHERE lower(email)=lower(?)", (email.strip(),)).fetchone()
        r = c.execute("SELECT * FROM reset_codes WHERE user_id=?", (u["id"],)).fetchone() if u else None
        if not r or datetime.fromisoformat(r["expires_at"]) < datetime.now() or r["attempts"] >= 5:
            return "The code is invalid or has expired. Please request a new one."
        if not check_pw(code.strip(), r["code_hash"]):
            c.execute("UPDATE reset_codes SET attempts=attempts+1 WHERE user_id=?", (u["id"],))
            return "Wrong code."
        c.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_pw(new_pw), u["id"]))
        c.execute("DELETE FROM reset_codes WHERE user_id=?", (u["id"],))
    return None


@st.cache_resource
def _fails():
    return {}


def is_locked(u):
    return _fails().get(u.strip().lower(), (0, 0))[1] > time.time()


def note_fail(u):
    k = u.strip().lower()
    n = _fails().get(k, (0, 0))[0] + 1
    _fails()[k] = (0, time.time() + 300) if n >= 5 else (n, 0)


def clear_fails(u):
    _fails().pop(u.strip().lower(), None)


@st.cache_resource
def bg_uri():
    try:
        from PIL import Image
        img = Image.open(BG_IMAGE).convert("RGB")
        img.thumbnail((1400, 1400))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


LOGIN_CSS = """
<style>
header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stSidebar"] {display:none !important;}
[data-testid="stApp"], [data-testid="stAppViewContainer"] {background:#ffffff !important;}
.left-panel {position:fixed; top:0; left:0; width:50vw; height:100vh; z-index:1;
  background:#9fd0b0 url("__BG__") center/cover no-repeat;}
.left-panel .overlay {position:absolute; inset:0; display:flex; flex-direction:column; justify-content:flex-end;
  padding:3.5rem; background:linear-gradient(to top, rgba(196,228,205,.97) 0%, rgba(196,228,205,.65) 40%, rgba(196,228,205,0) 75%);}
.left-panel h1 {color:#0a3d1f !important; font-size:2.6rem; font-weight:800; line-height:1.15; margin:0; padding:0;}
.left-panel p {color:#2f6b46 !important; font-size:1.5rem; margin:.5rem 0 0 0;}
[data-testid="stMainBlockContainer"], .block-container {margin-left:50vw !important; width:50vw !important;
  max-width:50vw !important; padding:14vh 7vw 2rem 7vw !important;}
[data-testid="stApp"] h2, [data-testid="stApp"] label, [data-testid="stApp"] p, [data-testid="stApp"] span {color:#1f2937;}
.sub {color:#6b7280 !important; margin-bottom:1.2rem;}
[data-testid="stTextInput"] input {background:#f3f4f6 !important; color:#111827 !important;}
.stButton button, [data-testid="stFormSubmitButton"] button {width:100%; border-radius:8px;}
[data-testid="stFormSubmitButton"] button {background:#0b6a35; border:0;}
[data-testid="stFormSubmitButton"] button p {color:#fff !important; font-weight:600;}
@media (max-width: 800px) {
  .left-panel {position:relative; width:100vw; height:30vh;}
  .left-panel .overlay {padding:1.2rem;} .left-panel h1 {font-size:1.5rem;} .left-panel p {font-size:1rem;}
  [data-testid="stMainBlockContainer"], .block-container {margin-left:0 !important; width:100% !important;
    max-width:100% !important; padding:1.5rem !important;}
}
.left-panel {background:linear-gradient(160deg,#eef8f1 0%,#bfe0c9 100%) !important;}
.left-panel .overlay {background:none !important; justify-content:center; align-items:flex-start;}
.left-panel .logo {width:190px; background:#ffffff; border-radius:14px; padding:.6rem 1rem; margin-bottom:1.6rem;
  box-shadow:0 2px 10px rgba(10,61,31,.15);}
@media (max-width: 800px) {.left-panel .logo {width:110px; margin-bottom:.6rem;}}
</style>
"""


def go(view):
    st.session_state.view = view
    st.rerun()


def login():
    st.markdown(LOGIN_CSS.replace("__BG__", bg_uri()), unsafe_allow_html=True)
    st.markdown(LEFT_HTML, unsafe_allow_html=True)
    view = st.session_state.get("view", "login")

    if view == "login":
        st.markdown("## Welcome back")
        st.markdown('<div class="sub">Sign in to continue.</div>', unsafe_allow_html=True)
        if not users_exist():
            st.warning("No accounts exist yet. The owner must add a [first_manager] section to the app "
                       "secrets (see the README), then reload this page.")
        with st.form("login_form"):
            u = st.text_input("User name")
            p = st.text_input("Password", type="password")
            ok = st.form_submit_button("Log in")
        if ok:
            if is_locked(u):
                st.error("Too many wrong attempts. Please wait 5 minutes and try again.")
            else:
                user = authenticate(u, p)
                if user:
                    clear_fails(u)
                    st.session_state.update(user=user["username"], role=user["role"], uid=user["id"])
                    st.rerun()
                else:
                    note_fail(u)
                    st.error("Wrong user name or password.")
        if st.button("Forgot password?"):
            go("forgot")

    elif view == "forgot":
        st.markdown("## Forgot password")
        st.markdown('<div class="sub">Enter the email linked to your account and we will send you a code.</div>',
                    unsafe_allow_html=True)
        with st.form("forgot_form"):
            email = st.text_input("Email")
            send = st.form_submit_button("Send reset code")
        if send:
            if not EMAIL_RE.match(email.strip()):
                st.error("Please enter a valid email address.")
            else:
                found, code, err = request_reset(email)
                dev = os.getenv("STOCK_DEV_SHOW_CODE") == "1"
                if found and err and not dev:
                    st.error("The email could not be sent. Please contact the administrator.")
                else:
                    st.session_state.reset_email = email.strip()
                    st.session_state.dev_code = code if (found and err) else None
                    go("reset")
        if st.button("Back to log in"):
            go("login")

    elif view == "reset":
        st.markdown("## Reset password")
        st.markdown(f'<div class="sub">Enter the code sent to {st.session_state.get("reset_email", "your email")}.</div>',
                    unsafe_allow_html=True)
        st.success("If that email is registered, a 6-digit code has been sent.")
        if st.session_state.get("dev_code"):
            st.info(f"DEV MODE - email not sent. Your code is {st.session_state.dev_code}")
        with st.form("reset_form"):
            code = st.text_input("6-digit code")
            n1 = st.text_input("New password", type="password")
            n2 = st.text_input("Confirm new password", type="password")
            ok = st.form_submit_button("Change password")
        if ok:
            if len(n1) < 8:
                st.error("Password must be at least 8 characters.")
            elif n1 != n2:
                st.error("Passwords do not match.")
            else:
                err = reset_password(st.session_state.get("reset_email", ""), code, n1)
                if err:
                    st.error(err)
                else:
                    st.session_state.view = "login"
                    st.toast("Password changed. You can log in now.")
                    st.rerun()
        if st.button("Back to log in"):
            go("login")


def email_prompt():
    """Shown after login when the account has no recovery email yet."""
    st.title("Add your recovery email")
    st.write("This email is used to reset your password if you forget it.")
    with st.form("email_form"):
        email = st.text_input("Email")
        save = st.form_submit_button("Save email")
    if save:
        err = set_email(st.session_state.uid, email)
        if err:
            st.error(err)
        else:
            st.rerun()
    if st.button("Skip for now"):
        st.session_state.skip_email = True
        st.rerun()


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


SIDEBAR_CSS = """
<style>
[data-testid="stSidebar"] {background:#dff0e4;}
[data-testid="stSidebar"] * {color:#0a3d1f;}
[data-testid="stSidebar"] hr {border-color:#a8d0b5;}
[data-testid="stSidebar"] .stButton {width:100%;}
[data-testid="stSidebar"] .stButton button {width:100%; justify-content:flex-start; border:0; border-radius:8px;
  background:transparent; padding:.55rem .8rem;}
[data-testid="stSidebar"] .stButton button:hover {background:#c6e5d0;}
[data-testid="stSidebar"] .stButton button p {text-align:left;}
[data-testid="stSidebar"] .stButton button[kind="primary"],
[data-testid="stSidebar"] .stButton button[data-testid="stBaseButton-primary"] {background:#0b6a35;}
[data-testid="stSidebarUserContent"] {padding-bottom:4rem;}
.st-key-userbox {position:fixed; bottom:1rem; left:1rem; width:auto !important; max-width:13rem; z-index:99;}
.st-key-userbox button {border:1px solid #a8d0b5; background:#eef8f1; border-radius:999px; min-height:0;
  padding:.2rem .8rem;}
.st-key-userbox button p {font-size:.85rem; margin:0;}
[data-testid="stSidebar"][aria-expanded="false"] .st-key-userbox {display:none;}
[data-testid="stSidebar"] .stButton button[kind="primary"] *,
[data-testid="stSidebar"] .stButton button[data-testid="stBaseButton-primary"] * {color:#ffffff !important;}
</style>
"""
# ---------- database tables ----------
class Abort(Exception):
    pass


SCHEMA = [
    f"""CREATE TABLE IF NOT EXISTS sites(
        id {PK}, name TEXT UNIQUE NOT NULL, client TEXT, location TEXT, budget BIGINT DEFAULT 0, start_date TEXT,
        status TEXT DEFAULT 'Active')""",
    "CREATE TABLE IF NOT EXISTS user_sites(user_id INTEGER, site_id INTEGER, PRIMARY KEY(user_id, site_id))",
    f"CREATE TABLE IF NOT EXISTS categories(id {PK}, name TEXT UNIQUE NOT NULL)",
    f"""CREATE TABLE IF NOT EXISTS expenses(
        id {PK}, site_id INTEGER, category_id INTEGER, item TEXT, qty DOUBLE PRECISION, unit_price DOUBLE PRECISION,
        total BIGINT, spent_on TEXT, entered_by TEXT, entered_at TEXT, source TEXT)""",
    f"""CREATE TABLE IF NOT EXISTS funds(
        id {PK}, site_id INTEGER, amount BIGINT, received_on TEXT, source TEXT, note TEXT, entered_by TEXT)""",
    f"""CREATE TABLE IF NOT EXISTS users(
        id {PK}, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL, email TEXT UNIQUE)""",
    """CREATE TABLE IF NOT EXISTS reset_codes(
        user_id INTEGER PRIMARY KEY, code_hash TEXT NOT NULL, expires_at TEXT NOT NULL, attempts INTEGER DEFAULT 0)""",
    f"CREATE TABLE IF NOT EXISTS materials(id {PK}, name TEXT UNIQUE NOT NULL, unit TEXT)",
    f"""CREATE TABLE IF NOT EXISTS material_moves(
        id {PK}, site_id INTEGER, material_id INTEGER, kind TEXT, qty DOUBLE PRECISION, moved_on TEXT, detail TEXT,
        entered_by TEXT, entered_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_mm_site ON material_moves(site_id)",
    "CREATE INDEX IF NOT EXISTS idx_exp_site ON expenses(site_id)",
    "CREATE INDEX IF NOT EXISTS idx_exp_date ON expenses(spent_on)",
    "CREATE INDEX IF NOT EXISTS idx_funds_site ON funds(site_id)",
]


@st.cache_resource
def init_db():
    with conn() as c:
        if IS_PG:
            c.execute(";\n".join(SCHEMA))
        else:
            for s in SCHEMA:
                c.execute(s)
        if c.execute("SELECT COUNT(*) AS n FROM categories").fetchone()["n"] == 0:
            bulk_insert(c, "categories", ["name"], [(n,) for n in DEFAULT_CATEGORIES])
        if c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0:
            u, p, e = first_manager()
            if u and p:
                c.execute("INSERT INTO users(username, password_hash, role, email) VALUES(?,?,?,?)",
                          (u.strip(), hash_pw(p), CEO, (e or "").strip().lower() or None))


def bulk_insert(c, table, cols, data, chunk=50):
    """Insert many rows with few round trips (one statement per `chunk` rows)."""
    mark = "(" + ",".join("?" * len(cols)) + ")"
    for i in range(0, len(data), chunk):
        part = data[i:i + chunk]
        c.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES " + ",".join([mark] * len(part)),
                  [v for row in part for v in row])


def _logo_uri():
    try:
        with open(LOGO, "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode()
    except Exception:
        return ""


LOGO_URI = _logo_uri()
LEFT_HTML = ('<div class="left-panel"><div class="overlay">' + (f'<img class="logo" src="{LOGO_URI}">' if LOGO_URI else "")
             + '<h1>Inchars PD Group Ltd</h1><p>Construction Management System</p></div></div>')


# ---------- helpers ----------
def money(v):
    return f"{int(round(float(v))):,}"


def norm(s):
    return re.sub(r"[^a-z]", "", str(s).lower())


def _q(v):
    return "" if pd.isna(v) else f"{v:,.2f}".rstrip("0").rstrip(".")


def pretty(frame, money_cols=(), status_col=None, qty_cols=()):
    """Thousands separators for money and quantity columns, and colours for a status column."""
    red, green = "background-color:#fde2e2;color:#7a1f1f", "background-color:#e3f5e8;color:#1b5e30"
    colors = {"Shortfall": red, "OK": green, "No funds": "background-color:#eceff1;color:#455a64",
              "Out of stock": red, "In stock": green}
    try:
        fmt = {c: "{:,.0f}" for c in money_cols if c in frame.columns}
        fmt.update({c: _q for c in qty_cols if c in frame.columns})
        sty = frame.style.format(fmt)
        if status_col in frame.columns:
            sty = (getattr(sty, "map", None) or sty.applymap)(lambda v: colors.get(v, ""), subset=[status_col])
        return sty
    except Exception:
        return frame


def hbar(frame, cat, val, title):
    """Horizontal bar chart, biggest on top."""
    try:
        import altair as alt
    except ImportError:
        st.bar_chart(frame.set_index(cat)[val])
        return
    chart = alt.Chart(frame).mark_bar(color="#0b6a35").encode(
        x=alt.X(f"{val}:Q", title=title), y=alt.Y(f"{cat}:N", sort="-x", title=None, axis=alt.Axis(labelLimit=400)),
        tooltip=[cat, val]).properties(height=34 * len(frame) + 50)
    wide(st.altair_chart, chart)


def visible_sites():
    """CEO and Accountant see every field. Store and Field managers only the fields given to them."""
    if st.session_state.role in SEE_ALL:
        return df("SELECT * FROM sites ORDER BY name")
    return df("""SELECT s.* FROM sites s JOIN user_sites u ON u.site_id=s.id WHERE u.user_id=? ORDER BY s.name""",
              (st.session_state.uid,))


def categories_df():
    return df("SELECT id, name FROM categories ORDER BY name")


EXP_COLS = ["id", "site_id", "Date", "Field", "Category", "Item", "Qty", "Unit price", "Total", "Entered by"]
FUND_COLS = ["id", "site_id", "Date", "Field", "Amount", "Source", "Note", "Entered by"]


def load_expenses(site_ids):
    if not site_ids:
        return pd.DataFrame(columns=EXP_COLS)
    return df(f"""SELECT e.id, e.site_id, e.spent_on AS "Date", s.name AS "Field", c.name AS "Category",
                  e.item AS "Item", e.qty AS "Qty", e.unit_price AS "Unit price", e.total AS "Total",
                  e.entered_by AS "Entered by" FROM expenses e JOIN sites s ON s.id=e.site_id
                  JOIN categories c ON c.id=e.category_id WHERE e.site_id IN ({','.join('?' * len(site_ids))})
                  ORDER BY e.spent_on DESC, e.id DESC""", tuple(site_ids))


def load_funds(site_ids):
    if not site_ids:
        return pd.DataFrame(columns=FUND_COLS)
    return df(f"""SELECT f.id, f.site_id, f.received_on AS "Date", s.name AS "Field", f.amount AS "Amount",
                  f.source AS "Source", f.note AS "Note", f.entered_by AS "Entered by" FROM funds f
                  JOIN sites s ON s.id=f.site_id WHERE f.site_id IN ({','.join('?' * len(site_ids))})
                  ORDER BY f.received_on DESC, f.id DESC""", tuple(site_ids))


def filter_bar(sites, exp, prefix):
    """Field, category and period filters (like the filters at the top of the Excel dashboard)."""
    f1, f2, f3, f4 = st.columns([2, 2, 1.5, 1.5])
    if len(sites) > 1:
        field = f1.selectbox("Field", ["All fields"] + sites.name.tolist(), key=f"{prefix}_field")
    else:
        field = sites.name.iloc[0]
        f1.markdown(f"**Field:** {field}")
    cat = f2.selectbox("Category", ["All categories"] + categories_df().name.tolist(), key=f"{prefix}_cat")
    lo = date.fromisoformat(exp.Date.min()) if len(exp) else date.today()
    hi = max(date.fromisoformat(exp.Date.max()), date.today()) if len(exp) else date.today()
    d_from = f3.date_input("From", lo, key=f"{prefix}_from")
    d_to = f4.date_input("To", hi, key=f"{prefix}_to")
    scope = exp if field == "All fields" else exp[exp.Field == field]
    sel = scope[(scope.Date >= str(d_from)) & (scope.Date <= str(d_to))]
    if cat != "All categories":
        sel = sel[sel.Category == cat]
    return field, cat, scope, sel


def no_field_message():
    if st.session_state.role in SEE_ALL:
        st.info("There is no field yet. Create the first one in Fields & categories.")
    else:
        st.info("No field is linked to your account yet. Please ask the CEO to give you access to a field.")


# ---------- dashboard ----------
def page_dashboard():
    can_funds = st.session_state.role in FUNDS_VISIBLE
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    ids = [int(i) for i in sites.id]
    exp = load_expenses(ids)
    fnd = load_funds(ids) if can_funds else pd.DataFrame(columns=FUND_COLS)
    field, cat, scope, sel = filter_bar(sites, exp, "d")
    fscope = fnd if field == "All fields" else fnd[fnd.Field == field]
    total, n, spent_all = float(sel.Total.sum()), len(sel), float(scope.Total.sum())
    bsites = sites[sites.budget.fillna(0) > 0] if field == "All fields" else sites[(sites.name == field) & (sites.budget.fillna(0) > 0)]
    budget = float(bsites.budget.sum())  # only fields that have a budget are compared with it
    spent_budgeted = float(scope[scope.site_id.isin(bsites.id)].Total.sum())

    st.caption("All amounts are in Frw. The numbers below follow the filters above.")
    k = st.columns(5)
    k[0].metric("Total spend", money(total))
    k[1].metric("Transactions", n)
    k[2].metric("Average per transaction", money(total / n if n else 0))
    k[3].metric("Largest transaction", money(sel.Total.max() if n else 0))
    k[4].metric("Share of the field's spend", f"{total / spent_all * 100:.1f}%" if spent_all else "0%")

    st.subheader("Whole field" if field != "All fields" else "All fields together")
    st.caption("Not affected by the category and period filters.")
    items = [("Recorded expenditure", money(spent_all))]
    if budget:
        items += [("Budget", money(budget)), ("Budget used", f"{spent_budgeted / budget * 100:.1f}%")]
    if can_funds:
        funds_all = float(fscope.Amount.sum())
        balance = funds_all - spent_all
        items += [("Funds received", money(funds_all)), ("Balance / (shortfall)", ("-" if balance < 0 else "") + money(abs(balance))),
                  ("Funds used", f"{spent_all / funds_all * 100:.0f}%" if funds_all else "no funds yet")]
    for col, (label, value) in zip(st.columns(len(items)), items):
        col.metric(label, value)

    if field == "All fields" and len(sites) > 1:
        st.subheader("Fields")
        per = sites[["id", "name", "client", "budget"]].copy()
        per["Spent"] = per.id.map(exp.groupby("site_id").Total.sum()).fillna(0).astype(int)
        per["Budget used"] = [f"{s / b * 100:.1f}%" if b else "-" for s, b in zip(per.Spent, per.budget.fillna(0))]
        per["Last entry"] = per.id.map(exp.groupby("site_id").Date.max()).fillna("")
        per["budget"] = per.budget.fillna(0).astype(int)
        cols, money_cols = ["Field", "Client", "Budget", "Spent", "Budget used", "Last entry"], ["Budget", "Spent"]
        if can_funds:
            per["Funds received"] = per.id.map(fnd.groupby("site_id").Amount.sum()).fillna(0).astype(int)
            per["Balance"] = per["Funds received"] - per["Spent"]
            per["Funds used"] = [f"{s / f * 100:.0f}%" if f else "-" for s, f in zip(per.Spent, per["Funds received"])]
            per["Status"] = ["Shortfall" if b < 0 else ("No funds" if f == 0 else "OK") for b, f in zip(per.Balance, per["Funds received"])]
            cols = ["Field", "Client", "Budget", "Funds received", "Spent", "Balance", "Funds used", "Budget used", "Last entry", "Status"]
            money_cols = ["Budget", "Funds received", "Spent", "Balance"]
        per = per.rename(columns={"name": "Field", "client": "Client", "budget": "Budget"})
        q = search_box("q_fields", "Search field or client", "Type part of a name…")
        wide(st.dataframe, pretty(match(per, ["Field", "Client"], q)[cols], money_cols, "Status"), hide_index=True)
        hbar(per.sort_values("Spent", ascending=False).head(10)[["Field", "Spent"]], "Field", "Spent", "Spent (Frw)")

    if st.session_state.role == CEO:  # progress of the materials on every field
        st.subheader("Materials on site")
        mv = load_moves(ids)
        if mv.empty:
            st.caption("No materials have been recorded yet.")
        else:
            recent, bal = str(date.today() - timedelta(days=30)), stock_balances(mv)
            mat = pd.DataFrame({"Field": sites.name.values})
            mat["Items in stock"] = mat.Field.map(bal[bal.Balance > 0].groupby("Field").size()).fillna(0).astype(int)
            mat["Items out of stock"] = mat.Field.map(bal[bal.Balance <= 0].groupby("Field").size()).fillna(0).astype(int)
            mat["Received (30 days)"] = mat.Field.map(mv[(mv.Type == "Received") & (mv.Date >= recent)].groupby("Field").size()).fillna(0).astype(int)
            mat["Used (30 days)"] = mat.Field.map(mv[(mv.Type == "Used") & (mv.Date >= recent)].groupby("Field").size()).fillna(0).astype(int)
            mat["Last movement"] = mat.Field.map(mv.groupby("Field").Date.max()).fillna("")
            st.caption("Received and used show the number of lines recorded in the last 30 days. Open Material stock for the details.")
            wide(st.dataframe, mat, hide_index=True)

    st.subheader("Category ranking")
    if sel.empty:
        st.info("No spending in this selection.")
    else:
        rk = sel.groupby("Category").agg(Amount=("Total", "sum"), Transactions=("Total", "size")).reset_index()
        rk = rk.sort_values("Amount", ascending=False).reset_index(drop=True)
        rk.insert(0, "Rank", range(1, len(rk) + 1))
        rk["Share"] = [f"{a / rk.Amount.sum() * 100:.1f}%" for a in rk.Amount]
        wide(st.dataframe, pretty(rk, ["Amount"]), hide_index=True)
        hbar(rk.head(10)[["Category", "Amount"]], "Category", "Amount", "Amount (Frw)")
        st.subheader("Spending by month")
        st.bar_chart(sel.assign(Month=sel.Date.str[:7]).groupby("Month").Total.sum())


# ---------- expenses ----------
def parse_entries(ed, cat_ids):
    ok, bad = [], []
    for pos, (_, r) in enumerate(ed.iterrows(), start=1):
        item = str(r["Item"]).strip() if pd.notna(r["Item"]) else ""
        cat = r["Category"] if pd.notna(r["Category"]) else None
        qty, price = pd.to_numeric(r["Qty"], errors="coerce"), pd.to_numeric(r["Unit price"], errors="coerce")
        if not item and not cat and (pd.isna(price) or price == 0):
            continue  # empty row
        d = pd.to_datetime(r["Date"], errors="coerce")
        d = date.today() if pd.isna(d) else d.date()
        if not item:
            bad.append(f"Row {pos}: write the item name.")
        elif cat not in cat_ids:
            bad.append(f"Row {pos}: choose a category.")
        elif pd.isna(qty) or qty <= 0:
            bad.append(f"Row {pos}: the quantity must be above 0.")
        elif pd.isna(price) or price <= 0:
            bad.append(f"Row {pos}: enter the unit price.")
        elif d > date.today():
            bad.append(f"Row {pos}: the date is in the future.")
        else:
            ok.append({"date": d, "category": cat, "item": item, "qty": float(qty), "price": float(price),
                       "total": int(round(float(qty) * float(price)))})
    return ok, bad


def add_expenses(site_id, cat_ids, entries, user):
    now = datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        bulk_insert(c, "expenses", ["site_id", "category_id", "item", "qty", "unit_price", "total", "spent_on",
                                    "entered_by", "entered_at", "source"],
                    [(site_id, cat_ids[e["category"]], e["item"], e["qty"], e["price"], e["total"], str(e["date"]),
                      user, now, "entry") for e in entries])


def delete_expenses(ids, role, user, uid):
    """The Accountant can delete any entry of their fields. Others only their own entries of today."""
    n = 0
    with conn() as c:
        for rid in ids:
            if role == ACCOUNTANT:
                n += c.execute("DELETE FROM expenses WHERE id=? AND site_id IN (SELECT site_id FROM user_sites WHERE user_id=?)",
                               (rid, uid)).rowcount
            else:
                n += c.execute("""DELETE FROM expenses WHERE id=? AND entered_by=? AND SUBSTR(entered_at,1,10)=?
                                  AND site_id IN (SELECT site_id FROM user_sites WHERE user_id=?)""",
                               (rid, user, str(date.today()), uid)).rowcount
    return n


def page_expenses():
    role, user, uid = st.session_state.role, st.session_state.user, st.session_state.uid
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    site_ids = {r.name: int(r.id) for r in sites.itertuples()}
    cats = categories_df()
    cat_ids = dict(zip(cats.name, cats.id.astype(int)))
    if role != CEO:
        st.subheader("Add expenses")
        st.caption("Fill one line for each purchase or payment. Add more lines with the + at the bottom of the table.")
        a1, _ = st.columns([2, 3])
        if len(site_ids) > 1:
            field = a1.selectbox("Field", list(site_ids), key=K("ef"))
        else:
            field = list(site_ids)[0]
            a1.markdown(f"**Field:** {field}")
        grid = pd.DataFrame({"Date": [date.today()] * 5, "Category": [None] * 5, "Item": [""] * 5,
                             "Qty": [1.0] * 5, "Unit price": [0.0] * 5})
        ed = wide(st.data_editor, grid, num_rows="dynamic", hide_index=True, key=K("eg"), column_config={
            "Date": st.column_config.DateColumn("Date", max_value=date.today(), format="YYYY-MM-DD"),
            "Category": st.column_config.SelectboxColumn("Category", options=list(cat_ids), required=False),
            "Item": st.column_config.TextColumn("Item"),
            "Qty": st.column_config.NumberColumn("Qty", min_value=0.0, step=0.01),
            "Unit price": st.column_config.NumberColumn("Unit price (Frw)", min_value=0.0, step=0.01)})
        ok, bad = parse_entries(ed, cat_ids)
        if ok:
            st.write(f"**{len(ok)} line(s) ready, total {money(sum(e['total'] for e in ok))} Frw**")
        for msg in bad:
            st.warning(msg)
        if st.button("Save entries", type="primary"):
            if bad:
                st.error("Please correct the lines marked above.")
            elif not ok:
                st.info("Nothing to save yet.")
            else:
                add_expenses(site_ids[field], cat_ids, ok, user)
                done(f"{len(ok)} entr{'y' if len(ok) == 1 else 'ies'} saved for {field}.")

    st.subheader("Entries")
    g1, g2, g3 = st.columns([2, 2, 3])
    f_field = g1.selectbox("Field", ["All fields"] + list(site_ids), key="el_f") if len(site_ids) > 1 else "All fields"
    f_cat = g2.selectbox("Category", ["All categories"] + list(cat_ids), key="el_c")
    with g3:
        q = search_box("q_exp", "Search item or person", "Type part of an item name…")
    view = load_expenses(list(site_ids.values()))
    if f_field != "All fields":
        view = view[view.Field == f_field]
    if f_cat != "All categories":
        view = view[view.Category == f_cat]
    view = match(view, ["Item", "Entered by"], q)
    st.caption(f"{len(view)} entries, total {money(view.Total.sum())} Frw" + (" (showing the latest 300)" if len(view) > 300 else ""))
    shown = view.head(300)
    cols = ["Date", "Field", "Category", "Item", "Qty", "Unit price", "Total", "Entered by"]
    if role == CEO:
        wide(st.dataframe, pretty(shown[cols], ["Unit price", "Total"]), hide_index=True)
        return
    tbl = shown[cols].copy()
    tbl["Remove"] = False
    ed2 = wide(st.data_editor, tbl, hide_index=True, key=f"{K('el')}_{len(shown)}", disabled=cols)
    st.caption("Tick Remove, then press Delete. " + ("You can delete any entry of your fields." if role == ACCOUNTANT
                                                       else "You can delete your own entries from today."))
    if st.button("Delete ticked entries"):
        ids = [int(i) for i, r in zip(shown.id, ed2["Remove"]) if r]
        if not ids:
            st.info("Tick the entries you want to delete.")
        else:
            n = delete_expenses(ids, role, user, uid)
            done(f"{n} entr{'y' if n == 1 else 'ies'} deleted." +
                 (f" {len(ids) - n} could not be deleted." if n < len(ids) else ""))


# ---------- funds received (CEO only) ----------
def page_funds():
    if st.session_state.role not in FUNDS_VISIBLE:
        return st.error("Funds received can only be seen by the CEO.")
    user = st.session_state.user
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    site_ids = {r.name: int(r.id) for r in sites.itertuples()}
    st.subheader("Record funds received")
    a1, a2, a3 = st.columns([2, 2, 2])
    field = a1.selectbox("Field", list(site_ids), key=K("ff"))
    amount = a2.number_input("Amount (Frw)", min_value=1, step=1000, value=None, placeholder="Enter amount", key=K("fa"))
    day = a3.date_input("Date received", date.today(), max_value=date.today(), key=K("fd"))
    b1, b2 = st.columns(2)
    source = b1.text_input("Received from", placeholder="For example the client name", key=K("fs"))
    note = b2.text_input("Note (optional)", key=K("fn"))
    if st.button("Save funds", type="primary"):
        if not amount:
            st.warning("Enter the amount.")
        else:
            run("INSERT INTO funds(site_id, amount, received_on, source, note, entered_by) VALUES(?,?,?,?,?,?)",
                (site_ids[field], int(amount), str(day), source.strip(), note.strip(), user))
            done(f"{money(amount)} Frw recorded for {field}.")
    st.subheader("Funds received")
    q = search_box("q_funds", "Search field, source or note", "Type part of a name…")
    fnd = match(load_funds(list(site_ids.values())), ["Field", "Source", "Note"], q)
    st.caption(f"{len(fnd)} records, total {money(fnd.Amount.sum())} Frw")
    cols = ["Date", "Field", "Amount", "Source", "Note", "Entered by"]
    tbl = fnd[cols].copy()
    tbl["Remove"] = False
    ed = wide(st.data_editor, tbl, hide_index=True, key=f"{K('fl')}_{len(fnd)}", disabled=cols)
    if st.button("Delete ticked records"):
        ids = [int(i) for i, r in zip(fnd.id, ed["Remove"]) if r]
        if not ids:
            st.info("Tick the records you want to delete.")
        else:
            with conn() as c:
                for rid in ids:
                    c.execute("DELETE FROM funds WHERE id=?", (rid,))
            done(f"{len(ids)} record(s) deleted.")


# ---------- materials on site (Store manager) ----------
MOVE_COLS = ["id", "site_id", "material_id", "Date", "Field", "Material", "Unit", "Type", "Qty", "Detail", "Entered by"]


def load_materials():
    return df("SELECT id, name, unit FROM materials ORDER BY name")


def load_moves(site_ids):
    if not site_ids:
        return pd.DataFrame(columns=MOVE_COLS)
    return df(f"""SELECT m.id, m.site_id, m.material_id, m.moved_on AS "Date", s.name AS "Field", t.name AS "Material",
                  t.unit AS "Unit", m.kind AS "Type", m.qty AS "Qty", m.detail AS "Detail", m.entered_by AS "Entered by"
                  FROM material_moves m JOIN sites s ON s.id=m.site_id JOIN materials t ON t.id=m.material_id
                  WHERE m.site_id IN ({','.join('?' * len(site_ids))}) ORDER BY m.moved_on DESC, m.id DESC""", tuple(site_ids))


def stock_balances(mv):
    """One row per field and material: received, used, count corrections and the balance."""
    cols = ["site_id", "material_id", "Field", "Material", "Unit", "Received", "Used", "Corrected", "Balance"]
    if mv.empty:
        return pd.DataFrame(columns=cols)
    q = pd.to_numeric(mv.Qty)
    g = mv.assign(Qty=q, R=q.where(mv.Type == "Received", 0.0), U=(-q).where(mv.Type == "Used", 0.0),
                  C=q.where(mv.Type == "Count", 0.0))
    out = g.groupby(["site_id", "material_id", "Field", "Material", "Unit"], as_index=False).agg(
        Received=("R", "sum"), Used=("U", "sum"), Corrected=("C", "sum"), Balance=("Qty", "sum"))
    out["Status"] = ["In stock" if b > 1e-9 else "Out of stock" for b in out.Balance]
    return out.sort_values(["Field", "Material"]).reset_index(drop=True)


def parse_moves(ed, mat_ids):
    ok, bad = [], []
    for pos, (_, r) in enumerate(ed.iterrows(), start=1):
        mat = r["Material"] if pd.notna(r["Material"]) else None
        qty = pd.to_numeric(r["Qty"], errors="coerce")
        detail = str(r["Detail"]).strip() if pd.notna(r["Detail"]) else ""
        if not mat and (pd.isna(qty) or qty == 0) and not detail:
            continue  # empty row
        d = pd.to_datetime(r["Date"], errors="coerce")
        d = date.today() if pd.isna(d) else d.date()
        if mat not in mat_ids:
            bad.append(f"Row {pos}: choose a material.")
        elif pd.isna(qty) or qty <= 0:
            bad.append(f"Row {pos}: the quantity must be above 0.")
        elif d > date.today():
            bad.append(f"Row {pos}: the date is in the future.")
        else:
            ok.append({"date": d, "material": mat, "qty": float(qty), "detail": detail})
    return ok, bad


def add_moves(site_id, mat_ids, kind, entries, user):
    """Received adds to the stock and Used takes away. Returns an error text, or None when saved."""
    sign, now = (1 if kind == "Received" else -1), datetime.now().isoformat(timespec="seconds")
    try:
        with conn() as c:
            if kind == "Used":  # never use more than is in stock
                need = {}
                for e in entries:
                    need[e["material"]] = need.get(e["material"], 0) + e["qty"]
                for name, q in need.items():
                    have = float(c.execute("SELECT COALESCE(SUM(qty), 0) AS b FROM material_moves WHERE site_id=? AND material_id=?",
                                           (site_id, mat_ids[name])).fetchone()["b"])
                    if q > have + 1e-9:
                        raise Abort(f"{name.split(' (')[0]}: only {have:g} in stock.")
            bulk_insert(c, "material_moves", ["site_id", "material_id", "kind", "qty", "moved_on", "detail", "entered_by", "entered_at"],
                        [(site_id, mat_ids[e["material"]], kind, sign * e["qty"], str(e["date"]), e["detail"], user, now) for e in entries])
    except Abort as e:
        return str(e)
    return None


def count_stock(site_id, counted, note, user):
    """counted = {material_id: counted quantity}. Saves the difference as a 'Count' correction."""
    now, changed = datetime.now().isoformat(timespec="seconds"), 0
    with conn() as c:
        for mid, qty in counted.items():
            have = float(c.execute("SELECT COALESCE(SUM(qty), 0) AS b FROM material_moves WHERE site_id=? AND material_id=?",
                                   (site_id, mid)).fetchone()["b"])
            if abs(qty - have) > 1e-9:
                c.execute("INSERT INTO material_moves(site_id, material_id, kind, qty, moved_on, detail, entered_by, entered_at) "
                          "VALUES(?,?,?,?,?,?,?,?)", (site_id, mid, "Count", qty - have, str(date.today()),
                                                      f"Stock count: {note}".strip(), user, now))
                changed += 1
    return changed


def pick_field(sites, key):
    if len(sites) == 1:
        st.markdown(f"**Field:** {sites.name.iloc[0]}")
        return sites.name.iloc[0]
    return st.selectbox("Field", sites.name.tolist(), key=key)


def move_columns(kind, labels):
    return {"Date": st.column_config.DateColumn("Date", max_value=date.today(), format="YYYY-MM-DD"),
            "Material": st.column_config.SelectboxColumn("Material", options=labels, required=False),
            "Qty": st.column_config.NumberColumn("Quantity", min_value=0.0, step=0.01),
            "Detail": st.column_config.TextColumn("Supplier / delivery note" if kind == "Received" else "Used for (work or activity)")}


def recent_moves(site_id, kind, key):
    st.subheader("Recent entries")
    q = search_box(key, "Search material or note", "Type part of a name…")
    mv = load_moves([site_id])
    mv = match(mv[mv.Type == kind], ["Material", "Detail", "Entered by"], q).head(100)
    wide(st.dataframe, pretty(mv.assign(Qty=mv.Qty.abs())[["Date", "Material", "Unit", "Qty", "Detail", "Entered by"]],
                              qty_cols=["Qty"]), hide_index=True)


def page_receive():
    if st.session_state.role != STORE:
        return st.error("This page is for the Store manager.")
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    field = pick_field(sites, K("rf"))
    sid = int(sites[sites.name == field].id.iloc[0])
    mats = load_materials()
    with st.expander("Add a new material to the list"):
        n1, n2, n3 = st.columns([3, 2, 1])
        nm = n1.text_input("Material name", key=K("mn"))
        nu = n2.text_input("Unit", placeholder="bags, pcs, kg, m3, litres, tonnes…", key=K("mu"))
        n3.write("")
        if n3.button("Add"):
            if not nm.strip() or not nu.strip():
                st.warning("Write the material name and its unit.")
            else:
                try:
                    run("INSERT INTO materials(name, unit) VALUES(?,?)", (nm.strip(), nu.strip()))
                    done(f"{nm.strip()} added to the list.")
                except IntegrityErrors:
                    st.error("That material is already in the list.")
    if mats.empty:
        return st.info("The materials list is empty. Add your first material above.")
    labels = {f"{r.name} ({r.unit})": int(r.id) for r in mats.itertuples()}
    st.subheader("Materials received")
    st.caption("One line for each delivery. Add more lines with the + at the bottom of the table.")
    grid = pd.DataFrame({"Date": [date.today()] * 5, "Material": [None] * 5, "Qty": [0.0] * 5, "Detail": [""] * 5})
    ed = wide(st.data_editor, grid, num_rows="dynamic", hide_index=True, key=K("rg"), column_config=move_columns("Received", list(labels)))
    ok, bad = parse_moves(ed, labels)
    if ok:
        st.write(f"**{len(ok)} line(s) ready to save.**")
    for msg in bad:
        st.warning(msg)
    if st.button("Save received materials", type="primary"):
        if bad:
            st.error("Please correct the lines marked above.")
        elif not ok:
            st.info("Nothing to save yet.")
        else:
            add_moves(sid, labels, "Received", ok, st.session_state.user)
            done(f"{len(ok)} delivery line(s) saved for {field}.")
    recent_moves(sid, "Received", "q_rec")


def page_use():
    if st.session_state.role != STORE:
        return st.error("This page is for the Store manager.")
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    field = pick_field(sites, K("uf"))
    sid = int(sites[sites.name == field].id.iloc[0])
    bal = stock_balances(load_moves([sid]))
    avail = bal[bal.Balance > 1e-9]
    if avail.empty:
        return st.info("There is nothing in stock yet. Record the materials received first.")
    labels = {f"{r.Material} ({r.Unit}) - {r.Balance:g} in stock": int(r.material_id) for r in avail.itertuples()}
    st.subheader("Materials used")
    st.caption("One line for each use. You cannot use more than the quantity in stock.")
    grid = pd.DataFrame({"Date": [date.today()] * 5, "Material": [None] * 5, "Qty": [0.0] * 5, "Detail": [""] * 5})
    ed = wide(st.data_editor, grid, num_rows="dynamic", hide_index=True, key=K("ug"), column_config=move_columns("Used", list(labels)))
    ok, bad = parse_moves(ed, labels)
    if ok:
        st.write(f"**{len(ok)} line(s) ready to save.**")
    for msg in bad:
        st.warning(msg)
    if st.button("Save used materials", type="primary"):
        if bad:
            st.error("Please correct the lines marked above.")
        elif not ok:
            st.info("Nothing to save yet.")
        else:
            err = add_moves(sid, labels, "Used", ok, st.session_state.user)
            if err:
                st.error(err)
            else:
                done(f"{len(ok)} usage line(s) saved for {field}.")
    recent_moves(sid, "Used", "q_use")


def page_count():
    if st.session_state.role != STORE:
        return st.error("This page is for the Store manager.")
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    field = pick_field(sites, K("cf"))
    sid = int(sites[sites.name == field].id.iloc[0])
    mats = load_materials()
    if mats.empty:
        return st.info("The materials list is empty.")
    st.write("Count what is really in the store. Search for a material, type the counted quantity, then search for the "
             "next one. Your counts are kept while you search. Leave **Counted** empty for materials you did not count.")
    bal = stock_balances(load_moves([sid]))
    have = dict(zip(bal.material_id.astype(int), bal.Balance)) if len(bal) else {}
    base = mats.assign(system=[float(have.get(int(i), 0.0)) for i in mats.id])
    counts = st.session_state.setdefault("mcount", {})  # {(field id, material id): counted}
    q = search_box("q_count", "Search material", "Type part of a material name…")
    shown = match(base, ["name"], q)
    grid = pd.DataFrame({"Material": shown.name.values, "Unit": shown.unit.values, "In system": shown.system.values,
                         "Counted": pd.array([counts.get((sid, int(i))) for i in shown.id], dtype="Float64")})
    ed = wide(st.data_editor, grid, hide_index=True, height=420, disabled=["Material", "Unit", "In system"],
              column_config={"Counted": st.column_config.NumberColumn("Counted", min_value=0.0, step=0.01)},
              key=f"mc{K('c')}_{sid}_{q}")
    for i, c in zip(shown.id, ed["Counted"]):
        if pd.notna(c):
            counts[(sid, int(i))] = float(c)
        else:
            counts.pop((sid, int(i)), None)
    sysq, names = dict(zip(base.id.astype(int), base.system)), dict(zip(base.id.astype(int), base.name))
    diff = pd.DataFrame([{"Material": names[m], "In system": sysq[m], "Counted": c, "Difference": c - sysq[m]}
                         for (s_, m), c in counts.items() if s_ == sid and m in sysq and abs(c - sysq[m]) > 1e-9])
    if len(diff):
        st.write(f"**{len(diff)} material(s) will be corrected:**")
        wide(st.dataframe, pretty(diff, qty_cols=["In system", "Counted", "Difference"]), hide_index=True)
    reason = st.text_input("Note", "Physical count", key=K("cn"))
    b1, b2 = st.columns(2)
    if b1.button("Save stock count", type="primary", disabled=diff.empty):
        n = count_stock(sid, {m: c for (s_, m), c in counts.items() if s_ == sid and m in sysq}, reason, st.session_state.user)
        for key in [k for k in counts if k[0] == sid]:
            counts.pop(key)
        done(f"Stock corrected for {n} material(s).")
    if b2.button("Clear my counts", disabled=not any(k[0] == sid for k in counts)):
        for key in [k for k in counts if k[0] == sid]:
            counts.pop(key)
        done("Counts cleared.")


def page_stock():
    """Stock balance of the materials. The Store manager's dashboard, and a read-only view for the others."""
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    ids = [int(i) for i in sites.id]
    mv = load_moves(ids)
    f1, f2 = st.columns([2, 3])
    if len(sites) > 1:
        field = f1.selectbox("Field", ["All fields"] + sites.name.tolist(), key="ms_field")
    else:
        field = sites.name.iloc[0]
        f1.markdown(f"**Field:** {field}")
    with f2:
        q = search_box("q_ms", "Search material", "Type part of a material name…")
    view = mv if field == "All fields" else mv[mv.Field == field]
    bal = stock_balances(view)
    recent = str(date.today() - timedelta(days=30))
    k = st.columns(4)
    k[0].metric("Materials in stock", int((bal.Balance > 1e-9).sum()))
    k[1].metric("Out of stock", int((bal.Balance <= 1e-9).sum()))
    k[2].metric("Received (30 days)", int(((view.Type == "Received") & (view.Date >= recent)).sum()))
    k[3].metric("Used (30 days)", int(((view.Type == "Used") & (view.Date >= recent)).sum()))
    st.subheader("Stock balance")
    shown = match(bal, ["Material"], q)
    cols = (["Field"] if field == "All fields" else []) + ["Material", "Unit", "Received", "Used", "Corrected", "Balance", "Status"]
    if bal.empty:
        st.info("No materials have been recorded yet.")
    else:
        wide(st.dataframe, pretty(shown[cols], status_col="Status", qty_cols=["Received", "Used", "Corrected", "Balance"]), hide_index=True)
    st.subheader("Movements")
    m1, m2 = st.columns([2, 3])
    kind = m1.radio("Show", ["All", "Received", "Used", "Count"], horizontal=True, key="ms_kind")
    with m2:
        q2 = search_box("q_mv", "Search material, note or person", "Type part of a name…")
    mvs = view if kind == "All" else view[view.Type == kind]
    mvs = match(mvs, ["Material", "Detail", "Entered by"], q2)
    st.caption(f"{len(mvs)} movements" + (" (showing the latest 300)" if len(mvs) > 300 else ""))
    mcols = ["Date", "Field", "Material", "Unit", "Type", "Qty", "Detail", "Entered by"]
    wide(st.dataframe, pretty(mvs.head(300)[mcols], qty_cols=["Qty"]), hide_index=True)
    if not bal.empty:
        st.download_button(":material/download: Download stock report (Excel)",
                           to_excel({"Stock balance": bal.drop(columns=["site_id", "material_id"]), "Movements": view[mcols]}),
                           f"material_stock_{date.today()}.xlsx", mime=XLSX, key="dl_stock")


# ---------- fields and categories ----------
def add_site(name, client, location, budget, start):
    with conn() as c:
        return c.execute("INSERT INTO sites(name, client, location, budget, start_date) VALUES(?,?,?,?,?) RETURNING id",
                         (name.strip(), client.strip(), location.strip(), int(budget or 0), str(start))).fetchone()["id"]


def page_setup():
    role = st.session_state.role
    if role not in (CEO, ACCOUNTANT):
        return st.error("This page is for the CEO and the Accountant.")
    if role == CEO:
        setup_fields()
    setup_categories()


def setup_fields():
    st.subheader("Fields")
    st.caption("A field is one construction project or site.")
    a1, a2, a3 = st.columns(3)
    name = a1.text_input("Field name", key=K("sn"))
    client = a2.text_input("Client", key=K("sc"))
    location = a3.text_input("Location", key=K("sl"))
    b1, b2 = st.columns(2)
    budget = b1.number_input("Budget (Frw, optional)", min_value=0, step=100000, value=None, placeholder="Enter budget", key=K("sb"))
    start = b2.date_input("Start date", date.today(), key=K("sd"))
    if st.button("Add field", type="primary"):
        if not name.strip():
            st.warning("Write the field name.")
        else:
            try:
                add_site(name, client, location, budget, start)
                done(f"Field {name.strip()} added.")
            except IntegrityErrors:
                st.error("A field with that name already exists.")
    sites = df("SELECT id, name, client, location, budget, status FROM sites ORDER BY name")
    if len(sites):
        tbl = sites.rename(columns={"name": "Field", "client": "Client", "location": "Location", "budget": "Budget",
                                    "status": "Status"}).drop(columns="id")
        tbl["Budget"] = tbl["Budget"].fillna(0).astype(int)
        ed = wide(st.data_editor, tbl, hide_index=True, key=f"{K('st')}_{len(sites)}", disabled=["Field"], column_config={
            "Status": st.column_config.SelectboxColumn("Status", options=["Active", "Completed", "On hold"], required=True),
            "Budget": st.column_config.NumberColumn("Budget", min_value=0, step=100000)})
        if st.button("Save changes to fields"):
            with conn() as c:
                for i, cl, lo, bu, stt in zip(sites.id, ed["Client"], ed["Location"], ed["Budget"], ed["Status"]):
                    c.execute("UPDATE sites SET client=?, location=?, budget=?, status=? WHERE id=?",
                              (cl or "", lo or "", int(0 if pd.isna(bu) else bu), stt, int(i)))
            done("Fields saved.")


def setup_categories():
    st.subheader("Categories")
    cats = categories_df()
    q = search_box("q_cats", "Search category", "Type part of a name…")
    wide(st.dataframe, match(cats[["name"]].rename(columns={"name": "Category"}), ["Category"], q), hide_index=True)
    c1, c2 = st.columns([3, 1])
    newcat = c1.text_input("New category", key=K("nc"))
    c2.write("")
    if c2.button("Add category"):
        if not newcat.strip():
            st.warning("Write the category name.")
        else:
            try:
                run("INSERT INTO categories(name) VALUES(?)", (newcat.strip(),))
                done(f"Category {newcat.strip()} added.")
            except IntegrityErrors:
                st.error("That category already exists.")


# ---------- import the cost workbook (one sheet per category) ----------
def _num(v):
    try:
        if v is None or isinstance(v, bool):
            return None
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def parse_cost_workbook(file):
    """Reads a workbook like the KDBS one: one sheet per cost category, plus a SUMMARY sheet with the funds."""
    from openpyxl import load_workbook
    wb = load_workbook(file, read_only=True, data_only=True)
    rows, checks, skipped, funds = [], [], 0, None
    for name in wb.sheetnames:
        key, data = norm(name), list(wb[name].iter_rows(values_only=True))
        if key == "summary":
            for r in data:
                text = " ".join(str(c) for c in r if isinstance(c, str))
                if norm(text).startswith("recordedmoneypaid"):
                    nums = [_num(c) for c in r if _num(c)]
                    funds = int(nums[0]) if nums else funds
            continue
        if key in ("dashboard", "pivots", "dashcalc"):
            continue
        hdr = None
        for i, r in enumerate(data[:20]):
            cells = [norm(c) for c in r]
            col = {k: next((j for j, c in enumerate(cells) if c in al), None) for k, al in ALIASES.items()}
            if col["item"] is not None and col["date"] is not None and col["total"] is not None:
                hdr = (i, col)
                break
        if hdr is None:
            continue
        i0, col = hdr
        cat, sheet_total, got, amount = SHEET_TO_CATEGORY.get(key, name.strip()), None, 0, 0
        for r in data[i0 + 1:]:
            cell = lambda j: r[j] if j is not None and j < len(r) else None
            if any(isinstance(c, str) and "grandtotal" in norm(c) for c in r):
                sheet_total = _num(cell(col["total"]))
                if sheet_total is None:
                    nums = [x for x in (_num(c) for c in r) if x]
                    sheet_total = nums[-1] if nums else None
                break
            item, tot, qty, price, d = cell(col["item"]), _num(cell(col["total"])), _num(cell(col["qty"])), _num(cell(col["price"])), cell(col["date"])
            if not isinstance(item, str) or not item.strip():
                continue
            if tot is None and qty is not None and price is not None:
                tot = qty * price
            if not tot:
                continue
            if isinstance(d, datetime):
                d = d.date()
            if not isinstance(d, date):
                skipped += 1
                continue
            qty = qty if qty else 1.0
            rows.append({"Category": cat, "Item": item.strip(), "Qty": qty, "Unit price": price if price else tot / qty,
                         "Total": int(round(tot)), "Date": d})
            got += 1
            amount += int(round(tot))
        checks.append({"Category": cat, "Rows": got, "Imported": amount, "Sheet total": int(sheet_total or 0)})
    return pd.DataFrame(rows), pd.DataFrame(checks), skipped, funds


def import_costs(site_id, rows, user, fname, funds=None, funds_date=None):
    now = datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        have = {r["name"]: r["id"] for r in c.execute("SELECT id, name FROM categories").fetchall()}
        for n in sorted(set(rows.Category) - set(have)):
            have[n] = c.execute("INSERT INTO categories(name) VALUES(?) RETURNING id", (n,)).fetchone()["id"]
        bulk_insert(c, "expenses", ["site_id", "category_id", "item", "qty", "unit_price", "total", "spent_on",
                                    "entered_by", "entered_at", "source"],
                    [(site_id, have[r["Category"]], r["Item"], float(r["Qty"]), float(r["Unit price"]), int(r["Total"]),
                      str(r["Date"]), user, now, f"import:{fname}") for r in rows.to_dict("records")])
        if funds:
            c.execute("INSERT INTO funds(site_id, amount, received_on, source, note, entered_by) VALUES(?,?,?,?,?,?)",
                      (site_id, int(funds), str(funds_date), "Imported", f"From the SUMMARY sheet of {fname}", user))


def page_import():
    role = st.session_state.role
    if role not in (CEO, ACCOUNTANT):
        return st.error("Only the CEO and the Accountant can import workbooks.")
    st.write("Upload your cost workbook (one sheet per cost category, with a SUMMARY sheet). Every row is checked "
             "before anything is saved.")
    up = st.file_uploader("Excel workbook (.xlsx)", type=["xlsx"], key=K("imp"))
    if up is None:
        return
    try:
        rows, checks, skipped, funds = parse_cost_workbook(up)
    except Exception as e:
        return st.error(f"Could not read the workbook: {e}")
    if rows.empty:
        return st.error("No cost rows were found. Each category sheet needs the columns: item name, quantity, price, total, date.")
    checks["Check"] = ["OK" if abs(a - b) < 1 else "Differs" for a, b in zip(checks.Imported, checks["Sheet total"])]
    st.write(f"**{len(rows)} rows found in {len(checks)} category sheets, total {money(rows.Total.sum())} Frw.**")
    wide(st.dataframe, pretty(checks, ["Imported", "Sheet total"]), hide_index=True)
    if skipped:
        st.warning(f"{skipped} row(s) have no valid date and will not be imported.")
    if (checks.Check == "Differs").any():
        st.warning("Some sheets differ from their own grand total (rows without a date or an amount). Check them in Excel.")
    sites = visible_sites()
    site_ids = dict(zip(sites.name, sites.id.astype(int)))
    options = (["New field…"] if role == CEO else []) + list(site_ids)
    if not options:
        return no_field_message()
    target = st.selectbox("Import into which field?", options, key=K("it"))
    new_name = new_client = ""
    if target == "New field…":
        n1, n2 = st.columns(2)
        new_name = n1.text_input("New field name", key=K("inn"))
        new_client = n2.text_input("Client", key=K("inc"))
    add_funds = False
    if funds and role in FUNDS_VISIBLE:
        add_funds = st.checkbox(f"Also record the funds received found in the SUMMARY sheet ({money(funds)} Frw)", value=True, key=K("ifd"))
    again = True
    if target != "New field…":
        n_old = int(df("SELECT COUNT(*) AS n FROM expenses WHERE site_id=? AND source LIKE ?", (site_ids[target], "import:%"))["n"][0])
        if n_old:
            st.warning(f"{target} already has {n_old} imported entries. Importing again would add the same entries a second time.")
            again = st.checkbox("I understand, import again", key=K("iag"))
    if st.button("Import", type="primary"):
        if target == "New field…" and not new_name.strip():
            st.warning("Write the name of the new field.")
        elif not again:
            st.warning("Tick the box to confirm.")
        else:
            try:
                sid = add_site(new_name, new_client, "", 0, rows.Date.min()) if target == "New field…" else site_ids[target]
            except IntegrityErrors:
                return st.error("A field with that name already exists. Choose it from the list.")
            import_costs(sid, rows, st.session_state.user, up.name, funds if add_funds else None, rows.Date.max())
            done(f"Imported {len(rows)} entries into {new_name.strip() if target == 'New field…' else target}.")


# ---------- reports ----------
def page_reports():
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    ids = [int(i) for i in sites.id]
    exp = load_expenses(ids)
    field, cat, scope, sel = filter_bar(sites, exp, "r")
    if sel.empty:
        return st.info("No spending in this selection.")
    by_cat = sel.groupby("Category").agg(Amount=("Total", "sum"), Transactions=("Total", "size")).reset_index().sort_values("Amount", ascending=False)
    by_cat["Share %"] = (by_cat.Amount / by_cat.Amount.sum() * 100).round(1)
    by_month = sel.assign(Month=sel.Date.str[:7]).groupby("Month").Total.sum().reset_index().rename(columns={"Total": "Amount"})
    st.write(f"**{len(sel)} transactions, total {money(sel.Total.sum())} Frw**")
    wide(st.dataframe, pretty(by_cat, ["Amount"]), hide_index=True)
    sheets = {"By category": by_cat, "By month": by_month,
              "Transactions": sel[["Date", "Field", "Category", "Item", "Qty", "Unit price", "Total", "Entered by"]]}
    if st.session_state.role in FUNDS_VISIBLE:  # funds received are for the CEO only
        fnd = load_funds(ids)
        per = sites.rename(columns={"name": "Field"})[["id", "Field"]].copy()
        per["Funds received"] = per.id.map(fnd.groupby("site_id").Amount.sum()).fillna(0).astype(int)
        per["Spent"] = per.id.map(exp.groupby("site_id").Total.sum()).fillna(0).astype(int)
        per["Balance"] = per["Funds received"] - per["Spent"]
        sheets["Funds and balance"] = (per if field == "All fields" else per[per.Field == field]).drop(columns="id")
    st.download_button(":material/download: Download this report (Excel)", to_excel(sheets),
                       f"report_{norm(field) or 'all'}_{date.today()}.xlsx", mime=XLSX, key="dl_rep")


# ---------- users (CEO) ----------
def set_user_sites(uid, site_ids):
    with conn() as c:
        c.execute("DELETE FROM user_sites WHERE user_id=?", (uid,))
        if site_ids:
            bulk_insert(c, "user_sites", ["user_id", "site_id"], [(uid, s) for s in site_ids])


def page_users():
    if st.session_state.role != CEO:
        return st.error("Only the CEO can manage users.")
    me = st.session_state.uid
    sites = df("SELECT id, name FROM sites ORDER BY name")
    site_ids = dict(zip(sites.name, sites.id.astype(int)))
    users = df("SELECT id, username, role, email FROM users ORDER BY role, username")
    links = df("SELECT u.user_id, s.name FROM user_sites u JOIN sites s ON s.id=u.site_id")
    mine = {int(k): sorted(v.tolist()) for k, v in links.groupby("user_id")["name"]} if len(links) else {}
    show = users.assign(Fields=[("All fields" if r in SEE_ALL else ", ".join(mine.get(int(i), [])))
                                for i, r in zip(users.id, users.role)])
    q = search_box("q_users", "Search user", "Type part of a name…")
    wide(st.dataframe, match(show.drop(columns="id").rename(columns={"username": "User name", "role": "Role", "email": "Email"}),
                             ["User name", "Role", "Email", "Fields"], q), hide_index=True)
    with st.expander("Add a user"):
        a1, a2 = st.columns(2)
        nu = a1.text_input("User name", key=K("nu"))
        nr = a2.selectbox("Role", ROLES, index=3, key=K("nr"))
        ne = a1.text_input("Email (optional, used to reset a forgotten password)", key=K("ne"))
        npw = a2.text_input("Temporary password (at least 8 characters)", type="password", key=K("np"))
        ns = st.multiselect("Fields this user works on", list(site_ids), key=K("ns")) if nr != CEO else []
        if st.button("Add user"):
            err = add_user(nu, nr, ne, npw)
            if err:
                st.error(err)
            else:
                set_user_sites(int(df("SELECT id FROM users WHERE lower(username)=lower(?)", (nu.strip(),))["id"][0]),
                               [site_ids[n] for n in ns])
                done(f"User {nu.strip()} added.")
    with st.expander("Change fields, change a password or delete a user"):
        labels = {f"{r.username} ({r.role})": (int(r.id), r.role, r.username) for r in users.itertuples()}
        who = st.selectbox("User", list(labels), index=None, placeholder="Choose a user", key=K("wu"))
        if who:
            uid, role, uname = labels[who]
            if role != CEO:
                cur = st.multiselect("Fields this user works on", list(site_ids), default=mine.get(uid, []), key=K(f"wf{uid}"))
                if st.button("Save fields"):
                    set_user_sites(uid, [site_ids[n] for n in cur])
                    done(f"Fields saved for {uname}.")
            pw = st.text_input("New password (at least 8 characters)", type="password", key=K("wp"))
            sure = st.checkbox("I am sure I want to delete this user", key=K("ws"))
            w1, w2 = st.columns(2)
            if w1.button("Set new password"):
                err = set_user_password(uid, pw)
                if err:
                    st.error(err)
                else:
                    done(f"Password changed for {uname}.")
            if w2.button("Delete this user"):
                err = delete_user(uid, me) if sure else "Tick the box to confirm."
                if err:
                    st.error(err)
                else:
                    run("DELETE FROM user_sites WHERE user_id=?", (uid,))
                    done(f"{uname} deleted.")


# ---------- main ----------
PAGES = {
    CEO: [("dashboard", "Dashboard", "dashboard"), ("stock", "Material stock", "inventory_2"),
          ("expenses", "Expenses", "receipt_long"), ("funds", "Funds received", "payments"),
          ("import", "Import from Excel", "upload_file"), ("setup", "Fields & categories", "construction"),
          ("reports", "Reports", "assessment"), ("users", "Users", "group")],
    ACCOUNTANT: [("dashboard", "Dashboard", "dashboard"), ("expenses", "Expenses", "receipt_long"),
                 ("import", "Import from Excel", "upload_file"), ("stock", "Material stock", "inventory_2"),
                 ("setup", "Categories", "construction"), ("reports", "Reports", "assessment")],
    FIELD: [("dashboard", "Dashboard", "dashboard"), ("expenses", "Expenses", "receipt_long"),
            ("stock", "Material stock", "inventory_2"), ("reports", "Reports", "assessment")],
    STORE: [("stock", "Dashboard", "dashboard"), ("receive", "Materials received", "move_to_inbox"),
            ("use", "Materials used", "outbox"), ("count", "Stock count", "fact_check")],
}
RENDER = {"dashboard": page_dashboard, "expenses": page_expenses, "funds": page_funds, "import": page_import,
          "setup": page_setup, "reports": page_reports, "users": page_users, "stock": page_stock,
          "receive": page_receive, "use": page_use, "count": page_count}


def main():
    init_db()
    if "user" not in st.session_state:
        login()
        return
    me = get_user(st.session_state.uid)
    if me is None:  # the account was deleted
        st.session_state.clear()
        st.rerun()
    if not me["email"] and not st.session_state.get("skip_email"):
        email_prompt()
        return
    st.markdown(SIDEBAR_CSS, unsafe_allow_html=True)
    with st.sidebar:
        if LOGO_URI:
            st.image(LOGO, width=150)
        st.markdown("### Inchars PD Group")
        st.caption("Construction Management")
        st.divider()
    sec, title = side_menu(PAGES[st.session_state.role])
    header(title)
    show_flash()
    RENDER[sec]()
    with st.sidebar:
        user_box()


if __name__ == "__main__":
    main()
