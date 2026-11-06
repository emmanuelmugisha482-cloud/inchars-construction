import base64
import calendar
import difflib
import hashlib
import html
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
ALL = tuple(ROLES)
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

MONEY_CATEGORIES = ["Fuel", "Food", "Transport", "Materials", "Tools & equipment", "Wages", "Airtime & communication",
                    "Accommodation", "Tax", "Debt payment", "Other"]
TAX_TYPES = ["VAT", "PAYE", "Corporate income tax", "Pension / RSSB", "Withholding tax", "Local taxes and fees", "Other"]

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
            if not IS_PG:
                st.error("The online database is not connected: DATABASE_URL was not found in the secrets. "
                         "Do not enter any data yet.")
            if not all(first_manager()[:2]):
                st.warning("No accounts exist yet, and the [first_manager] section (with username and password) "
                           "was not found in the secrets. Add it, save, then reboot the app.")
            else:
                st.warning("No accounts exist yet. Reboot the app and try again.")
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
                    roles = load_roles(user["id"]) or [user["role"]]
                    st.session_state.update(user=user["username"], role=", ".join(roles), roles=roles, uid=user["id"])
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
[data-testid="stMetricValue"] {font-size:1.45rem !important;}
[data-testid="stMetricValue"] > div {white-space:normal !important; overflow:visible !important; text-overflow:clip !important; line-height:1.2;}
[data-testid="stMetricLabel"] p {font-size:.85rem !important;}
.bars {margin:.3rem 0 1rem 0;}
.bars .brow {display:flex; align-items:center; gap:.6rem; margin:.28rem 0;}
.bars .blabel {width:30%; font-size:.85rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
.bars .btrack {flex:1; background:#e4efe8; border-radius:6px; height:16px;}
.bars .bfill {height:16px; border-radius:6px; background:#0b6a35;}
.bars .bfill.b2 {background:#e0922f;}
.bars .bval {width:22%; text-align:right; font-size:.85rem;}
.bars .legend {font-size:.8rem; color:#4b6a57; margin-bottom:.3rem;}
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
    f"""CREATE TABLE IF NOT EXISTS material_requests(
        id {PK}, batch INTEGER, site_id INTEGER, material_id INTEGER, qty_requested DOUBLE PRECISION,
        qty_approved DOUBLE PRECISION, qty_delivered DOUBLE PRECISION, status TEXT DEFAULT 'Pending', needed_by TEXT,
        requested_on TEXT, requested_by TEXT, decided_on TEXT, decided_by TEXT, note TEXT, reason TEXT, delivered_on TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_mr_site ON material_requests(site_id)",
    "CREATE TABLE IF NOT EXISTS user_roles(user_id INTEGER, role TEXT, PRIMARY KEY(user_id, role))",
    f"CREATE TABLE IF NOT EXISTS money_categories(id {PK}, name TEXT UNIQUE NOT NULL)",
    f"""CREATE TABLE IF NOT EXISTS money_requests(
        id {PK}, site_id INTEGER, category TEXT, purpose TEXT, amount BIGINT, amount_approved BIGINT, needed_by TEXT,
        status TEXT DEFAULT 'Pending', requested_on TEXT, requested_by TEXT, decided_on TEXT, decided_by TEXT,
        reason TEXT, paid_on TEXT, paid_by TEXT)""",
    f"""CREATE TABLE IF NOT EXISTS money_ledger(
        id {PK}, spent_on TEXT, person TEXT, site_id INTEGER, kind TEXT, category TEXT, amount BIGINT, note TEXT,
        request_id INTEGER, entered_by TEXT, entered_at TEXT)""",
    f"""CREATE TABLE IF NOT EXISTS taxes(
        id {PK}, tax_type TEXT, period TEXT, amount BIGINT, paid_on TEXT, reference TEXT, site_id INTEGER, note TEXT,
        entered_by TEXT)""",
    f"""CREATE TABLE IF NOT EXISTS debts(
        id {PK}, party TEXT, direction TEXT, amount BIGINT, due_on TEXT, site_id INTEGER, note TEXT, entered_by TEXT,
        entered_on TEXT)""",
    f"CREATE TABLE IF NOT EXISTS debt_payments(id {PK}, debt_id INTEGER, amount BIGINT, paid_on TEXT, note TEXT, entered_by TEXT)",
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
        if c.execute("SELECT COUNT(*) AS n FROM money_categories").fetchone()["n"] == 0:
            bulk_insert(c, "money_categories", ["name"], [(n,) for n in MONEY_CATEGORIES])
        # users made before several roles existed keep their one role
        c.execute("INSERT INTO user_roles(user_id, role) SELECT id, role FROM users WHERE id NOT IN (SELECT user_id FROM user_roles)")


def seed_first_manager():
    """Creates the CEO account from the secrets while there are no users. It is checked at every visit to the
    login page, so adding the secrets after the first start also works."""
    u, p, e = first_manager()
    if not (u and p):
        return
    with conn() as c:
        if c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0:
            uid = c.execute("INSERT INTO users(username, password_hash, role, email) VALUES(?,?,?,?) RETURNING id",
                            (u.strip(), hash_pw(p), CEO, (e or "").strip().lower() or None)).fetchone()["id"]
            c.execute("INSERT INTO user_roles(user_id, role) VALUES(?,?)", (uid, CEO))


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


# ---------- several roles per user ----------
def load_roles(uid):
    return df("SELECT role FROM user_roles WHERE user_id=? ORDER BY role", (uid,))["role"].tolist()


def roles_now():
    return set(st.session_state.get("roles", []))


def has(*rs):
    """True when the signed-in user has at least one of these roles."""
    return bool(roles_now() & set(rs))


def set_user_roles(uid, roles):
    with conn() as c:
        c.execute("DELETE FROM user_roles WHERE user_id=?", (uid,))
        bulk_insert(c, "user_roles", ["user_id", "role"], [(uid, r) for r in roles])
        c.execute("UPDATE users SET role=? WHERE id=?", (roles[0], uid))


def add_user_roles(username, roles, email, password):
    err = add_user(username, roles[0], email, password)
    if err:
        return err
    set_user_roles(int(df("SELECT id FROM users WHERE lower(username)=lower(?)", (username.strip(),))["id"][0]), roles)
    return None


def delete_user_full(uid, me):
    if uid == me:
        return "You cannot delete your own account."
    with conn() as c:
        ceos = [r["user_id"] for r in c.execute("SELECT user_id FROM user_roles WHERE role=?", (CEO,)).fetchall()]
        if uid in ceos and len(ceos) <= 1:
            return "You cannot delete the last CEO."
        for t in ("user_roles", "user_sites", "reset_codes"):
            c.execute(f"DELETE FROM {t} WHERE user_id=?", (uid,))
        c.execute("DELETE FROM users WHERE id=?", (uid,))
    return None


def ensure_named(c, table, name, extra=None):
    """Id of the row called `name` in categories / money_categories / materials (case ignored). Creates it when new."""
    row = c.execute(f"SELECT id FROM {table} WHERE lower(name)=lower(?)", (name.strip(),)).fetchone()
    if row:
        return row["id"]
    if table == "materials":
        return c.execute("INSERT INTO materials(name, unit) VALUES(?,?) RETURNING id", (name.strip(), (extra or "").strip())).fetchone()["id"]
    return c.execute(f"INSERT INTO {table}(name) VALUES(?) RETURNING id", (name.strip(),)).fetchone()["id"]


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
              "Out of stock": red, "In stock": green, "Rejected": red, "Delivered": green,
              "Pending": "background-color:#fff3d6;color:#7a4a00", "Approved": "background-color:#dbeeff;color:#0b3a66",
              "Paid": green, "Settled": green, "Overdue": red, "Open": "background-color:#fff3d6;color:#7a4a00"}
    try:
        fmt = {c: "{:,.0f}" for c in money_cols if c in frame.columns}
        fmt.update({c: _q for c in qty_cols if c in frame.columns})
        sty = frame.style.format(fmt)
        if status_col in frame.columns:
            sty = (getattr(sty, "map", None) or sty.applymap)(lambda v: colors.get(v, ""), subset=[status_col])
        return sty
    except Exception:
        return frame


def bars(frame, label, value, fmt=None, color=""):
    """Horizontal bars drawn with plain HTML, biggest first. No chart add-on is needed, so it always loads."""
    if frame is None or frame.empty:
        return
    fmt = fmt or money
    top = max(float(frame[value].abs().max()), 1e-9)
    rows = "".join(
        f'<div class="brow"><div class="blabel" title="{html.escape(str(l))}">{html.escape(str(l))}</div>'
        f'<div class="btrack"><div class="bfill {color}" style="width:{max(abs(float(v)) / top * 100, 0.8):.1f}%"></div></div>'
        f'<div class="bval">{fmt(v)}</div></div>' for l, v in zip(frame[label], frame[value]))
    st.markdown(f'<div class="bars">{rows}</div>', unsafe_allow_html=True)


def hbar(frame, cat, val, title):
    bars(frame, cat, val)


def compare_bars(frame, label, a, b):
    """Two bars per row (for example funds received and spent)."""
    if frame.empty:
        return
    top = max(float(frame[[a, b]].abs().max().max()), 1e-9)
    rows = ""
    for l, x, y in zip(frame[label], frame[a], frame[b]):
        rows += (f'<div class="brow"><div class="blabel">{html.escape(str(l))}</div><div style="flex:1">'
                 f'<div class="btrack"><div class="bfill" style="width:{max(abs(float(x)) / top * 100, 0.8):.1f}%"></div></div>'
                 f'<div class="btrack" style="margin-top:3px"><div class="bfill b2" style="width:{max(abs(float(y)) / top * 100, 0.8):.1f}%"></div></div>'
                 f'</div><div class="bval">{money(x)}<br>{money(y)}</div></div>')
    st.markdown(f'<div class="bars"><div class="legend">Green: {html.escape(a)} &nbsp; Orange: {html.escape(b)}</div>{rows}</div>',
                unsafe_allow_html=True)


def visible_sites():
    """CEO and Accountant see every field. Store and Field managers only the fields given to them."""
    if has(*SEE_ALL):
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
    if len(sites) > 1 or has(CEO):
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
    if has(*SEE_ALL):
        st.info("There is no field yet. Create the first one in Fields & categories.")
    else:
        st.info("No field is linked to your account yet. Please ask the CEO to give you access to a field.")


# ---------- dashboard ----------
def page_dashboard():
    can_funds = has(*FUNDS_VISIBLE)
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
    for i in range(0, len(items), 3):  # three numbers per row so that every number can be read in full
        for col, (label, value) in zip(st.columns(3), items[i:i + 3]):
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

    if has(CEO):  # progress of the materials on every field
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
        bars(sel.assign(Month=sel.Date.str[:7]).groupby("Month", as_index=False).Total.sum().sort_values("Month", ascending=False).head(12),
             "Month", "Total")


# ---------- expenses ----------
def parse_entries(ed, cat_ids):
    ok, bad = [], []
    for pos, (_, r) in enumerate(ed.iterrows(), start=1):
        item = str(r["Item"]).strip() if pd.notna(r["Item"]) else ""
        cat = r["Category"] if pd.notna(r["Category"]) else None
        newcat = str(r["New category"]).strip() if pd.notna(r["New category"]) else ""
        qty, price = pd.to_numeric(r["Qty"], errors="coerce"), pd.to_numeric(r["Unit price"], errors="coerce")
        if not item and not cat and not newcat and (pd.isna(price) or price == 0):
            continue  # empty row
        d = pd.to_datetime(r["Date"], errors="coerce")
        d = date.today() if pd.isna(d) else d.date()
        if not item:
            bad.append(f"Row {pos}: write the item name.")
        elif not newcat and cat not in cat_ids:
            bad.append(f"Row {pos}: choose a category, or type a new one.")
        elif pd.isna(qty) or qty <= 0:
            bad.append(f"Row {pos}: the quantity must be above 0.")
        elif pd.isna(price) or price <= 0:
            bad.append(f"Row {pos}: enter the unit price.")
        elif d > date.today():
            bad.append(f"Row {pos}: the date is in the future.")
        else:
            ok.append({"date": d, "category": cat, "new_category": newcat, "item": item, "qty": float(qty),
                       "price": float(price), "total": int(round(float(qty) * float(price)))})
    return ok, bad


def add_expenses(site_id, cat_ids, entries, user):
    now = datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        rows = []
        for e in entries:
            cid = ensure_named(c, "categories", e["new_category"]) if e["new_category"] else cat_ids[e["category"]]
            rows.append((site_id, cid, e["item"], e["qty"], e["price"], e["total"], str(e["date"]), user, now, "entry"))
        bulk_insert(c, "expenses", ["site_id", "category_id", "item", "qty", "unit_price", "total", "spent_on",
                                    "entered_by", "entered_at", "source"], rows)


def delete_expenses(ids, user, uid, is_accountant):
    """An Accountant can delete any entry of their fields. Others only their own entries of today."""
    n = 0
    with conn() as c:
        for rid in ids:
            if is_accountant:
                n += c.execute("DELETE FROM expenses WHERE id=? AND site_id IN (SELECT site_id FROM user_sites WHERE user_id=?)",
                               (rid, uid)).rowcount
            else:
                n += c.execute("""DELETE FROM expenses WHERE id=? AND entered_by=? AND SUBSTR(entered_at,1,10)=?
                                  AND site_id IN (SELECT site_id FROM user_sites WHERE user_id=?)""",
                               (rid, user, str(date.today()), uid)).rowcount
    return n


def page_expenses():
    user, uid = st.session_state.user, st.session_state.uid
    can_add, is_acc = has(ACCOUNTANT, FIELD), has(ACCOUNTANT)
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    site_ids = {r.name: int(r.id) for r in sites.itertuples()}
    cats = categories_df()
    cat_ids = dict(zip(cats.name, cats.id.astype(int)))
    if can_add:
        st.subheader("Add expenses")
        st.caption("One line for each purchase or payment. Choose the category from the list, or type a new category in "
                   "the New category column. Add more lines with the + at the bottom of the table.")
        a1, _ = st.columns([2, 3])
        if len(site_ids) > 1:
            field = a1.selectbox("Field", list(site_ids), key=K("ef"))
        else:
            field = list(site_ids)[0]
            a1.markdown(f"**Field:** {field}")
        grid = pd.DataFrame({"Date": [date.today()] * 5, "Category": [None] * 5, "New category": [""] * 5,
                             "Item": [""] * 5, "Qty": [1.0] * 5, "Unit price": [0.0] * 5})
        ed = wide(st.data_editor, grid, num_rows="dynamic", hide_index=True, key=K("eg"), column_config={
            "Date": st.column_config.DateColumn("Date", max_value=date.today(), format="YYYY-MM-DD"),
            "Category": st.column_config.SelectboxColumn("Category", options=list(cat_ids), required=False),
            "New category": st.column_config.TextColumn("New category"),
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
    if not can_add:
        wide(st.dataframe, pretty(shown[cols], ["Unit price", "Total"]), hide_index=True)
        return
    tbl = shown[cols].copy()
    tbl["Remove"] = False
    ed2 = wide(st.data_editor, tbl, hide_index=True, key=f"{K('el')}_{len(shown)}", disabled=cols)
    st.caption("Tick Remove, then press Delete. " + ("You can delete any entry of your fields." if is_acc
                                                       else "You can delete your own entries from today."))
    if st.button("Delete ticked entries"):
        ids = [int(i) for i, r in zip(shown.id, ed2["Remove"]) if r]
        if not ids:
            st.info("Tick the entries you want to delete.")
        else:
            n = delete_expenses(ids, user, uid, is_acc)
            done(f"{n} entr{'y' if n == 1 else 'ies'} deleted." + (f" {len(ids) - n} could not be deleted." if n < len(ids) else ""))


# ---------- funds received (CEO only) ----------
def page_funds():
    if not has(*FUNDS_VISIBLE):
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


def parse_moves(ed, mat_ids, allow_new=False):
    ok, bad = [], []
    for pos, (_, r) in enumerate(ed.iterrows(), start=1):
        mat = r["Material"] if pd.notna(r["Material"]) else None
        new = str(r.get("New material", "") or "").strip() if pd.notna(r.get("New material", "")) else ""
        unit = str(r.get("Unit", "") or "").strip() if pd.notna(r.get("Unit", "")) else ""
        qty = pd.to_numeric(r["Qty"], errors="coerce")
        detail = str(r["Detail"]).strip() if pd.notna(r["Detail"]) else ""
        if not mat and not new and (pd.isna(qty) or qty == 0) and not detail:
            continue  # empty row
        d = pd.to_datetime(r["Date"], errors="coerce")
        d = date.today() if pd.isna(d) else d.date()
        if new and not allow_new:
            bad.append(f"Row {pos}: choose the material from the list.")
        elif new and not unit:
            bad.append(f"Row {pos}: write the unit of the new material (bags, pcs, kg, m3…).")
        elif not new and mat not in mat_ids:
            bad.append(f"Row {pos}: choose a material, or type a new one.")
        elif pd.isna(qty) or qty <= 0:
            bad.append(f"Row {pos}: the quantity must be above 0.")
        elif d > date.today():
            bad.append(f"Row {pos}: the date is in the future.")
        else:
            ok.append({"date": d, "material": mat, "new": (new, unit) if new else None, "qty": float(qty), "detail": detail})
    return ok, bad


def add_moves(site_id, mat_ids, kind, entries, user):
    """Received adds to the stock and Used takes away. New materials are created on the way.
    Returns an error text, or None when saved."""
    sign, now = (1 if kind == "Received" else -1), datetime.now().isoformat(timespec="seconds")
    try:
        with conn() as c:
            ids = [ensure_named(c, "materials", e["new"][0], e["new"][1]) if e["new"] else mat_ids[e["material"]] for e in entries]
            if kind == "Used":  # never use more than is in stock
                need = {}
                for mid, e in zip(ids, entries):
                    need[mid] = need.get(mid, 0) + e["qty"]
                for mid, q in need.items():
                    have = float(c.execute("SELECT COALESCE(SUM(qty), 0) AS b FROM material_moves WHERE site_id=? AND material_id=?",
                                           (site_id, mid)).fetchone()["b"])
                    if q > have + 1e-9:
                        name = c.execute("SELECT name FROM materials WHERE id=?", (mid,)).fetchone()["name"]
                        raise Abort(f"{name}: only {have:g} in stock.")
            bulk_insert(c, "material_moves", ["site_id", "material_id", "kind", "qty", "moved_on", "detail", "entered_by", "entered_at"],
                        [(site_id, mid, kind, sign * e["qty"], str(e["date"]), e["detail"], user, now) for mid, e in zip(ids, entries)])
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
    cfg = {"Date": st.column_config.DateColumn("Date", max_value=date.today(), format="YYYY-MM-DD"),
           "Material": st.column_config.SelectboxColumn("Material", options=labels or ["(list is empty)"], required=False),
           "Qty": st.column_config.NumberColumn("Quantity", min_value=0.0, step=0.01),
           "Detail": st.column_config.TextColumn("Supplier / delivery note" if kind == "Received" else "Used for (work or activity)")}
    if kind == "Received":
        cfg["New material"] = st.column_config.TextColumn("New material (if not in the list)")
        cfg["Unit"] = st.column_config.TextColumn("Unit (new material)")
    return cfg


def recent_moves(site_id, kind, key):
    st.subheader("Recent entries")
    q = search_box(key, "Search material or note", "Type part of a name…")
    mv = load_moves([site_id])
    mv = match(mv[mv.Type == kind], ["Material", "Detail", "Entered by"], q).head(100)
    wide(st.dataframe, pretty(mv.assign(Qty=mv.Qty.abs())[["Date", "Material", "Unit", "Qty", "Detail", "Entered by"]],
                              qty_cols=["Qty"]), hide_index=True)


def page_receive():
    if not has(STORE):
        return st.error("This page is for the Store manager.")
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    field = pick_field(sites, K("rf"))
    sid = int(sites[sites.name == field].id.iloc[0])
    mats = load_materials()
    labels = {f"{r.name} ({r.unit})": int(r.id) for r in mats.itertuples()}
    st.subheader("Materials received")
    st.caption("One line for each delivery. Choose the material from the list. If it is not in the list yet, type its "
               "name and unit in the last columns and it is added for everybody. Add more lines with the + at the bottom.")
    grid = pd.DataFrame({"Date": [date.today()] * 5, "Material": [None] * 5, "Qty": [0.0] * 5, "New material": [""] * 5,
                         "Unit": [""] * 5, "Detail": [""] * 5})
    ed = wide(st.data_editor, grid, num_rows="dynamic", hide_index=True, key=K("rg"), column_config=move_columns("Received", list(labels)))
    ok, bad = parse_moves(ed, labels, allow_new=True)
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
    if not has(STORE):
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
    if not has(STORE):
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


# ---------- material requests ----------
# Everybody can ask. The Accountant or the CEO approves (whoever acts first), nobody approves their own request
# (the CEO may), and the Store manager records the delivery, which adds the quantity to the stock.
REQ_COLS = ["id", "batch", "site_id", "material_id", "Date", "Field", "Material", "Unit", "Requested", "Approved",
            "Delivered", "Status", "Needed by", "Requested by", "Decided by", "Note", "Reason"]


def load_mat_requests(site_ids):
    if not site_ids:
        return pd.DataFrame(columns=REQ_COLS)
    return df(f"""SELECT r.id, r.batch, r.site_id, r.material_id, r.requested_on AS "Date", s.name AS "Field",
                  t.name AS "Material", t.unit AS "Unit", r.qty_requested AS "Requested", r.qty_approved AS "Approved",
                  r.qty_delivered AS "Delivered", r.status AS "Status", r.needed_by AS "Needed by",
                  r.requested_by AS "Requested by", r.decided_by AS "Decided by", r.note AS "Note", r.reason AS "Reason"
                  FROM material_requests r JOIN sites s ON s.id=r.site_id JOIN materials t ON t.id=r.material_id
                  WHERE r.site_id IN ({','.join('?' * len(site_ids))}) ORDER BY r.batch DESC, t.name""", tuple(site_ids))


def parse_req(ed, labels):
    ok, bad = [], []
    for pos, (_, r) in enumerate(ed.iterrows(), start=1):
        mat = r["Material"] if pd.notna(r["Material"]) else None
        new = str(r["New material"]).strip() if pd.notna(r["New material"]) else ""
        unit = str(r["Unit"]).strip() if pd.notna(r["Unit"]) else ""
        qty = pd.to_numeric(r["Qty"], errors="coerce")
        note = str(r["Note"]).strip() if pd.notna(r["Note"]) else ""
        if not mat and not new and (pd.isna(qty) or qty == 0) and not note:
            continue  # empty row
        if new and not unit:
            bad.append(f"Row {pos}: write the unit of the new material.")
        elif not new and mat not in labels:
            bad.append(f"Row {pos}: choose a material, or type a new one.")
        elif pd.isna(qty) or qty <= 0:
            bad.append(f"Row {pos}: the quantity must be above 0.")
        else:
            ok.append({"material": mat, "new": (new, unit) if new else None, "qty": float(qty), "note": note})
    return ok, bad


def create_mat_request(site_id, labels, items, needed_by, user):
    """All lines go into one request. Its number is the id of its first line."""
    first, today, b = True, str(date.today()), 0
    with conn() as c:
        for e in items:
            mid = ensure_named(c, "materials", e["new"][0], e["new"][1]) if e["new"] else labels[e["material"]]
            vals = (site_id, mid, e["qty"], str(needed_by), today, user, e["note"])
            if first:
                b = c.execute("INSERT INTO material_requests(batch, site_id, material_id, qty_requested, needed_by, requested_on, "
                              "requested_by, note) VALUES(0,?,?,?,?,?,?,?) RETURNING id", vals).fetchone()["id"]
                c.execute("UPDATE material_requests SET batch=? WHERE id=?", (b, b))
                first = False
            else:
                c.execute("INSERT INTO material_requests(batch, site_id, material_id, qty_requested, needed_by, requested_on, "
                          "requested_by, note) VALUES(?,?,?,?,?,?,?,?)", (b,) + vals)
    return b


def update_mat_pending(user, changes, removes):
    """The person who asked can still change or delete their own lines while they are pending."""
    skipped = 0
    with conn() as c:
        for rid in removes:
            skipped += c.execute("DELETE FROM material_requests WHERE id=? AND status='Pending' AND requested_by=?",
                                 (rid, user)).rowcount == 0
        for rid, q in changes.items():
            skipped += c.execute("UPDATE material_requests SET qty_requested=? WHERE id=? AND status='Pending' AND requested_by=?",
                                 (q, rid, user)).rowcount == 0
    return skipped


def decide_mat_requests(decisions, user, is_ceo):
    """Accountant or CEO. decisions = {request id: (quantity to approve, reason)}. Quantity 0 refuses the line.
    Nobody except the CEO can decide their own request."""
    today = str(date.today())
    try:
        with conn() as c:
            for rid, (q, why) in decisions.items():
                row = c.execute("SELECT requested_by FROM material_requests WHERE id=?", (rid,)).fetchone()
                if row is not None and row["requested_by"] == user and not is_ceo:
                    raise Abort("You cannot decide your own request. The CEO does that.")
                if q > 0:
                    n = c.execute("UPDATE material_requests SET status='Approved', qty_approved=?, decided_on=?, decided_by=? "
                                  "WHERE id=? AND status='Pending'", (q, today, user, rid)).rowcount
                else:
                    n = c.execute("UPDATE material_requests SET status='Rejected', qty_approved=0, decided_on=?, decided_by=?, "
                                  "reason=? WHERE id=? AND status='Pending'", (today, user, why or "Not approved", rid)).rowcount
                if n != 1:
                    raise Abort("Some of these requests were already decided by someone else. Please refresh the page.")
    except Abort as e:
        return str(e)
    return None


def deliver_mat_lines(deliveries, user, uid):
    """Store manager. deliveries = {request id: quantity received}. Adds the quantity to the stock of the field."""
    now, n = datetime.now().isoformat(timespec="seconds"), 0
    with conn() as c:
        for rid, qty in deliveries.items():
            row = c.execute("""SELECT site_id, material_id, batch FROM material_requests WHERE id=? AND status='Approved'
                               AND site_id IN (SELECT site_id FROM user_sites WHERE user_id=?)""", (rid, uid)).fetchone()
            if row is None or qty <= 0:
                continue
            c.execute("INSERT INTO material_moves(site_id, material_id, kind, qty, moved_on, detail, entered_by, entered_at) "
                      "VALUES(?,?,?,?,?,?,?,?)", (row["site_id"], row["material_id"], "Received", qty, str(date.today()),
                                                  f"Request #{row['batch']}", user, now))
            c.execute("UPDATE material_requests SET status='Delivered', qty_delivered=?, delivered_on=? WHERE id=?",
                      (qty, str(date.today()), rid))
            n += 1
    return n


def pending_material_requests():
    """How many requests wait for the signed-in Accountant or CEO."""
    sites = visible_sites()
    if sites.empty:
        return 0
    r = load_mat_requests([int(i) for i in sites.id])
    p = r[r.Status == "Pending"]
    if not has(CEO):
        p = p[p["Requested by"] != st.session_state.user]
    return int(p.batch.nunique())


def request_list(reqs, prefix):
    """Search and status filter for a table of requests."""
    c1, c2 = st.columns([2, 3])
    status = c1.radio("Show", ["All", "Pending", "Approved", "Delivered", "Rejected"], horizontal=True, key=f"{prefix}_st")
    with c2:
        q = search_box(f"{prefix}_q", "Search material, field or person", "Type part of a name…")
    view = reqs if status == "All" else reqs[reqs.Status == status]
    view = match(view, ["Material", "Field", "Requested by", "Decided by"], q)
    cols = ["batch", "Date", "Field", "Material", "Unit", "Requested", "Approved", "Delivered", "Status", "Needed by",
            "Requested by", "Decided by", "Reason"]
    wide(st.dataframe, pretty(view[cols].rename(columns={"batch": "Request #"}), status_col="Status",
                              qty_cols=["Requested", "Approved", "Delivered"]), hide_index=True)


def section_approve(reqs, sites, user, is_ceo):
    st.subheader("Waiting for your approval")
    pending = reqs[reqs.Status == "Pending"]
    if not is_ceo:
        st.caption("You and the CEO can both approve. Your own requests are decided by the CEO.")
        pending = pending[pending["Requested by"] != user]
    if pending.empty:
        return st.success("No pending requests.")
    f1, f2 = st.columns(2)
    field = f1.selectbox("Field", ["All fields"] + sites.name.tolist(), key="ap_field") if len(sites) > 1 else "All fields"
    with f2:
        q = search_box("ap_q", "Search material or person", "Type part of a name…")
    shown = match(pending if field == "All fields" else pending[pending.Field == field], ["Material", "Requested by"], q)
    if shown.empty:
        return st.info("No pending request matches your search.")
    bal = stock_balances(load_moves(sorted({int(i) for i in shown.site_id})))
    have = {(int(a), int(b)): float(c) for a, b, c in zip(bal.site_id, bal.material_id, bal.Balance)}
    st.write("Every row is one material. Choose **Approve**, **Reject** or **Decide later**, change the quantity if needed, "
             "then press **Confirm decisions**.")
    tbl = pd.DataFrame({
        "Request #": shown.batch.astype(int).values, "Field": shown.Field.values, "By": shown["Requested by"].values,
        "Material": shown.Material.values, "Unit": shown.Unit.values, "Requested": shown.Requested.values,
        "In stock now": [have.get((int(a), int(b)), 0.0) for a, b in zip(shown.site_id, shown.material_id)],
        "Needed by": shown["Needed by"].values, "Decision": "Approve", "Quantity to approve": shown.Requested.values,
        "Reason if rejected": ""})
    ed = wide(st.data_editor, tbl, hide_index=True, key=f"mdec{K('m')}_{abs(hash(tuple(int(i) for i in shown.id)))}",
              disabled=["Request #", "Field", "By", "Material", "Unit", "Requested", "In stock now", "Needed by"],
              column_config={"Decision": st.column_config.SelectboxColumn(options=["Approve", "Reject", "Decide later"], required=True),
                             "Quantity to approve": st.column_config.NumberColumn(min_value=0.0, step=0.01)})
    if st.button(":material/check: Confirm decisions", type="primary", key="mdec_go"):
        decisions, problems = {}, []
        for rid, mat, dec, qty, why in zip(shown.id, ed["Material"], ed["Decision"], ed["Quantity to approve"], ed["Reason if rejected"]):
            qty = 0.0 if pd.isna(qty) else float(qty)
            if dec == "Approve":
                if qty <= 0:
                    problems.append(mat)
                else:
                    decisions[int(rid)] = (qty, "")
            elif dec == "Reject":
                decisions[int(rid)] = (0.0, why.strip() if isinstance(why, str) and why.strip() else "Not approved")
        if problems:
            st.error("Enter a quantity above 0, or choose Reject, for: " + ", ".join(problems))
        elif not decisions:
            st.warning("Nothing to confirm: every row is set to Decide later.")
        else:
            err = decide_mat_requests(decisions, user, is_ceo)
            if err:
                st.error(err)
            else:
                ok_n = sum(q > 0 for q, _ in decisions.values())
                done(f"{ok_n} approved, {len(decisions) - ok_n} rejected, {len(pending) - len(decisions)} left for later.")


def section_new(sites, user):
    field = pick_field(sites, K("qf"))
    sid = int(sites[sites.name == field].id.iloc[0])
    labels = {f"{r.name} ({r.unit})": int(r.id) for r in load_materials().itertuples()}
    st.subheader("New request")
    st.caption("The request goes to the Accountant and to the CEO. Whoever is available approves it. Choose the material "
               "from the list, or type a new material and its unit in the last two columns.")
    needed = st.date_input("Needed by", date.today() + timedelta(days=7), min_value=date.today(), key=K("qd"))
    grid = pd.DataFrame({"Material": [None] * 4, "Qty": [0.0] * 4, "New material": [""] * 4, "Unit": [""] * 4, "Note": [""] * 4})
    ed = wide(st.data_editor, grid, num_rows="dynamic", hide_index=True, key=K("qg"), column_config={
        "Material": st.column_config.SelectboxColumn("Material", options=list(labels) or ["(list is empty)"], required=False),
        "Qty": st.column_config.NumberColumn("Quantity", min_value=0.0, step=0.01),
        "New material": st.column_config.TextColumn("New material (if not in the list)"),
        "Unit": st.column_config.TextColumn("Unit (new material)"),
        "Note": st.column_config.TextColumn("What it is for (optional)")})
    ok, bad = parse_req(ed, labels)
    if ok:
        st.write(f"**{len(ok)} line(s) ready to send.**")
    for msg in bad:
        st.warning(msg)
    if st.button("Send request", type="primary"):
        if bad:
            st.error("Please correct the lines marked above.")
        elif not ok:
            st.info("Add at least one material.")
        else:
            b = create_mat_request(sid, labels, ok, needed, user)
            done(f"Request #{b} sent with {len(ok)} line(s).")


def section_mine(reqs, user):
    mine = reqs[(reqs.Status == "Pending") & (reqs["Requested by"] == user)]
    if mine.empty:
        return
    st.subheader("My pending requests")
    st.caption("You can still change these. Edit a quantity or tick Remove, then press Save changes.")
    tbl = pd.DataFrame({"Request #": mine.batch.astype(int).values, "Field": mine.Field.values, "Material": mine.Material.values,
                        "Unit": mine.Unit.values, "Quantity": mine.Requested.values, "Remove": False})
    ed = wide(st.data_editor, tbl, hide_index=True, key=f"{K('mp')}_{len(mine)}_{int(mine.id.iloc[0])}",
              disabled=["Request #", "Field", "Material", "Unit"],
              column_config={"Quantity": st.column_config.NumberColumn(min_value=0.01, step=0.01),
                             "Remove": st.column_config.CheckboxColumn()})
    if st.button("Save changes", key="mp_save"):
        removes = [int(i) for i, rm in zip(mine.id, ed["Remove"]) if rm]
        changes = {int(i): float(q) for i, q, rm, old in zip(mine.id, ed["Quantity"], ed["Remove"], mine.Requested)
                   if not rm and pd.notna(q) and abs(float(q) - float(old)) > 1e-9}
        if not removes and not changes:
            st.info("Nothing to change.")
        else:
            skipped = update_mat_pending(user, changes, removes)
            done("Changes saved." + (f" {skipped} line(s) could not be changed because they were already decided." if skipped else ""))


def section_delivery(reqs, user, uid):
    appr = reqs[reqs.Status == "Approved"]
    if appr.empty:
        return
    st.subheader("Approved: record the delivery")
    st.caption("When the materials arrive, check the quantity received and press Record delivery. The quantity is added to your stock.")
    tbl = pd.DataFrame({"Request #": appr.batch.astype(int).values, "Field": appr.Field.values, "Material": appr.Material.values,
                        "Unit": appr.Unit.values, "Approved": appr.Approved.values, "Received": appr.Approved.values})
    ed = wide(st.data_editor, tbl, hide_index=True, key=f"{K('dl')}_{len(appr)}_{int(appr.id.iloc[0])}",
              disabled=["Request #", "Field", "Material", "Unit", "Approved"],
              column_config={"Received": st.column_config.NumberColumn("Received", min_value=0.0, step=0.01)})
    if st.button("Record delivery", type="primary", key="dl_go"):
        deliv = {int(i): float(q) for i, q in zip(appr.id, ed["Received"]) if pd.notna(q) and float(q) > 0}
        if not deliv:
            st.info("Enter the quantity received for at least one line.")
        else:
            done(f"{deliver_mat_lines(deliv, user, uid)} delivery line(s) recorded. The stock was updated.")


def page_matreq():
    user, uid = st.session_state.user, st.session_state.uid
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    reqs = load_mat_requests([int(i) for i in sites.id])
    if has(CEO, ACCOUNTANT):
        section_approve(reqs, sites, user, has(CEO))
    section_new(sites, user)
    section_mine(reqs, user)
    if has(STORE):
        section_delivery(reqs, user, uid)
    st.subheader("All requests")
    if reqs.empty:
        st.caption("No requests yet.")
    else:
        request_list(reqs, "rq")


# ---------- money: requests to the CEO, money used, taxes and debts ----------
def money_cats():
    return df("SELECT name FROM money_categories ORDER BY name")["name"].tolist()


def field_choices(sites):
    return {"(no field)": None, **{r.name: int(r.id) for r in sites.itertuples()}}


def load_money_requests():
    """Everybody sees their own requests. The Accountant also sees those of their fields and company-level ones."""
    user, uid = st.session_state.user, st.session_state.uid
    where, params = "r.requested_by=?", [user]
    if has(CEO):
        where, params = "1=1", []
    elif has(ACCOUNTANT):
        where, params = "(r.requested_by=? OR r.site_id IS NULL OR r.site_id IN (SELECT site_id FROM user_sites WHERE user_id=?))", [user, uid]
    return df(f"""SELECT r.id, r.site_id, r.requested_on AS "Date", COALESCE(s.name, '(no field)') AS "Field",
                  r.category AS "Category", r.purpose AS "Purpose", r.amount AS "Requested", r.amount_approved AS "Approved",
                  r.status AS "Status", r.needed_by AS "Needed by", r.requested_by AS "Requested by",
                  r.decided_by AS "Decided by", r.reason AS "Reason", r.paid_on AS "Paid on"
                  FROM money_requests r LEFT JOIN sites s ON s.id=r.site_id WHERE {where} ORDER BY r.id DESC""", tuple(params))


def create_money_request(site_id, category, new_category, purpose, amount, needed_by, user):
    with conn() as c:
        cat = new_category.strip() if new_category.strip() else category
        if new_category.strip():
            ensure_named(c, "money_categories", cat)
        c.execute("INSERT INTO money_requests(site_id, category, purpose, amount, needed_by, requested_on, requested_by) "
                  "VALUES(?,?,?,?,?,?,?)", (site_id, cat, purpose.strip(), int(amount), str(needed_by), str(date.today()), user))


def update_money_pending(user, changes, removes):
    skipped = 0
    with conn() as c:
        for rid in removes:
            skipped += c.execute("DELETE FROM money_requests WHERE id=? AND status='Pending' AND requested_by=?", (rid, user)).rowcount == 0
        for rid, a in changes.items():
            skipped += c.execute("UPDATE money_requests SET amount=? WHERE id=? AND status='Pending' AND requested_by=?",
                                 (int(a), rid, user)).rowcount == 0
    return skipped


def decide_money_requests(decisions, user):
    """CEO. decisions = {request id: (amount to approve, reason)}. Amount 0 refuses the request."""
    today = str(date.today())
    try:
        with conn() as c:
            for rid, (a, why) in decisions.items():
                if a > 0:
                    n = c.execute("UPDATE money_requests SET status='Approved', amount_approved=?, decided_on=?, decided_by=? "
                                  "WHERE id=? AND status='Pending'", (int(a), today, user, rid)).rowcount
                else:
                    n = c.execute("UPDATE money_requests SET status='Rejected', amount_approved=0, decided_on=?, decided_by=?, reason=? "
                                  "WHERE id=? AND status='Pending'", (today, user, why or "Not approved", rid)).rowcount
                if n != 1:
                    raise Abort("Some requests were already decided. Please refresh the page.")
    except Abort as e:
        return str(e)
    return None


def pay_money_requests(ids, user):
    """CEO or Accountant records the payment. The money is added to the requester's 'money used'."""
    now, n = datetime.now().isoformat(timespec="seconds"), 0
    with conn() as c:
        for rid in ids:
            r = c.execute("SELECT * FROM money_requests WHERE id=? AND status='Approved'", (rid,)).fetchone()
            if r is None or r["requested_by"] == user:
                continue  # nobody records the payment of their own request
            c.execute("UPDATE money_requests SET status='Paid', paid_on=?, paid_by=? WHERE id=?", (str(date.today()), user, rid))
            c.execute("INSERT INTO money_ledger(spent_on, person, site_id, kind, category, amount, note, request_id, entered_by, entered_at) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?)", (str(date.today()), r["requested_by"], r["site_id"], "Business", r["category"],
                                                      r["amount_approved"], r["purpose"], rid, user, now))
            n += 1
    return n


def pending_money_requests():
    r = load_money_requests()
    return int((r.Status == "Pending").sum()) if has(CEO) else int((r.Status == "Approved").sum()) if has(ACCOUNTANT) else 0


def money_list(reqs, prefix):
    c1, c2 = st.columns([2, 3])
    status = c1.radio("Show", ["All", "Pending", "Approved", "Paid", "Rejected"], horizontal=True, key=f"{prefix}_st")
    with c2:
        q = search_box(f"{prefix}_q", "Search purpose, category or person", "Type part of a word…")
    view = reqs if status == "All" else reqs[reqs.Status == status]
    view = match(view, ["Purpose", "Category", "Requested by", "Field"], q)
    cols = ["id", "Date", "Field", "Category", "Purpose", "Requested", "Approved", "Status", "Needed by", "Requested by",
            "Decided by", "Reason", "Paid on"]
    wide(st.dataframe, pretty(view[cols].rename(columns={"id": "Request #"}), ["Requested", "Approved"], "Status"), hide_index=True)


def page_moneyreq():
    user = st.session_state.user
    sites = visible_sites()
    reqs = load_money_requests()
    if has(CEO):
        st.subheader("Waiting for your decision")
        pending = reqs[reqs.Status == "Pending"]
        if pending.empty:
            st.success("No pending money requests.")
        else:
            tbl = pd.DataFrame({"Request #": pending.id.values, "By": pending["Requested by"].values, "Field": pending.Field.values,
                                "Category": pending.Category.values, "Purpose": pending.Purpose.values,
                                "Requested": pending.Requested.values, "Needed by": pending["Needed by"].values,
                                "Decision": "Approve", "Amount to approve": pending.Requested.values, "Reason if rejected": ""})
            ed = wide(st.data_editor, tbl, hide_index=True, key=f"mrd{K('d')}_{abs(hash(tuple(int(i) for i in pending.id)))}",
                      disabled=["Request #", "By", "Field", "Category", "Purpose", "Requested", "Needed by"], column_config={
                          "Decision": st.column_config.SelectboxColumn(options=["Approve", "Reject", "Decide later"], required=True),
                          "Amount to approve": st.column_config.NumberColumn(min_value=0, step=1000)})
            if st.button(":material/check: Confirm decisions", type="primary", key="mrd_go"):
                decisions, problems = {}, []
                for rid, dec, amt, why in zip(pending.id, ed["Decision"], ed["Amount to approve"], ed["Reason if rejected"]):
                    amt = 0 if pd.isna(amt) else float(amt)
                    if dec == "Approve":
                        if amt <= 0:
                            problems.append(f"#{int(rid)}")
                        else:
                            decisions[int(rid)] = (amt, "")
                    elif dec == "Reject":
                        decisions[int(rid)] = (0, why.strip() if isinstance(why, str) and why.strip() else "Not approved")
                if problems:
                    st.error("Enter an amount above 0, or choose Reject, for: " + ", ".join(problems))
                elif not decisions:
                    st.warning("Nothing to confirm: every row is set to Decide later.")
                else:
                    err = decide_money_requests(decisions, user)
                    if err:
                        st.error(err)
                    else:
                        ok_n = sum(a > 0 for a, _ in decisions.values())
                        done(f"{ok_n} approved, {len(decisions) - ok_n} rejected.")
    st.subheader("New money request")
    st.caption("Ask the CEO for money, for example for fuel, transport, tools or materials.")
    fc = field_choices(sites)
    a1, a2, a3 = st.columns(3)
    field = a1.selectbox("Field", list(fc), key=K("mf"))
    cat = a2.selectbox("Category", money_cats(), key=K("mc"))
    newcat = a3.text_input("or a new category", key=K("mn"))
    b1, b2, b3 = st.columns([1, 2, 1])
    amount = b1.number_input("Amount (Frw)", min_value=1, step=1000, value=None, placeholder="Enter amount", key=K("ma"))
    purpose = b2.text_input("What is it for?", key=K("mp"))
    needed = b3.date_input("Needed by", date.today() + timedelta(days=2), min_value=date.today(), key=K("md"))
    if st.button("Send request to the CEO", type="primary", key="mr_send"):
        if not amount or not purpose.strip():
            st.warning("Enter the amount and what it is for.")
        else:
            create_money_request(fc[field], cat, newcat, purpose, amount, needed, user)
            done(f"Request for {money(amount)} Frw sent to the CEO.")
    mine = reqs[(reqs.Status == "Pending") & (reqs["Requested by"] == user)]
    if len(mine):
        st.subheader("My pending requests")
        tbl = pd.DataFrame({"Request #": mine.id.values, "Category": mine.Category.values, "Purpose": mine.Purpose.values,
                            "Amount": mine.Requested.values, "Remove": False})
        ed = wide(st.data_editor, tbl, hide_index=True, key=f"{K('mq')}_{len(mine)}_{int(mine.id.iloc[0])}",
                  disabled=["Request #", "Category", "Purpose"], column_config={"Amount": st.column_config.NumberColumn(min_value=1, step=1000),
                                                                                "Remove": st.column_config.CheckboxColumn()})
        if st.button("Save changes", key="mq_save"):
            removes = [int(i) for i, rm in zip(mine.id, ed["Remove"]) if rm]
            changes = {int(i): float(a) for i, a, rm, old in zip(mine.id, ed["Amount"], ed["Remove"], mine.Requested)
                       if not rm and pd.notna(a) and float(a) != float(old)}
            if not removes and not changes:
                st.info("Nothing to change.")
            else:
                skipped = update_money_pending(user, changes, removes)
                done("Changes saved." + (f" {skipped} could not be changed because they were already decided." if skipped else ""))
    if has(CEO, ACCOUNTANT):
        appr = reqs[(reqs.Status == "Approved") & (reqs["Requested by"] != user)]
        if len(appr):
            st.subheader("Approved: record the payment")
            st.caption("Tick Paid when the money has been given. It is then added to the person's money used.")
            tbl = pd.DataFrame({"Request #": appr.id.values, "By": appr["Requested by"].values, "Field": appr.Field.values,
                                "Category": appr.Category.values, "Purpose": appr.Purpose.values, "Approved": appr.Approved.values, "Paid": False})
            ed = wide(st.data_editor, tbl, hide_index=True, key=f"{K('mx')}_{len(appr)}_{int(appr.id.iloc[0])}",
                      disabled=["Request #", "By", "Field", "Category", "Purpose", "Approved"],
                      column_config={"Paid": st.column_config.CheckboxColumn()})
            if st.button("Record payment", type="primary", key="mx_go"):
                ids = [int(i) for i, p in zip(appr.id, ed["Paid"]) if p]
                if not ids:
                    st.info("Tick the requests that were paid.")
                else:
                    done(f"{pay_money_requests(ids, user)} payment(s) recorded.")
    st.subheader("Requests")
    if reqs.empty:
        st.caption("No money requests yet.")
    else:
        money_list(reqs, "mrl")


# ---------- money used ----------
def load_ledger():
    user = st.session_state.user
    where, params = ("(l.kind='Business' OR l.person=?)", [user]) if has(CEO, ACCOUNTANT) else ("l.person=?", [user])
    return df(f"""SELECT l.id, l.entered_by, l.request_id, l.spent_on AS "Date", l.person AS "Person",
                  COALESCE(s.name, '(no field)') AS "Field", l.kind AS "Kind", l.category AS "Category", l.amount AS "Amount",
                  l.note AS "Note" FROM money_ledger l LEFT JOIN sites s ON s.id=l.site_id WHERE {where}
                  ORDER BY l.spent_on DESC, l.id DESC""", tuple(params))


def parse_ledger(ed, cats, fields):
    ok, bad = [], []
    for pos, (_, r) in enumerate(ed.iterrows(), start=1):
        cat = r["Category"] if pd.notna(r["Category"]) else None
        new = str(r["New category"]).strip() if pd.notna(r["New category"]) else ""
        amt = pd.to_numeric(r["Amount"], errors="coerce")
        note = str(r["Note"]).strip() if pd.notna(r["Note"]) else ""
        if not cat and not new and (pd.isna(amt) or amt == 0) and not note:
            continue
        d = pd.to_datetime(r["Date"], errors="coerce")
        d = date.today() if pd.isna(d) else d.date()
        kind = r["Kind"] if r["Kind"] in ("Business", "Personal") else "Business"
        if not new and cat not in cats:
            bad.append(f"Row {pos}: choose a category, or type a new one.")
        elif pd.isna(amt) or amt <= 0:
            bad.append(f"Row {pos}: enter the amount.")
        elif d > date.today():
            bad.append(f"Row {pos}: the date is in the future.")
        else:
            ok.append({"date": d, "category": new or cat, "new": bool(new), "amount": int(round(float(amt))), "kind": kind,
                       "site": fields.get(r["Field"]), "note": note})
    return ok, bad


def add_ledger(entries, user):
    now = datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        for e in entries:
            if e["new"]:
                ensure_named(c, "money_categories", e["category"])
        bulk_insert(c, "money_ledger", ["spent_on", "person", "site_id", "kind", "category", "amount", "note", "entered_by", "entered_at"],
                    [(str(e["date"]), user, e["site"], e["kind"], e["category"], e["amount"], e["note"], user, now) for e in entries])


def page_moneyused():
    user = st.session_state.user
    if has(CEO):
        st.subheader("Record money I used")
        st.caption("Money you spent yourself: materials you bought, fuel, food, personal use. Choose Business or Personal. "
                   "Personal entries are visible only to you.")
        cats, fields = money_cats(), field_choices(visible_sites())
        grid = pd.DataFrame({"Date": [date.today()] * 4, "Category": [None] * 4, "New category": [""] * 4, "Amount": [0] * 4,
                             "Kind": ["Business"] * 4, "Field": ["(no field)"] * 4, "Note": [""] * 4})
        ed = wide(st.data_editor, grid, num_rows="dynamic", hide_index=True, key=K("lg"), column_config={
            "Date": st.column_config.DateColumn("Date", max_value=date.today(), format="YYYY-MM-DD"),
            "Category": st.column_config.SelectboxColumn("Category", options=cats, required=False),
            "New category": st.column_config.TextColumn("New category"),
            "Amount": st.column_config.NumberColumn("Amount (Frw)", min_value=0, step=100),
            "Kind": st.column_config.SelectboxColumn("Kind", options=["Business", "Personal"], required=True),
            "Field": st.column_config.SelectboxColumn("Field", options=list(fields), required=True)})
        ok, bad = parse_ledger(ed, cats, fields)
        if ok:
            st.write(f"**{len(ok)} line(s) ready, total {money(sum(e['amount'] for e in ok))} Frw**")
        for msg in bad:
            st.warning(msg)
        if st.button("Save money used", type="primary"):
            if bad:
                st.error("Please correct the lines marked above.")
            elif not ok:
                st.info("Nothing to save yet.")
            else:
                add_ledger(ok, user)
                done(f"{len(ok)} line(s) saved.")
    st.subheader("Money used")
    led = load_ledger()
    if led.empty:
        return st.caption("Nothing recorded yet.")
    f1, f2, f3 = st.columns([2, 2, 3])
    kind = f1.radio("Show", ["All", "Business", "Personal"], horizontal=True, key="ml_k")
    cat = f2.selectbox("Category", ["All categories"] + sorted(set(led.Category)), key="ml_c")
    with f3:
        q = search_box("ml_q", "Search note, person or field", "Type part of a word…")
    view = led if kind == "All" else led[led.Kind == kind]
    view = view if cat == "All categories" else view[view.Category == cat]
    view = match(view, ["Note", "Person", "Field"], q)
    k = st.columns(3)
    k[0].metric("Total", money(view.Amount.sum()))
    k[1].metric("Business", money(view[view.Kind == "Business"].Amount.sum()))
    k[2].metric("Personal", money(view[view.Kind == "Personal"].Amount.sum()))
    bars(view.groupby("Category", as_index=False).Amount.sum().sort_values("Amount", ascending=False).head(10), "Category", "Amount")
    cols = ["Date", "Person", "Field", "Kind", "Category", "Amount", "Note", "request_id"]
    shown = view.head(300)
    mine = shown[(shown.entered_by == user) & shown.request_id.isna()]
    wide(st.dataframe, pretty(shown[cols].rename(columns={"request_id": "Request #"}), ["Amount"]), hide_index=True)
    if has(CEO) and len(mine):
        with st.expander("Delete my own entries"):
            tbl = mine[["Date", "Category", "Amount", "Note"]].copy()
            tbl["Remove"] = False
            ed2 = wide(st.data_editor, tbl, hide_index=True, key=f"{K('ld')}_{len(mine)}", disabled=["Date", "Category", "Amount", "Note"])
            if st.button("Delete ticked entries", key="ml_del"):
                ids = [int(i) for i, r in zip(mine.id, ed2["Remove"]) if r]
                if ids:
                    with conn() as c:
                        for i in ids:
                            c.execute("DELETE FROM money_ledger WHERE id=? AND entered_by=? AND request_id IS NULL", (i, user))
                    done(f"{len(ids)} entr{'y' if len(ids) == 1 else 'ies'} deleted.")


# ---------- taxes and debts (CEO and Accountant) ----------
def page_taxdebt():
    if not has(CEO, ACCOUNTANT):
        return st.error("This page is for the CEO and the Accountant.")
    user = st.session_state.user
    fc = field_choices(visible_sites())
    st.subheader("Taxes paid")
    a1, a2, a3 = st.columns(3)
    ttype = a1.selectbox("Tax", TAX_TYPES, key=K("tt"))
    period = a2.text_input("Period", placeholder="For example October 2026", key=K("tp"))
    tamount = a3.number_input("Amount (Frw)", min_value=1, step=1000, value=None, placeholder="Enter amount", key=K("ta"))
    b1, b2, b3 = st.columns(3)
    tday = b1.date_input("Date paid", date.today(), max_value=date.today(), key=K("td"))
    tref = b2.text_input("Receipt or reference", key=K("tr"))
    tfield = b3.selectbox("Field (optional)", list(fc), key=K("tf"))
    if st.button("Save tax payment", type="primary"):
        if not tamount:
            st.warning("Enter the amount.")
        else:
            run("INSERT INTO taxes(tax_type, period, amount, paid_on, reference, site_id, entered_by) VALUES(?,?,?,?,?,?,?)",
                (ttype, period.strip(), int(tamount), str(tday), tref.strip(), fc[tfield], user))
            done(f"{money(tamount)} Frw of {ttype} recorded.")
    taxes = df("""SELECT t.paid_on AS "Date", t.tax_type AS "Tax", t.period AS "Period", t.amount AS "Amount",
                  t.reference AS "Reference", COALESCE(s.name, '(no field)') AS "Field", t.entered_by AS "By"
                  FROM taxes t LEFT JOIN sites s ON s.id=t.site_id ORDER BY t.paid_on DESC, t.id DESC""")
    if taxes.empty:
        st.caption("No tax payments recorded yet.")
    else:
        q = search_box("q_tax", "Search tax, period or reference", "Type part of a word…")
        view = match(taxes, ["Tax", "Period", "Reference", "Field"], q)
        st.caption(f"{len(view)} payments, total {money(view.Amount.sum())} Frw")
        bars(view.groupby("Tax", as_index=False).Amount.sum().sort_values("Amount", ascending=False), "Tax", "Amount")
        wide(st.dataframe, pretty(view.head(300), ["Amount"]), hide_index=True)

    st.subheader("Debts")
    st.caption("Money the company owes, or money others owe the company.")
    d1, d2, d3 = st.columns(3)
    party = d1.text_input("Who", placeholder="Supplier, bank, client…", key=K("dp"))
    direction = d2.selectbox("Direction", ["We owe", "Owed to us"], key=K("dd"))
    damount = d3.number_input("Amount (Frw)", min_value=1, step=1000, value=None, placeholder="Enter amount", key=K("da"))
    e1, e2, e3 = st.columns(3)
    due = e1.date_input("Due date", date.today() + timedelta(days=30), key=K("de"))
    dfield = e2.selectbox("Field (optional)", list(fc), key=K("df"))
    dnote = e3.text_input("Note (optional)", key=K("dn"))
    if st.button("Save debt", type="primary"):
        if not party.strip() or not damount:
            st.warning("Enter who and the amount.")
        else:
            run("INSERT INTO debts(party, direction, amount, due_on, site_id, note, entered_by, entered_on) VALUES(?,?,?,?,?,?,?,?)",
                (party.strip(), direction, int(damount), str(due), fc[dfield], dnote.strip(), user, str(date.today())))
            done(f"Debt with {party.strip()} saved.")
    debts = df("""SELECT d.id, d.party AS "Who", d.direction AS "Direction", d.amount AS "Amount",
                  COALESCE((SELECT SUM(p.amount) FROM debt_payments p WHERE p.debt_id=d.id), 0) AS "Paid",
                  d.due_on AS "Due", COALESCE(s.name, '(no field)') AS "Field", d.note AS "Note"
                  FROM debts d LEFT JOIN sites s ON s.id=d.site_id ORDER BY d.due_on""")
    if debts.empty:
        return st.caption("No debts recorded yet.")
    debts["Balance"] = debts.Amount - debts.Paid
    debts["Status"] = ["Settled" if b <= 0 else ("Overdue" if due_ < str(date.today()) else "Open") for b, due_ in zip(debts.Balance, debts.Due)]
    q2 = search_box("q_debt", "Search who, field or note", "Type part of a word…")
    view = match(debts, ["Who", "Field", "Note"], q2)
    k = st.columns(2)
    k[0].metric("We owe (open)", money(view[(view.Direction == "We owe") & (view.Balance > 0)].Balance.sum()))
    k[1].metric("Owed to us (open)", money(view[(view.Direction == "Owed to us") & (view.Balance > 0)].Balance.sum()))
    wide(st.dataframe, pretty(view.drop(columns="id"), ["Amount", "Paid", "Balance"], "Status"), hide_index=True)
    openr = debts[debts.Balance > 0]
    if len(openr):
        st.markdown("**Record a payment on a debt**")
        labels = {f"{r.Who} ({r.Direction}) - balance {money(r.Balance)}": int(r.id) for r in openr.itertuples()}
        p1, p2, p3 = st.columns(3)
        which = p1.selectbox("Debt", list(labels), index=None, placeholder="Choose a debt", key=K("pd"))
        pam = p2.number_input("Amount paid (Frw)", min_value=1, step=1000, value=None, placeholder="Enter amount", key=K("pa"))
        pday = p3.date_input("Date", date.today(), max_value=date.today(), key=K("pt"))
        if st.button("Save payment", key="dp_save"):
            if not which or not pam:
                st.warning("Choose the debt and enter the amount.")
            elif pam > float(openr[openr.id == labels[which]].Balance.iloc[0]):
                st.error("The payment is more than the balance of this debt.")
            else:
                run("INSERT INTO debt_payments(debt_id, amount, paid_on, entered_by) VALUES(?,?,?,?)", (labels[which], int(pam), str(pday), user))
                done("Payment saved.")


# ---------- analytics: charts that follow the person's access ----------
def count_bars(series, color=""):
    if len(series):
        c = series.value_counts().rename_axis("Status").reset_index(name="Number")
        bars(c, "Status", "Number", fmt=lambda v: f"{int(v)}", color=color)
    else:
        st.caption("Nothing yet.")


def month_totals(frame, date_col, val_col, months=12):
    m = frame.assign(Month=frame[date_col].astype(str).str[:7]).groupby("Month", as_index=False)[val_col].sum()
    return m.sort_values("Month", ascending=False).head(months)


def page_analytics():
    user = st.session_state.user
    sites = visible_sites()
    ids = [int(i) for i in sites.id]
    st.caption("Charts for the parts of the system you can access. Several roles show several groups of charts.")
    scope_ids, field = ids, "All fields"
    if len(sites) > 1 or has(CEO):
        field = st.selectbox("Field", ["All fields"] + sites.name.tolist(), key="an_field")
        if field != "All fields":
            scope_ids = [int(sites[sites.name == field].id.iloc[0])]
    if not scope_ids:
        st.info("No field is linked to your account yet, so there are no field charts.")
    if scope_ids and has(CEO, ACCOUNTANT, FIELD):
        exp = load_expenses(scope_ids)
        st.subheader("Spending")
        if exp.empty:
            st.caption("No spending recorded yet.")
        else:
            st.markdown("**By category (top 10, Frw)**")
            bars(exp.groupby("Category", as_index=False).Total.sum().sort_values("Total", ascending=False).head(10), "Category", "Total")
            st.markdown("**By month (last 12, Frw)**")
            bars(month_totals(exp, "Date", "Total"), "Month", "Total")
            if field == "All fields" and len(sites) > 1:
                st.markdown("**By field (Frw)**")
                bars(exp.groupby("Field", as_index=False).Total.sum().sort_values("Total", ascending=False).head(10), "Field", "Total")
        if has(CEO):
            fnd = load_funds(ids)
            per = sites.rename(columns={"name": "Field"})[["id", "Field"]].copy()
            per["Funds received"] = per.id.map(fnd.groupby("site_id").Amount.sum()).fillna(0).astype(int)
            per["Spent"] = per.id.map(load_expenses(ids).groupby("site_id").Total.sum()).fillna(0).astype(int)
            if field != "All fields":
                per = per[per.Field == field]
            if per["Funds received"].sum() or per.Spent.sum():
                st.markdown("**Funds received and spent by field (Frw)**")
                compare_bars(per.sort_values("Spent", ascending=False).head(10), "Field", "Funds received", "Spent")
    if scope_ids:
        st.subheader("Materials")
        mv, reqs = load_moves(scope_ids), load_mat_requests(scope_ids)
        bal = stock_balances(mv)
        if bal.empty:
            st.caption("No materials recorded yet.")
        else:
            st.markdown("**Largest stock balances**")
            top = bal[bal.Balance > 1e-9].assign(Item=lambda d: d.Material + " (" + d.Unit + ")").sort_values("Balance", ascending=False).head(10)
            bars(top, "Item", "Balance", fmt=lambda v: f"{v:,.2f}".rstrip("0").rstrip("."), color="")
            q = mv.assign(Qty=pd.to_numeric(mv.Qty))
            by = q[q.Type.isin(["Received", "Used"])].assign(Month=lambda d: d.Date.astype(str).str[:7])
            if len(by):
                st.markdown("**Deliveries and use by month (number of lines)**")
                piv = by.groupby(["Month", "Type"]).size().unstack(fill_value=0).reindex(columns=["Received", "Used"], fill_value=0)
                piv = piv.reset_index().sort_values("Month", ascending=False).head(6)
                compare_bars(piv, "Month", "Received", "Used")
        st.markdown("**Material requests by status**")
        count_bars(reqs.Status if has(CEO, ACCOUNTANT, STORE) else reqs[reqs["Requested by"] == user].Status)
    st.subheader("Money")
    mr = load_money_requests()
    st.markdown("**Money requests by status**")
    count_bars(mr.Status)
    if has(CEO, ACCOUNTANT):
        led = load_ledger()
        if len(led):
            st.markdown("**Money used by category (Frw)**")
            bars(led[led.Kind == "Business"].groupby("Category", as_index=False).Amount.sum().sort_values("Amount", ascending=False).head(10),
                 "Category", "Amount")
        taxes = df("""SELECT tax_type AS "Tax", SUM(amount) AS "Amount" FROM taxes GROUP BY tax_type""")
        if len(taxes):
            st.markdown("**Taxes paid (Frw)**")
            bars(taxes.sort_values("Amount", ascending=False), "Tax", "Amount")
        debts = df("""SELECT d.direction AS "Direction", d.amount - COALESCE((SELECT SUM(p.amount) FROM debt_payments p WHERE p.debt_id=d.id), 0) AS "Balance"
                      FROM debts d""")
        if len(debts) and (debts.Balance > 0).any():
            st.markdown("**Open debts (Frw)**")
            bars(debts[debts.Balance > 0].groupby("Direction", as_index=False).Balance.sum(), "Direction", "Balance")


# ---------- fields and categories ----------
def add_site(name, client, location, budget, start):
    with conn() as c:
        return c.execute("INSERT INTO sites(name, client, location, budget, start_date) VALUES(?,?,?,?,?) RETURNING id",
                         (name.strip(), client.strip(), location.strip(), int(budget or 0), str(start))).fetchone()["id"]


def page_setup():
    if not has(CEO, ACCOUNTANT):
        return st.error("This page is for the CEO and the Accountant.")
    if has(CEO):
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
    if not has(CEO, ACCOUNTANT):
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
    options = (["New field…"] if has(CEO) else []) + list(site_ids)
    if not options:
        return no_field_message()
    target = st.selectbox("Import into which field?", options, key=K("it"))
    new_name = new_client = ""
    if target == "New field…":
        n1, n2 = st.columns(2)
        new_name = n1.text_input("New field name", key=K("inn"))
        new_client = n2.text_input("Client", key=K("inc"))
    add_funds = False
    if funds and has(*FUNDS_VISIBLE):
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
    if has(*FUNDS_VISIBLE):  # funds received are for the CEO only
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
        rows = list(ev.selection.rows) if ev is not None else []
    except TypeError:  # an older Streamlit without row selection: the list below is used
        wide(st.dataframe, view, hide_index=True)
        rows = []
    with st.expander("Add a user"):
        st.caption("Choose one or more roles. The CEO sees all fields. Every other user needs at least one field.")
        a1, a2 = st.columns(2)
        nu = a1.text_input("User name", key=K("nu"))
        nroles = a2.multiselect("Roles", ROLES, default=[FIELD], key=K("nr"))
        ne = a1.text_input("Email (optional, used to reset a forgotten password)", key=K("ne"))
        npw = a2.text_input("Temporary password (at least 8 characters)", type="password", key=K("np"))
        ns = st.multiselect("Fields this user works on", list(site_ids), key=K("ns")) if nroles and CEO not in nroles else []
        if st.button("Add user"):
            err = None
            if not nroles:
                err = "Choose at least one role."
            elif CEO not in nroles and not ns:
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
                        key=K(f"ef{uid}")) if CEO not in nr else cur_fields
    if st.button("Save roles and fields"):
        ceos = [i for i, r in roles_of.items() if CEO in r]
        if not nr:
            st.error("Choose at least one role.")
        elif CEO not in nr and not nf:
            st.error("A user without the CEO role needs at least one field.")
        elif uid in ceos and CEO not in nr and len(ceos) <= 1:
            st.error("There must be at least one CEO.")
        else:
            set_user_roles(uid, nr)
            if CEO not in nr:
                set_user_sites(uid, [site_ids[n] for n in nf])
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


# ---------- main ----------
PAGE_DEFS = [  # key, label, icon, roles that get the page. A user with several roles gets all their pages.
    ("dashboard", "Dashboard", "dashboard", (CEO, ACCOUNTANT, FIELD)),
    ("stock", "Material stock", "inventory_2", ALL),
    ("analytics", "Analytics", "monitoring", ALL),
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
RENDER = {"dashboard": page_dashboard, "expenses": page_expenses, "funds": page_funds, "import": page_import,
          "setup": page_setup, "reports": page_reports, "users": page_users, "stock": page_stock,
          "receive": page_receive, "use": page_use, "count": page_count, "matreq": page_matreq,
          "analytics": page_analytics, "moneyreq": page_moneyreq, "moneyused": page_moneyused, "taxdebt": page_taxdebt}


def allowed_pages():
    out = []
    for key, label, icon, roles in PAGE_DEFS:
        if has(*roles):
            out.append((key, "Dashboard" if key == "stock" and not has(CEO, ACCOUNTANT, FIELD) else label, icon))
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
    counts = {}
    if has(CEO, ACCOUNTANT):  # requests waiting for this person
        counts["matreq"], counts["moneyreq"] = pending_material_requests(), pending_money_requests()
    sec, title = side_menu(allowed_pages(), counts)
    header(title)
    show_flash()
    RENDER[sec]()
    with st.sidebar:
        user_box()


if __name__ == "__main__":
    main()
