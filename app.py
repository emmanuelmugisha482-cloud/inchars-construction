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
           "qty": ("units", "qty", "quantity"), "price": ("priceunitfrw", "punitfrw", "punit", "priceunit", "unitprice", "pu"),
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
        for stmt in ("ALTER TABLE materials ADD COLUMN category TEXT",
                     "ALTER TABLE money_ledger ADD COLUMN qty DOUBLE PRECISION",
                     "ALTER TABLE money_ledger ADD COLUMN unit_price DOUBLE PRECISION"):
            if IS_PG:
                c.execute(stmt.replace("ADD COLUMN", "ADD COLUMN IF NOT EXISTS"))
            else:
                try:
                    c.execute(stmt)
                except sqlite3.OperationalError:
                    pass  # column already there
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
        def _m(v):
            n = pd.to_numeric(v, errors="coerce")
            return "" if pd.isna(n) else f"{n:,.0f}"

        def _qq(v):
            n = pd.to_numeric(v, errors="coerce")
            return "" if pd.isna(n) else _q(n)

        fmt = {c: _m for c in money_cols if c in frame.columns}
        fmt.update({c: _qq for c in qty_cols if c in frame.columns})
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
    """CEO sees every field. Everybody else only the fields given to them. On the new pages the Field drop-down at the
    top narrows this to one field (ceo_scope is set by main)."""
    if has(*SEE_ALL):
        sites = df("SELECT * FROM sites ORDER BY name")
    else:
        sites = df("""SELECT s.* FROM sites s JOIN user_sites u ON u.site_id=s.id WHERE u.user_id=? ORDER BY s.name""",
                   (st.session_state.uid,))
    chosen = st.session_state.get("ceo_scope")
    if chosen and chosen != "All fields":
        sites = sites[sites.name == chosen].reset_index(drop=True)
    return sites


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
    shown = mv.assign(Qty=mv.Qty.abs())[["Date", "Material", "Unit", "Qty", "Detail", "Entered by"]].reset_index(drop=True)
    shown.insert(0, "No", range(1, len(shown) + 1))
    wide(st.dataframe, pretty(shown, qty_cols=["Qty"]), hide_index=True)
    if len(shown):
        st.download_button(":material/download: Download Excel", to_excel({kind: shown}), f"materials_{kind.lower()}_{date.today()}.xlsx",
                           mime=XLSX, key=f"dl_{key}")


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
    """Money requests and money used use the SAME categories as the expenses / Excel workbook."""
    return categories_df().name.tolist()


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
            ensure_named(c, "categories", cat)
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
                ensure_named(c, "categories", e["category"])
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
        sheet_show(view.assign(Item=view.Tax + " " + view.Period.fillna(""), Qty=1.0, **{"P/U": view.Amount, "Total": view.Amount}),
                   ["Period", "Reference", "Field", "By"], "Taxes paid", "tx")

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
    sheet_show(view.assign(Date=view.Due, Item=view.Who, Qty=1.0, **{"P/U": view.Amount, "Total": view.Amount}),
               ["Direction", "Paid", "Balance", "Status", "Field", "Note"], "Debts", "db")
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
# =====================================================================================================
# CEO PAGES
# Every CEO page follows the same logic as the cost workbook (Excel):  No | Date | Item | Qty | P/U | Total
# with a Grand total line and a download bar, and every CEO data page has the Field drop-down at the top
# (All fields, then each field). Other roles keep their pages as they were until we confirm this design.
# =====================================================================================================
CEO_SCOPED = {"dashboard", "stock", "matreq", "expenses", "moneyreq", "moneyused", "funds", "reports"}
CORE = ["No", "Date", "Item", "Qty", "P/U", "Total"]
NEW_CAT = "+ New category…"
PALETTE = ["#0b6a35", "#e0922f", "#2f6fb0", "#8e5ea2", "#c9453b", "#3aa6a0", "#7c8b2c", "#b5527f", "#5c6b73", "#d4b21f"]


def field_bar():
    """The Field drop-down at the top of every data page (All fields, then each field). A person with one field sees its name."""
    st.session_state.ceo_scope = None
    names = visible_sites().name.tolist()
    if not names:
        return "All fields"
    if len(names) == 1 and not has(CEO):
        st.markdown(f"**Field:** {names[0]}")
        st.session_state.ceo_field = names[0]
        return names[0]
    options = ["All fields"] + names
    saved = st.session_state.get("ceo_field", "All fields")
    if saved not in options:
        saved = "All fields"
    a, _ = st.columns([2, 3])
    choice = a.selectbox("Field", options, index=options.index(saved), key="ceo_field_pick")
    st.session_state.ceo_field = choice
    return choice


def sheet_number(frame, cols):
    out = frame.reset_index(drop=True).copy()
    out.insert(0, "No", range(1, len(out) + 1))
    return out[cols]


def _xl_dates(tbl):
    """Real Excel dates (not text), so the file looks like the workbook and can be imported again."""
    tbl = tbl.copy()
    if "Date" in tbl.columns:
        tbl["Date"] = pd.Series([pd.to_datetime(v, errors="coerce") for v in tbl["Date"]], index=tbl.index, dtype=object).map(
            lambda v: None if pd.isna(v) else v.date())
    return tbl


def sheet_with_total(frame):
    """One category sheet exactly like the workbook: No, Date, Item, Qty, P/U, Total, then the Grand total line."""
    t = _xl_dates(sheet_number(frame, CORE))
    line = {"No": None, "Date": None, "Item": "Grand total", "Qty": None, "P/U": None, "Total": float(pd.to_numeric(t.Total).sum())}
    return pd.concat([t, pd.DataFrame([line])], ignore_index=True)


def _sheet_name(name, used):
    base = re.sub(r"[\[\]:*?/\\]", "", str(name)).strip()[:28] or "Sheet"
    out, n = base, 2
    while out.lower() in used:
        out, n = f"{base}_{n}", n + 1
    used.add(out.lower())
    return out


def cost_workbook(frame, category_col="Category"):
    """Excel file with a SUMMARY sheet and one sheet per category (the same layout as the cost workbook)."""
    used = {"summary"}
    sheets = {}
    summ = []
    for cat, part in frame.groupby(category_col):
        sheets[_sheet_name(cat, used)] = sheet_with_total(part)
        summ.append({"Category": cat, "Rows": len(part), "Total": float(pd.to_numeric(part.Total).sum())})
    summary = pd.DataFrame(summ, columns=["Category", "Rows", "Total"])
    if len(summary):
        summary = pd.concat([summary, pd.DataFrame([{"Category": "Grand total", "Rows": int(summary.Rows.sum()),
                                                      "Total": float(summary.Total.sum())}])], ignore_index=True)
    return to_excel({"SUMMARY": summary, **sheets})


def sheet_view(frame, key, extras=(), removable=False):
    """Excel-like table with No, Date, Item, Qty, P/U, Total (and the extra columns), the Grand total, and a
    download bar. Returns the ids ticked for removal when removable=True (the frame needs an id column)."""
    cols = CORE + [c for c in extras if c in frame.columns]
    tbl = sheet_number(frame, cols)
    total = float(pd.to_numeric(tbl.Total).sum()) if len(tbl) else 0.0
    st.markdown(f"**{len(tbl)} rows · Grand total {money(total)} Frw**")
    ticked = []
    if removable and len(tbl):
        t2 = tbl.copy()
        t2["Remove"] = False
        ed = wide(st.data_editor, t2, hide_index=True, key=f"{K(key)}_{len(t2)}_{int(total)}", disabled=cols)
        ticked = [int(i) for i, r in zip(frame.reset_index(drop=True).id, ed["Remove"]) if r]
    else:
        wide(st.dataframe, pretty(tbl, ["P/U", "Total"], qty_cols=["Qty"]), hide_index=True)
    return ticked


def download_sheet(frame, extras, name, key, label="Download Excel"):
    """Download bar for one table. Keeps the Excel layout, so he can keep working in Excel if he prefers."""
    cols = CORE + [c for c in extras if c in frame.columns]
    tbl = _xl_dates(sheet_number(frame, cols))
    if len(tbl):
        line = {c: None for c in cols}
        line.update({"Item": "Grand total", "Total": float(pd.to_numeric(tbl.Total).sum())})
        tbl = pd.concat([tbl, pd.DataFrame([line])], ignore_index=True)
    sheet_name = _sheet_name(name, set())
    st.download_button(f":material/download: {label}", to_excel({sheet_name: tbl}), f"{norm(name) or 'sheet'}_{date.today()}.xlsx",
                       mime=XLSX, key=f"dl_{key}")


def download_workbook(frame, name, key, label="Download Excel (one sheet per category)"):
    if frame.empty:
        return
    st.download_button(f":material/download: {label}", cost_workbook(frame), f"{norm(name) or 'sheets'}_{date.today()}.xlsx",
                       mime=XLSX, key=f"dl_{key}")


def category_pick(key):
    """Category list (the same for everybody, the workbook categories) with the option to add a new one."""
    cats = categories_df().name.tolist()
    choice = st.selectbox("Category", cats + [NEW_CAT], key=key)
    if choice == NEW_CAT:
        return st.text_input("New category name", key=key + "n").strip(), True
    return choice, False


def sheet_editor(key, rows=5):
    """Rows written like in Excel: Date, Item, Qty, P/U and the Total of every row, live, with the Grand total below."""
    n_key = K(key + "_n")
    n = st.session_state.get(n_key, rows)
    widths = [0.5, 1.6, 3.2, 1.0, 1.6, 1.7]
    head = st.columns(widths)
    for col, t in zip(head, ["No", "Date", "Item", "Qty", "P/U (Frw)", "Total"]):
        col.markdown(f"**{t}**")
    recs, grand = [], 0.0
    for i in range(n):
        c = st.columns(widths)
        c[0].markdown(f"{i + 1}")
        d = c[1].date_input("Date", date.today(), max_value=date.today(), key=f"{K(key)}_d{i}", label_visibility="collapsed")
        item = c[2].text_input("Item", key=f"{K(key)}_t{i}", placeholder="Item", label_visibility="collapsed")
        q = c[3].number_input("Qty", min_value=0.0, value=1.0, step=1.0, key=f"{K(key)}_q{i}", label_visibility="collapsed")
        p = c[4].number_input("P/U", min_value=0.0, value=None, step=100.0, key=f"{K(key)}_p{i}", placeholder="0", label_visibility="collapsed")
        line = float(q or 0) * float(p or 0)
        grand += line
        c[5].markdown(f"<div style='padding-top:6px;font-weight:700;text-align:right'>{money(line)}</div>", unsafe_allow_html=True)
        recs.append({"Date": d, "Item": item, "Qty": q, "P/U": p})
    foot = st.columns(widths)
    if foot[1].button("+ Add 5 rows", key=f"{K(key)}_more"):
        st.session_state[n_key] = n + 5
        st.rerun()
    foot[2].markdown("<div style='text-align:right;padding-top:6px'><b>Total</b></div>", unsafe_allow_html=True)
    foot[5].markdown(f"<div style='padding-top:6px;font-weight:800;text-align:right;border-top:2px solid #0b6a35'>{money(grand)}</div>", unsafe_allow_html=True)
    return pd.DataFrame(recs, columns=["Date", "Item", "Qty", "P/U"])


def parse_sheet(ed):
    ok, bad = [], []
    for pos, (_, r) in enumerate(ed.iterrows(), start=1):
        item = str(r["Item"]).strip() if pd.notna(r["Item"]) else ""
        qty, pu = pd.to_numeric(r["Qty"], errors="coerce"), pd.to_numeric(r["P/U"], errors="coerce")
        if not item and (pd.isna(pu) or pu == 0):
            continue  # empty row
        d = pd.to_datetime(r["Date"], errors="coerce")
        d = date.today() if pd.isna(d) else d.date()
        if not item:
            bad.append(f"Row {pos}: write the item name.")
        elif pd.isna(qty) or qty <= 0:
            bad.append(f"Row {pos}: the quantity must be above 0.")
        elif pd.isna(pu) or pu <= 0:
            bad.append(f"Row {pos}: enter the P/U.")
        elif d > date.today():
            bad.append(f"Row {pos}: the date is in the future.")
        else:
            ok.append({"date": d, "item": item, "qty": float(qty), "price": float(pu), "total": int(round(float(qty) * float(pu)))})
    return ok, bad


def sheet_preview(ok):
    if ok:
        prev = pd.DataFrame({"Date": [str(e["date"]) for e in ok], "Item": [e["item"] for e in ok], "Qty": [e["qty"] for e in ok],
                             "P/U": [e["price"] for e in ok], "Total": [e["total"] for e in ok]})
        st.markdown(f"**{len(ok)} row(s) ready · Grand total {money(prev.Total.sum())} Frw**")
        wide(st.dataframe, pretty(sheet_number(prev, CORE), ["P/U", "Total"], qty_cols=["Qty"]), hide_index=True)


def field_target(sites, key):
    """The field a new row belongs to: the chosen field, or a list when All fields is chosen."""
    if len(sites) == 1:
        st.markdown(f"**Field:** {sites.name.iloc[0]}")
        return int(sites.id.iloc[0]), sites.name.iloc[0]
    name = st.selectbox("Add to field", sites.name.tolist(), key=key)
    return int(sites[sites.name == name].id.iloc[0]), name


def ceo_add_cost_rows(sites, key):
    """Add rows to a category sheet (they are saved as expenses of the field)."""
    user = st.session_state.user
    a, b = st.columns(2)
    with a:
        cat, is_new = category_pick(K(key + "c"))
    with b:
        site_id, fname = field_target(sites, K(key + "f"))
    ed = sheet_editor(key + "g")
    ok, bad = parse_sheet(ed)
    sheet_preview(ok)
    for msg in bad:
        st.warning(msg)
    if st.button("Save rows", type="primary", key=f"{key}_save"):
        if not cat:
            st.warning("Write the name of the new category.")
        elif bad:
            st.error("Please correct the lines marked above.")
        elif not ok:
            st.info("Nothing to save yet.")
        else:
            cats = categories_df()
            cat_ids = dict(zip(cats.name, cats.id.astype(int)))
            entries = [{"date": e["date"], "category": None if is_new else cat, "new_category": cat if is_new else "",
                        "item": e["item"], "qty": e["qty"], "price": e["price"], "total": e["total"]} for e in ok]
            add_expenses(site_id, cat_ids, entries, user)
            done(f"{len(ok)} row(s) saved in {cat} for {fname}.")


def cost_frame(exp):
    """Expenses in the workbook layout."""
    return exp.rename(columns={"Unit price": "P/U"})


# ---------- charts (Altair; if a chart cannot be drawn the page still works) ----------
SERIES_COLORS = {"Spent": "#e0922f", "Funds received": "#0b6a35", "Received": "#2f6fb0", "Used": "#e0922f", "Spent (cumulative)": "#e0922f",
                 "Money used": "#8e5ea2", "Transactions": "#2f6fb0", "In stock": "#0b6a35", "Out of stock": "#c9453b"}
AXIS_EXPR = ("datum.value >= 1000000 ? format(datum.value/1000000, ',.1f') + 'M' : "
             "datum.value >= 1000 ? format(datum.value/1000, ',.0f') + 'K' : datum.value")


def short(v):
    """12,345,678 -> 12.3M, so labels on charts stay short and readable."""
    v = float(v)
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:.1f}B"
    if a >= 1e6:
        return f"{v / 1e6:.1f}M"
    if a >= 1e3:
        return f"{v / 1e3:.0f}K"
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.1f}"


def month_label(m, with_year=True):
    try:
        return datetime.strptime(m + "-01", "%Y-%m-%d").strftime("%b %Y" if with_year else "%b")
    except Exception:
        return str(m)


def _alt():
    try:
        import altair as alt
        return alt
    except Exception:
        return None


def _recs(frame, cols):
    out = []
    for row in frame[cols].itertuples(index=False):
        out.append({c: (None if pd.isna(v) else (v.item() if hasattr(v, "item") else v)) for c, v in zip(cols, row)})
    return out


def _draw(chart, key=None, select=False):
    """Draw a chart across the page width. With select=True a click on a bar is returned (needs a recent Streamlit)."""
    attempts = []
    if select:
        attempts += [dict(width="stretch", on_select="rerun", key=key), dict(use_container_width=True, on_select="rerun", key=key)]
    attempts += [dict(width="stretch"), dict(use_container_width=True)]
    for kw in attempts:
        try:
            return st.altair_chart(chart, **kw)
        except TypeError:
            continue
    return None


def _none(msg="Nothing to show yet."):
    st.caption(msg)


PAD = {"left": 8, "right": 48, "top": 14, "bottom": 8}


def c_bar(frame, label, value, horizontal=True, highlight=None, xtitle=None, unit="Frw", key=None, select=False, color=None):
    """Bars with the value written at the end of each bar. Returns the clicked label when select=True."""
    if frame is None or frame.empty:
        return _none()
    alt = _alt()
    try:
        rec = frame[[label, value]].copy()
        rec["Label"] = [short(v) for v in rec[value]]
        rec["Hi"] = [bool(highlight) and str(l) == str(highlight) for l in rec[label]]
        top = max(float(rec[value].max()), 1.0)
        data = alt.Data(values=_recs(rec, [label, value, "Label", "Hi"]))
        base = alt.Chart(data)
        col = alt.condition("datum.Hi", alt.value("#e0922f"), alt.value(color or "#0b6a35"))
        tip = [alt.Tooltip(f"{label}:N", title=label), alt.Tooltip(f"{value}:Q", format=",.0f", title=unit)]
        if horizontal:
            y = alt.Y(f"{label}:N", sort="-x", title=None, axis=alt.Axis(labelLimit=260, labelFontSize=12, ticks=False, domain=False))
            x = alt.X(f"{value}:Q", title=xtitle or unit, scale=alt.Scale(domain=[0, top * 1.22], nice=False),
                      axis=alt.Axis(labelExpr=AXIS_EXPR, grid=True, tickCount=5, title=xtitle or unit))
            bars = base.mark_bar(cornerRadiusEnd=4, size=20).encode(y=y, x=x, color=col, tooltip=tip)
            txt = base.mark_text(align="left", dx=5, fontSize=12, fontWeight="bold", color="#1d2b22").encode(y=y, x=x, text="Label:N")
            height = max(120, 32 * len(rec) + 40)
        else:
            x = alt.X(f"{label}:N", sort=None, title=xtitle, axis=alt.Axis(labelAngle=-35, labelLimit=140, labelFontSize=11, ticks=False))
            y = alt.Y(f"{value}:Q", title=unit, scale=alt.Scale(domain=[0, top * 1.2], nice=False), axis=alt.Axis(labelExpr=AXIS_EXPR, tickCount=5))
            bars = base.mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(x=x, y=y, color=col, tooltip=tip)
            txt = base.mark_text(align="center", dy=-8, fontSize=11, fontWeight="bold", color="#1d2b22").encode(x=x, y=y, text="Label:N")
            height = 290
        if select:
            pick = alt.selection_point(name="pick", fields=[label])
            bars = bars.add_params(pick).encode(opacity=alt.condition(pick, alt.value(1), alt.value(0.6)))
        ev = _draw((bars + txt).properties(height=height, padding=PAD), key=key, select=select)
        if select and ev is not None:
            sel = getattr(ev, "selection", None)
            if sel is None and isinstance(ev, dict):
                sel = ev.get("selection")
            items = (sel or {}).get("pick") or []
            return items[0].get(label) if items else None
    except Exception:
        bars_html(frame, label, value)
    return None


def bars_html(frame, label, value):
    bars(frame, label, value, fmt=lambda v: f"{v:,.0f}")


def legend_html(frame, label, value, colors):
    total = max(float(frame[value].sum()), 1e-9)
    items = "".join(f'<div class="lg"><span class="dot" style="background:{c}"></span><span class="ln">{html.escape(str(l))}</span>'
                    f'<span class="lv">{short(v)} · {float(v) / total * 100:.0f}%</span></div>'
                    for l, v, c in zip(frame[label], frame[value], colors))
    st.markdown(f'<div class="legend">{items}</div>', unsafe_allow_html=True)


def c_donut(frame, label, value, center=None, colors=None):
    """Donut in a fixed square (never clipped) with the legend written under it."""
    if frame is None or frame.empty or float(frame[value].sum()) <= 0:
        return _none()
    frame = frame[frame[value] > 0].reset_index(drop=True)
    cols = (colors or PALETTE)
    cols = [cols[i % len(cols)] for i in range(len(frame))]
    alt = _alt()
    try:
        rec = frame[[label, value]].copy()
        rec["Pct"] = rec[value] / rec[value].sum() * 100
        data = alt.Data(values=_recs(rec, [label, value, "Pct"]))
        arc = alt.Chart(data).mark_arc(innerRadius=68, outerRadius=108, stroke="white", strokeWidth=2).encode(
            theta=alt.Theta(f"{value}:Q", stack=True),
            color=alt.Color(f"{label}:N", scale=alt.Scale(domain=list(rec[label]), range=cols), legend=None),
            tooltip=[alt.Tooltip(f"{label}:N"), alt.Tooltip(f"{value}:Q", format=",.0f"), alt.Tooltip("Pct:Q", format=".1f", title="%")])
        ch = arc
        if center:
            ch = arc + alt.Chart(alt.Data(values=[{"t": center}])).mark_text(size=26, fontWeight="bold", color="#0f2a1d").encode(text="t:N")
        _draw(ch.properties(height=240, padding={"left": 10, "right": 10, "top": 10, "bottom": 10}))
        legend_html(frame, label, value, cols)
    except Exception:
        bars_html(frame, label, value)


def _long(frame, x, ys):
    return pd.DataFrame([{x: r[x], "Series": y, "Value": float(r[y])} for _, r in frame.iterrows() for y in ys])


def alt_scale(ys):
    alt = _alt()
    rng = [SERIES_COLORS.get(y, PALETTE[i % len(PALETTE)]) for i, y in enumerate(ys)]
    return alt.Scale(domain=list(ys), range=rng)


def c_lines(frame, x, ys, area=False, ytitle="Frw", xtitle="Month"):
    """Lines over the months, with the value written at each point (or at the last point when there are many)."""
    if frame is None or frame.empty:
        return _none()
    alt = _alt()
    try:
        lg = _long(frame, x, ys)
        lg["Label"] = [short(v) for v in lg.Value]
        n = frame[x].nunique()
        lg["Show"] = True if (len(ys) == 1 and n <= 14) else [i == n - 1 or False for i in lg.groupby("Series").cumcount()]
        top = max(float(lg.Value.max()), 1.0)
        data = alt.Data(values=_recs(lg, [x, "Series", "Value", "Label", "Show"]))
        base = alt.Chart(data).encode(
            x=alt.X(f"{x}:O", sort=None, title=xtitle, axis=alt.Axis(labelAngle=-35, labelFontSize=11)),
            y=alt.Y("Value:Q", title=ytitle, scale=alt.Scale(domain=[0, top * 1.18], nice=False), axis=alt.Axis(labelExpr=AXIS_EXPR, tickCount=5)),
            color=alt.Color("Series:N", scale=alt_scale(ys), legend=alt.Legend(title=None, orient="top")),
            tooltip=[alt.Tooltip(f"{x}:O", title=xtitle), alt.Tooltip("Series:N"), alt.Tooltip("Value:Q", format=",.0f", title="Frw")])
        layers = [base.mark_line(strokeWidth=3), base.mark_point(size=60, filled=True)]
        if area and len(ys) == 1:
            layers.insert(0, base.mark_area(opacity=0.15))
        layers.append(base.transform_filter("datum.Show").mark_text(dy=-11, fontSize=11, fontWeight="bold").encode(text="Label:N"))
        ch = layers[0]
        for extra in layers[1:]:
            ch = ch + extra
        _draw(ch.properties(height=290, padding=PAD))
    except Exception:
        st.caption("This chart could not be drawn.")


def c_grouped(frame, x, ys, stacked=False, ytitle="Frw", xtitle=None):
    """Several bars side by side (or stacked) for every x, with the values written on the bars."""
    if frame is None or frame.empty:
        return _none()
    alt = _alt()
    try:
        lg = _long(frame, x, ys)
        lg["Label"] = [short(v) if v else "" for v in lg.Value]
        top = float(lg.groupby(x).Value.sum().max()) if stacked else float(lg.Value.max())
        data = alt.Data(values=_recs(lg, [x, "Series", "Value", "Label"]))
        enc = dict(x=alt.X(f"{x}:N", sort=None, title=xtitle, axis=alt.Axis(labelAngle=-35, labelLimit=150, labelFontSize=11, ticks=False)),
                   y=alt.Y("Value:Q", title=ytitle, scale=alt.Scale(domain=[0, max(top, 1.0) * 1.18], nice=False),
                           axis=alt.Axis(labelExpr=AXIS_EXPR, tickCount=5)),
                   color=alt.Color("Series:N", scale=alt_scale(ys), legend=alt.Legend(title=None, orient="top")),
                   tooltip=[alt.Tooltip(f"{x}:N", title=xtitle or "Group"), alt.Tooltip("Series:N"), alt.Tooltip("Value:Q", format=",.0f")])
        if not stacked:
            enc["xOffset"] = alt.XOffset("Series:N")
        bars = alt.Chart(data).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(**enc)
        ch = bars
        if not stacked and frame[x].nunique() * len(ys) <= 24:
            ch = bars + alt.Chart(data).mark_text(dy=-7, fontSize=10, fontWeight="bold").encode(
                x=enc["x"], y=enc["y"], xOffset=alt.XOffset("Series:N"), text="Label:N")
        _draw(ch.properties(height=300, padding=PAD))
    except Exception:
        st.caption("This chart could not be drawn.")


def c_heat(frame, x, y, value):
    if frame is None or frame.empty:
        return _none()
    alt = _alt()
    try:
        rec = frame[[x, y, value]].copy()
        rec["Label"] = [short(v) for v in rec[value]]
        data = alt.Data(values=_recs(rec, [x, y, value, "Label"]))
        base = alt.Chart(data).encode(x=alt.X(f"{x}:O", sort=None, title=None, axis=alt.Axis(labelAngle=-35, orient="top")),
                                      y=alt.Y(f"{y}:N", title=None, axis=alt.Axis(labelLimit=240)))
        rect = base.mark_rect(cornerRadius=3).encode(color=alt.Color(f"{value}:Q", scale=alt.Scale(range=["#e8f3ec", "#0b6a35"]), legend=None),
                                                      tooltip=[alt.Tooltip(f"{x}:O"), alt.Tooltip(f"{y}:N"), alt.Tooltip(f"{value}:Q", format=",.0f")])
        txt = base.mark_text(fontSize=10).encode(text="Label:N", color=alt.condition(f"datum.{value} > {float(rec[value].max()) * 0.55}", alt.value("white"), alt.value("#1d2b22")))
        _draw((rect + txt).properties(height=max(160, 34 * rec[y].nunique() + 40), padding=PAD))
    except Exception:
        st.caption("This chart could not be drawn.")


from contextlib import contextmanager


@contextmanager
def card(title, sub="", data=None, key="x"):
    """A white card: bold title, grey one-line description, the chart, and a 'Download data (CSV)' button like the health dashboard."""
    with st.container(border=True):
        st.markdown(f'<div class="ct">{html.escape(title)}</div><div class="cs">{html.escape(sub)}</div>', unsafe_allow_html=True)
        yield
        if data is not None and len(data):
            st.download_button("Download data (CSV)", data.to_csv(index=False).encode("utf-8"), f"{norm(title) or 'data'}_{date.today()}.csv",
                               mime="text/csv", key=f"csv_{key}")


# ---------- dashboard data ----------
def month_series(frame, date_col, val_col):
    if frame is None or frame.empty:
        return pd.Series(dtype=float)
    return frame.assign(M=frame[date_col].astype(str).str[:7]).groupby("M")[val_col].sum()


def load_debts(field):
    d = df("""SELECT d.party AS "Who", d.direction AS "Direction", d.amount AS "Amount",
              COALESCE((SELECT SUM(p.amount) FROM debt_payments p WHERE p.debt_id=d.id), 0) AS "Paid",
              d.due_on AS "Due", COALESCE(s.name, '(no field)') AS "Field" FROM debts d LEFT JOIN sites s ON s.id=d.site_id""")
    if field != "All fields":
        d = d[d.Field == field]
    d = d.assign(Balance=d.Amount - d.Paid)
    return d[d.Balance > 0].reset_index(drop=True)


def load_taxes(field):
    t = df("""SELECT t.paid_on AS "Date", t.tax_type AS "Tax", t.amount AS "Amount", COALESCE(s.name, '(no field)') AS "Field"
              FROM taxes t LEFT JOIN sites s ON s.id=t.site_id""")
    return t if field == "All fields" else t[t.Field == field]


def top_other(frame, label, value, n=6):
    """Biggest n rows, everything else together as 'Other'."""
    g = frame.groupby(label, as_index=False)[value].sum().sort_values(value, ascending=False)
    if len(g) > n:
        rest = float(g[value].iloc[n:].sum())
        g = pd.concat([g.head(n), pd.DataFrame([{label: "Other", value: rest}])], ignore_index=True)
    return g


def dash_alerts(sites, exp, fnd, bal, mr, reqs, debts):
    """(level, text) lines for the 'Critical issue alerts' box."""
    out = []
    for s in sites.itertuples():
        spent, got = float(exp[exp.site_id == s.id].Total.sum()), float(fnd[fnd.site_id == s.id].Amount.sum())
        if got and spent > got:
            out.append(("High", f"{s.name}: spent is {money(spent - got)} Frw more than the funds received."))
        if (s.budget or 0) > 0 and spent >= 0.9 * s.budget:
            out.append(("High" if spent >= s.budget else "Medium", f"{s.name}: {spent / s.budget * 100:.0f}% of the budget is used."))
        last = exp[exp.site_id == s.id].Date.max() if len(exp[exp.site_id == s.id]) else None
        if s.status == "Active" and last and (date.today() - date.fromisoformat(last)).days >= 14:
            out.append(("Low", f"{s.name}: no expense recorded for {(date.today() - date.fromisoformat(last)).days} days."))
    out_stock = bal[bal.Balance <= 1e-9] if len(bal) else bal
    if len(out_stock):
        names = ", ".join(sorted(set(out_stock.Material))[:3])
        out.append(("Medium", f"{len(out_stock)} material line(s) out of stock ({names}{'…' if out_stock.Material.nunique() > 3 else ''})."))
    me = st.session_state.user
    pend = reqs[reqs.Status == "Pending"] if len(reqs) else reqs
    if has(CEO, ACCOUNTANT):
        pm = int(pend[pend["Requested by"] != me].batch.nunique()) if len(pend) else 0
        if pm:
            out.append(("Medium", f"{pm} material request(s) waiting for approval."))
    else:
        pm = int(pend[pend["Requested by"] == me].batch.nunique()) if len(pend) else 0
        if pm:
            out.append(("Low", f"{pm} of your material request(s) are waiting for approval."))
    pmr = int((mr.Status == "Pending").sum()) if len(mr) else 0
    if pmr and has(CEO):
        out.append(("Medium", f"{pmr} money request(s) waiting for your decision."))
    elif pmr:
        out.append(("Low", f"{pmr} of your money request(s) are waiting for the CEO."))
    ap = int(((mr.Status == "Approved") & (mr["Requested by"] != me)).sum()) if len(mr) and has(CEO, ACCOUNTANT) else 0
    if ap:
        out.append(("Low", f"{ap} approved money request(s) not paid yet."))
    if has(STORE) and len(reqs):
        dv = int((reqs.Status == "Approved").sum())
        if dv:
            out.append(("Medium", f"{dv} approved material line(s) waiting for you to record the delivery."))
    if len(debts):
        late = debts[(debts.Due < str(date.today())) & (debts.Direction == "We owe")]
        if len(late):
            out.append(("High", f"{len(late)} overdue debt(s) we owe, {money(late.Balance.sum())} Frw."))
    return out


def dash_deadlines(mr, reqs, debts):
    rows, today, soon = [], str(date.today()), str(date.today() + timedelta(days=30))
    for _, r in mr[mr.Status.isin(["Pending", "Approved"])].iterrows():
        if r["Needed by"] and r["Needed by"] <= soon:
            rows.append({"Due": r["Needed by"], "Type": "Money request", "Field": r.Field, "Detail": f"{r.Purpose} ({money(r.Requested)} Frw)", "Status": r.Status})
    if len(reqs):
        p = reqs[reqs.Status.isin(["Pending", "Approved"])].drop_duplicates("batch")
        for _, r in p.iterrows():
            if r["Needed by"] and r["Needed by"] <= soon:
                rows.append({"Due": r["Needed by"], "Type": "Material request", "Field": r.Field, "Detail": f"Request #{int(r.batch)}", "Status": r.Status})
    for _, r in debts.iterrows():
        if r.Due and r.Due <= soon:
            rows.append({"Due": r.Due, "Type": "Debt (" + r.Direction + ")", "Field": r.Field, "Detail": f"{r.Who}: {money(r.Balance)} Frw", "Status": "Overdue" if r.Due < today else "Open"})
    out = pd.DataFrame(rows, columns=["Due", "Type", "Field", "Detail", "Status"])
    return out.sort_values("Due").head(10)


def dash_recent(exp, fnd, mv, mr):
    rows = []
    for _, r in exp.head(8).iterrows():
        rows.append({"Date": r.Date, "What": "Expense", "Field": r.Field, "Detail": f"{r.Item} ({r.Category})", "Amount": r.Total, "By": r["Entered by"]})
    for _, r in fnd.head(5).iterrows():
        rows.append({"Date": r.Date, "What": "Funds received", "Field": r.Field, "Detail": str(r.Source) if pd.notna(r.Source) else "", "Amount": r.Amount, "By": r["Entered by"]})
    for _, r in mv.head(8).iterrows():
        rows.append({"Date": r.Date, "What": f"Material {str(r.Type).lower()}", "Field": r.Field, "Detail": f"{r.Material} ({abs(float(r.Qty)):g} {r.Unit})", "Amount": None, "By": r["Entered by"]})
    for _, r in mr.head(5).iterrows():
        rows.append({"Date": r.Date, "What": "Money request", "Field": r.Field, "Detail": f"{r.Purpose} - {r.Status}", "Amount": r.Requested, "By": r["Requested by"]})
    out = pd.DataFrame(rows, columns=["Date", "What", "Field", "Detail", "Amount", "By"])
    return out.sort_values("Date", ascending=False).head(10)


def dash_materials_tab(bal, mv, reqs):
    c1, c2 = st.columns(2)
    with c1:
        stat = pd.DataFrame({"Status": ["In stock", "Out of stock"], "Lines": [int((bal.Balance > 1e-9).sum()), int((bal.Balance <= 1e-9).sum())]}) if len(bal) else pd.DataFrame()
        with card("Stock status", "Material lines with and without balance", stat if len(stat) else None, "stat"):
            if len(stat):
                c_donut(stat, "Status", "Lines", colors=["#0b6a35", "#c9453b"])
            else:
                st.caption("No materials recorded yet.")
    with c2:
        top = pd.DataFrame()
        if len(bal):
            top = bal[bal.Balance > 1e-9].assign(Item=lambda d: d.Material + " (" + d.Unit.fillna("") + ")").sort_values("Balance", ascending=False).head(10)[["Item", "Balance"]]
        with card("Largest stock balances", "Top 10 materials in stock", top if len(top) else None, "topstock"):
            if len(top):
                c_bar(top, "Item", "Balance", unit="Quantity")
            else:
                st.caption("Nothing to show yet.")
    c1, c2 = st.columns(2)
    with c1:
        rcv = pd.DataFrame()
        if len(mv):
            by = mv[mv.Type.isin(["Received", "Used"])].assign(Month=lambda d: d.Date.astype(str).str[:7])
            if len(by):
                rcv = by.groupby(["Month", "Type"]).size().unstack(fill_value=0).reindex(columns=["Received", "Used"], fill_value=0).sort_index().tail(12)
                rcv.index = [month_label(x) for x in rcv.index]
                rcv = rcv.reset_index().rename(columns={"index": "Month"})
        with card("Materials received and used", "Number of movements by month", rcv if len(rcv) else None, "rcv"):
            if len(rcv):
                c_grouped(rcv, "Month", ["Received", "Used"], ytitle="Movements")
            else:
                st.caption("Nothing to show yet.")
    with c2:
        rs = reqs.groupby("Status", as_index=False).size().rename(columns={"size": "Lines"}) if len(reqs) else pd.DataFrame()
        with card("Material requests by status", "All requests sent by the field managers", rs if len(rs) else None, "reqst"):
            if len(rs):
                c_donut(rs, "Status", "Lines")
            else:
                st.caption("No material requests yet.")
    c1, c2 = st.columns(2)
    with c1:
        mr_top = reqs.groupby("Material", as_index=False).Requested.sum().sort_values("Requested", ascending=False).head(10) if len(reqs) else pd.DataFrame()
        with card("Most requested materials", "Total quantity requested · top 10", mr_top if len(mr_top) else None, "mreq"):
            if len(mr_top):
                c_bar(mr_top, "Material", "Requested", unit="Quantity", color="#2f6fb0")
            else:
                st.caption("No material requests yet.")
    with c2:
        bycat = pd.DataFrame()
        if len(bal):
            mats = df("SELECT id, COALESCE(category, 'Uncategorised') AS category FROM materials")
            inst = bal[bal.Balance > 1e-9].assign(Category=lambda d: d.material_id.map(dict(zip(mats.id, mats.category))).fillna("Uncategorised"))
            bycat = inst.groupby("Category", as_index=False).size().rename(columns={"size": "Lines"}).sort_values("Lines", ascending=False)
        with card("Materials in stock by category", "Number of material lines", bycat if len(bycat) else None, "matcat"):
            if len(bycat):
                c_bar(bycat, "Category", "Lines", unit="Lines")
            else:
                st.caption("Nothing to show yet.")


# ---------- CEO dashboard ----------
DASH_CSS = """
<style>
.dh{font-size:30px;font-weight:800;color:#0f2a1d;letter-spacing:.2px;margin:0 0 2px 0}
.dsub{color:#6b7a70;font-size:14px;margin-bottom:10px}
.dsec{font-size:13px;font-weight:800;letter-spacing:1px;color:#0b6a35;text-transform:uppercase;margin:22px 0 2px 0}
.dcap{color:#6b7a70;font-size:13px;margin:0 0 10px 0}
.kgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(185px,1fr));gap:14px;margin:6px 0 4px 0}
.kc{background:#eef7f1;border-radius:14px;border-top:5px solid #1b8a4b;padding:14px 14px 12px 14px;box-shadow:0 1px 4px rgba(0,0,0,.06);text-align:center;min-height:150px}
.kc.amber{background:#fff6e3;border-top-color:#e0922f}.kc.red{background:#fdecea;border-top-color:#c9453b}
.kc.blue{background:#eaf2fb;border-top-color:#2f6fb0}.kc.grey{background:#f4f5f4;border-top-color:#8a948d}
.kl{font-size:12.5px;font-weight:700;letter-spacing:.6px;color:#33423a;text-transform:uppercase}
.kv{font-size:30px;font-weight:800;color:#0f2a1d;line-height:1.15;margin:6px 0 2px 0;word-break:break-word}
.kv.sm{font-size:19px;line-height:1.3;margin:10px 0 6px 0}
.kc.red .kv{color:#a52a20}
.kd{font-size:12.5px;color:#5c6b62;margin-top:4px;line-height:1.3}
.kx{font-size:12.5px;font-weight:700;margin-top:2px}.kx.up{color:#c06a00}.kx.down{color:#1b8a4b}.kx.same{color:#2f6fb0}
.spark{display:block;margin:2px auto 0 auto}
.ins{background:#fff;border:1px solid #e3e8e5;border-radius:14px;padding:14px 18px;margin:4px 0 8px 0}
.ins .ir{display:flex;gap:12px;align-items:flex-start;padding:8px 0;border-bottom:1px solid #eef1ef;font-size:15px;color:#1d2b22}
.ins .ir:last-child{border-bottom:none}.ins .d{flex:0 0 12px;height:12px;border-radius:50%;margin-top:6px}
.ct{font-size:17px;font-weight:800;color:#0f2a1d}.cs{font-size:13px;color:#6b7a70;margin:0 0 8px 0}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;justify-content:center;margin-top:4px}
.lg{display:flex;align-items:center;gap:6px;font-size:13px;color:#1d2b22}
.lg .dot{width:11px;height:11px;border-radius:50%;display:inline-block}.lg .lv{color:#6b7a70}
</style>
"""


def spark_svg(values, labels, color="#1b8a4b"):
    """Tiny line with month names under it, like the cards of the health dashboard."""
    vals = [float(v) for v in values][-6:]
    labs = list(labels)[-6:]
    if len(vals) < 2:
        return ""
    w, h, pad = 170, 46, 14
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    xs = [pad + i * (w - 2 * pad) / (len(vals) - 1) for i in range(len(vals))]
    ys = [8 + (1 - (v - lo) / span) * (h - 22) for v in vals]
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}"/>' for x, y in zip(xs, ys))
    txt = "".join(f'<text x="{x:.1f}" y="{h + 6}" font-size="9.5" fill="#6b7a70" text-anchor="middle">{html.escape(str(l))}</text>' for x, l in zip(xs, labs))
    return (f'<svg class="spark" width="{w}" height="{h + 10}" viewBox="0 0 {w} {h + 10}"><polyline points="{pts}" fill="none" stroke="{color}" '
            f'stroke-width="2.2"/>{dots}{txt}</svg>')


def kpi(label, value, desc, tone="", spark="", delta=None, small=False):
    d = ""
    if delta:
        d = f'<div class="kx {delta[0]}">{html.escape(delta[1])}</div>'
    return (f'<div class="kc {tone}"><div class="kl">{html.escape(label)}</div><div class="kv{" sm" if small else ""}">{html.escape(str(value))}</div>'
            f'{spark}{d}<div class="kd">{html.escape(desc)}</div></div>')


def kpi_grid(cards):
    st.markdown(f'<div class="kgrid">{"".join(cards)}</div>', unsafe_allow_html=True)


def trend(cur, prev, prev_label, fmt=money):
    """('up'|'down'|'same', '▲ 12% vs Oct (1,200)') - or None when there is nothing to compare with."""
    if prev is None or prev <= 0:
        return None
    pct = (cur - prev) / prev * 100
    if abs(pct) < 0.5:
        return ("same", f"■ Same as {prev_label} ({fmt(prev)})")
    return ("up" if pct > 0 else "down", f"{'▲' if pct > 0 else '▼'} {abs(pct):.0f}% vs {prev_label} ({fmt(prev)})")


def monthly_vals(frame, col, months, how="sum"):
    if frame is None or frame.empty:
        return [0.0] * len(months)
    g = frame.assign(M=frame.Date.astype(str).str[:7]).groupby("M")[col]
    s = g.sum() if how == "sum" else g.size()
    return [float(s.get(m, 0.0)) for m in months]


def insights_html(rows):
    if not rows:
        rows = [("#1b8a4b", "Nothing needs your attention right now.")]
    inner = "".join(f'<div class="ir"><span class="d" style="background:{c}"></span><div>{t}</div></div>' for c, t in rows)
    st.markdown(f'<div class="ins">{inner}</div>', unsafe_allow_html=True)


RED, AMBER, GREEN = "#c9453b", "#e0922f", "#1b8a4b"


def dashboard_page():
    if not has(CEO, ACCOUNTANT, FIELD):
        return store_dashboard()
    user, field = st.session_state.user, st.session_state.get("ceo_field", "All fields")
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    can_funds, admin = has(*FUNDS_VISIBLE), has(CEO, ACCOUNTANT)
    ids = [int(i) for i in sites.id]
    exp, fnd, mv, reqs = load_expenses(ids), load_funds(ids), load_moves(ids), load_mat_requests(ids)
    if not can_funds:
        fnd = fnd.iloc[0:0]  # funds are for the CEO only
    mr, led = load_money_requests(), load_ledger()
    if field != "All fields":
        mr, led = mr[mr.Field == field], led[led.Field == field]
    mr, led = mr.reset_index(drop=True), led.reset_index(drop=True)
    debts, taxes = load_debts(field), load_taxes(field)
    if not admin:
        debts, taxes = debts.iloc[0:0], taxes.iloc[0:0]
    bal = stock_balances(mv)
    cats_all = categories_df().name.tolist()

    st.markdown(DASH_CSS, unsafe_allow_html=True)
    st.markdown(f'<div class="dh">{"Financial dashboard" if admin else "Dashboard"}</div><div class="dsub">{html.escape("All fields" if field == "All fields" else field)}'
                f' · all amounts in Frw · updates automatically when anything is recorded</div>', unsafe_allow_html=True)

    # ---- filters (the same four as the Excel dashboard) ----
    if len(exp):
        first, last = exp.Date.min()[:7], max(exp.Date.max()[:7], str(date.today())[:7])
        months, cur = [], date.fromisoformat(first + "-01")
        while str(cur)[:7] <= last:
            months.append(str(cur)[:7])
            cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    else:
        months = [str(date.today())[:7]]
    labels = [month_label(m) for m in months]
    options = ["All categories"] + cats_all
    saved = st.session_state.get("dash_cat", "All categories")
    saved = saved if saved in options else "All categories"
    f1, f2, f3, f4 = st.columns([2.4, 1.3, 1.3, 1.5])
    cat = f1.selectbox("Category", options, index=options.index(saved))
    st.session_state.dash_cat = cat
    a = f2.selectbox("From month", labels, index=0)
    b = f3.selectbox("To month", labels, index=len(labels) - 1)
    show = f4.selectbox("Show", ["Amount (Frw)", "Transactions"])
    by_count = show == "Transactions"
    i0, i1 = sorted([labels.index(a), labels.index(b)])
    period = months[i0:i1 + 1]
    inp = exp[(exp.Date.str[:7] >= period[0]) & (exp.Date.str[:7] <= period[-1])]
    sel = inp if cat == "All categories" else inp[inp.Category == cat]
    st.markdown(f'<div class="dcap">Showing: <b>{html.escape(cat if cat != "All categories" else "All categories")}</b> | '
                f'{labels[i0]} to {labels[i1]}</div>', unsafe_allow_html=True)

    # ---- numbers ----
    spent_all, funds_all = float(exp.Total.sum()), float(fnd.Amount.sum())
    sel_total, sel_n = float(sel.Total.sum()), len(sel)
    mon_sum, mon_cnt = monthly_vals(sel, "Total", period), monthly_vals(sel, "Total", period, "count")
    mon_lab = [month_label(m, False) for m in period]
    prev_lab = month_label(period[-2], False) if len(period) > 1 else ""
    by_cat_period = inp.groupby("Category").Total.sum().sort_values(ascending=False)
    top_cat = by_cat_period.index[0] if len(by_cat_period) else "-"
    big = sel.loc[sel.Total.idxmax()] if sel_n else None

    st.markdown('<div class="dsec">Selected category and period</div><div class="dcap">Changes with the filters above</div>', unsafe_allow_html=True)
    kpi_grid([
        kpi("Total spend", money(sel_total), "Frw spent in the selection", "green", spark_svg(mon_sum, mon_lab),
            trend(mon_sum[-1], mon_sum[-2], prev_lab) if len(mon_sum) > 1 else None),
        kpi("Transactions", f"{sel_n:,}", "entries recorded", "blue", spark_svg(mon_cnt, mon_lab, "#2f6fb0"),
            trend(mon_cnt[-1], mon_cnt[-2], prev_lab, lambda v: f"{v:,.0f}") if len(mon_cnt) > 1 else None),
        kpi("Average per transaction", money(sel_total / sel_n) if sel_n else "0", "Frw for one entry", "green"),
        kpi("Largest transaction", money(big.Total) if big is not None else "0",
            ("Frw · " + str(big.Item)[:34]) if big is not None else "Frw", "amber"),
        kpi("Share of project spend", f"{sel_total / spent_all * 100:.1f}%" if spent_all else "0%", "of all recorded cost", "grey"),
        kpi("Top category in period", top_cat, f"by amount · {money(by_cat_period.iloc[0])} Frw" if len(by_cat_period) else "by amount", "grey", small=True),
    ])

    st.markdown('<div class="dsec">Whole project</div><div class="dcap">Not affected by the filters</div>', unsafe_allow_html=True)
    balance = funds_all - spent_all
    used_pct = spent_all / funds_all * 100 if funds_all else None
    big_cat = exp.groupby("Category").Total.sum().sort_values(ascending=False)
    if can_funds:
        kpi_grid([
            kpi("Funds received", money(funds_all), "recorded in Funds received", "green"),
            kpi("Recorded expenditure", money(spent_all), f"all {exp.Category.nunique()} categories used", "amber"),
            kpi("Balance / (shortfall)", ("-" if balance < 0 else "") + money(abs(balance)), "funds − expenditure", "red" if balance < 0 else "green"),
            kpi("Funds used", f"{used_pct:.1f}%" if used_pct is not None else "no funds", "expenditure ÷ funds",
                "red" if (used_pct or 0) >= 100 else "amber" if (used_pct or 0) >= 90 else "green"),
            kpi("Biggest cost category", big_cat.index[0] if len(big_cat) else "-", f"all time · {money(big_cat.iloc[0])} Frw" if len(big_cat) else "all time", "grey", small=True),
            kpi("Last transaction", exp.Date.max() if len(exp) else "-", "latest date recorded", "grey"),
        ])
    else:
        budgeted0 = sites[sites.budget.fillna(0) > 0]
        bud0 = float(budgeted0.budget.sum())
        used0 = float(exp[exp.site_id.isin(budgeted0.id)].Total.sum()) / bud0 * 100 if bud0 else None
        kpi_grid([
            kpi("Recorded expenditure", money(spent_all), f"all {exp.Category.nunique()} categories used", "amber"),
            kpi("Budget", money(bud0) if bud0 else "no budget", "Frw set for the field" if bud0 else "the CEO sets it in Fields & categories", "blue"),
            kpi("Budget used", f"{used0:.1f}%" if used0 is not None else "-", "of the budget",
                "red" if (used0 or 0) >= 100 else "amber" if (used0 or 0) >= 90 else "green"),
            kpi("Entries recorded", f"{len(exp):,}", "all time", "green"),
            kpi("Biggest cost category", big_cat.index[0] if len(big_cat) else "-", f"all time · {money(big_cat.iloc[0])} Frw" if len(big_cat) else "all time", "grey", small=True),
            kpi("Last transaction", exp.Date.max() if len(exp) else "-", "latest date recorded", "grey"),
        ])

    pend_mat = int(reqs[reqs.Status == "Pending"].batch.nunique()) if len(reqs) else 0
    pend_money = int((mr.Status == "Pending").sum()) if len(mr) else 0
    out_n = int((bal.Balance <= 1e-9).sum()) if len(bal) else 0
    biz = float(led[led.Kind == "Business"].Amount.sum()) if len(led) else 0.0
    we_owe = float(debts[debts.Direction == "We owe"].Balance.sum()) if len(debts) else 0.0
    owed_us = float(debts[debts.Direction == "Owed to us"].Balance.sum()) if len(debts) else 0.0
    budgeted = sites[sites.budget.fillna(0) > 0]
    budget = float(budgeted.budget.sum())
    spent_budgeted = float(exp[exp.site_id.isin(budgeted.id)].Total.sum())
    bpct = spent_budgeted / budget * 100 if budget else None
    st.markdown('<div class="dsec">Operations</div><div class="dcap">Approvals, stock, money and budget</div>', unsafe_allow_html=True)
    me = user
    mine_mat = int(reqs[(reqs.Status == "Pending") & (reqs["Requested by"] == me)].batch.nunique()) if len(reqs) else 0
    mine_money = int(((mr.Status == "Pending") & (mr["Requested by"] == me)).sum()) if len(mr) else 0
    bcard = kpi("Budget used", f"{bpct:.1f}%" if bpct is not None else "no budget", "of the budget set for the field" if bpct is not None else "no budget set for the field",
                "red" if (bpct or 0) >= 100 else "amber" if (bpct or 0) >= 90 else "green")
    if has(CEO):
        kpi_grid([
            kpi("Waiting for approval", pend_mat + pend_money, f"{pend_mat} material · {pend_money} money requests", "amber" if pend_mat + pend_money else "green"),
            kpi("Materials out of stock", out_n, "material lines with no balance", "red" if out_n else "green"),
            kpi("Money used (business)", money(biz), "Frw recorded in Money used", "blue"),
            kpi("We owe (open)", money(we_owe), "unpaid debts", "amber" if we_owe else "green"),
            kpi("Owed to us (open)", money(owed_us), "money others must pay us", "blue"),
            bcard])
    elif has(ACCOUNTANT):
        to_ok = int(reqs[(reqs.Status == "Pending") & (reqs["Requested by"] != me)].batch.nunique()) if len(reqs) else 0
        to_pay = int(((mr.Status == "Approved") & (mr["Requested by"] != me)).sum()) if len(mr) else 0
        kpi_grid([
            kpi("Material requests to approve", to_ok, "waiting for your approval", "amber" if to_ok else "green"),
            kpi("Money requests to pay", to_pay, "approved by the CEO, not paid yet", "amber" if to_pay else "green"),
            kpi("Materials out of stock", out_n, "material lines with no balance", "red" if out_n else "green"),
            kpi("We owe (open)", money(we_owe), "unpaid debts", "amber" if we_owe else "green"),
            kpi("Owed to us (open)", money(owed_us), "money others must pay us", "blue"),
            bcard])
    else:
        kpi_grid([
            kpi("My material requests", mine_mat, "waiting for approval", "amber" if mine_mat else "green"),
            kpi("My money requests", mine_money, "waiting for the CEO", "amber" if mine_money else "green"),
            kpi("Materials out of stock", out_n, "material lines with no balance", "red" if out_n else "green"),
            kpi("Money used (mine)", money(biz), "Frw recorded in Money used", "blue"),
            bcard])

    # ---- what the data says ----
    says = []
    if len(by_cat_period) and by_cat_period.sum() > 0:
        share = by_cat_period.iloc[0] / by_cat_period.sum() * 100
        says.append((AMBER if share >= 50 else GREEN, f"<b>Biggest cost:</b> {html.escape(str(top_cat))} is {share:.0f}% of the spending in this period ({money(by_cat_period.iloc[0])} Frw)."))
    if len(period) > 1 and mon_sum[-2] > 0:
        pc = (mon_sum[-1] - mon_sum[-2]) / mon_sum[-2] * 100
        says.append((AMBER if pc > 25 else GREEN, f"<b>Spending:</b> {month_label(period[-1])} is {abs(pc):.0f}% {'higher' if pc > 0 else 'lower'} than {month_label(period[-2])} ({money(mon_sum[-1])} against {money(mon_sum[-2])} Frw)."))
    if funds_all:
        says.append((RED, f"<b>Funds:</b> spending is {money(-balance)} Frw more than the funds received.") if balance < 0
                    else (GREEN, f"<b>Funds:</b> {money(balance)} Frw of the funds received is still available ({100 - used_pct:.0f}%)."))
    for lvl, text in dash_alerts(sites, exp, fnd, bal, mr, reqs, debts)[:6]:
        if text.startswith(tuple(s.name for s in sites.itertuples())) and "funds received" in text:
            continue  # already said above
        says.append((RED if lvl == "High" else AMBER if lvl == "Medium" else "#2f6fb0", html.escape(text)))
    st.markdown('<div class="dsec">What the data says</div>', unsafe_allow_html=True)
    insights_html(says[:8])

    # ---- charts ----
    st.markdown('<div class="dsec">Analysis</div><div class="dcap">Hover a chart for the exact value. Click a bar in the category ranking to filter the whole page. '
                'Every chart has a Download data button.</div>', unsafe_allow_html=True)
    t_sp, t_fin, t_mat, t_time, t_money, t_act = st.tabs(["Spending", "Financial", "Materials", "Time spent", "Money, taxes & debts" if admin else "Money", "Activity"])
    metric = "Transactions" if by_count else "Amount"
    unit = "entries" if by_count else "Frw"

    with t_sp:
        rank = inp.groupby("Category", as_index=False).agg(Amount=("Total", "sum"), Transactions=("Total", "size"))
        rank["Value"] = rank["Transactions" if by_count else "Amount"]
        rank = rank.sort_values("Value", ascending=False).reset_index(drop=True)
        rank["Share %"] = (rank.Value / max(rank.Value.sum(), 1e-9) * 100).round(1)
        c1, c2 = st.columns(2)
        with c1:
            with card("Category ranking", f"{labels[i0]} to {labels[i1]} · ranked by {metric.lower()} · click a bar to select it", rank[["Category", "Amount", "Transactions", "Share %"]], "rank"):
                if rank.empty:
                    st.caption("No spending in this period.")
                else:
                    clicked = c_bar(rank.rename(columns={"Value": "Value"})[["Category", "Value"]], "Category", "Value", highlight=cat if cat != "All categories" else None,
                                    unit=unit, key="dash_rank", select=True)
                    last = st.session_state.get("dash_last_click")
                    if clicked != last:
                        st.session_state.dash_last_click = clicked
                        st.session_state.dash_cat = clicked if clicked else "All categories"
                        st.rerun()
        with c2:
            with card("Share of spending", f"Category share of the {metric.lower()} in the period", rank[["Category", "Value", "Share %"]], "share"):
                c_donut(top_other(rank.rename(columns={"Value": "V"}), "Category", "V", 7), "Category", "V",
                        center=short(rank.Value.sum()) if len(rank) else None)
        c1, c2 = st.columns(2)
        with c1:
            ms = pd.DataFrame({"Month": [month_label(m) for m in period], "Value": mon_cnt if by_count else mon_sum})
            with card(f"{metric} by month", f"{cat if cat != 'All categories' else 'All categories'} · {labels[i0]} to {labels[i1]}", ms, "bymonth"):
                c_bar(ms, "Month", "Value", horizontal=False, unit=unit, xtitle="Month")
        with c2:
            cm = pd.concat({"Spent (cumulative)": month_series(exp, "Date", "Total"), "Funds received": month_series(fnd, "Date", "Amount")}, axis=1)
            if len(cm):
                cm = cm.fillna(0).sort_index().cumsum().reset_index()
                cm.columns = ["Month"] + list(cm.columns[1:])
                cm["Month"] = [month_label(m) for m in cm.Month]
            with card("Cumulative spend vs funds received" if can_funds else "Cumulative spending", "Whole project · when the spending line passes the funds line, money is short" if can_funds else "Whole project · month by month", cm if len(cm) else None, "cum"):
                if len(cm):
                    c_lines(cm, "Month", [c for c in ["Spent (cumulative)", "Funds received"] if c in cm.columns])
                else:
                    st.caption("Nothing to show yet.")
        with card("Category ranking table", f"{labels[i0]} to {labels[i1]}", None, "ranktbl"):
            tbl = rank[["Category", "Amount", "Transactions", "Share %"]].copy()
            tbl.insert(0, "Rank", range(1, len(tbl) + 1))
            wide(st.dataframe, pretty(tbl, ["Amount"]), hide_index=True)
            if len(tbl):
                st.download_button("Download data (CSV)", tbl.to_csv(index=False).encode("utf-8"), f"category_ranking_{date.today()}.csv", mime="text/csv", key="csv_ranktbl")

    with t_fin:
        c1, c2 = st.columns(2)
        with c1:
            m = pd.concat({"Spent": month_series(exp, "Date", "Total"), "Funds received": month_series(fnd, "Date", "Amount")}, axis=1)
            m = m.fillna(0).sort_index().tail(12).reset_index() if len(m) else m
            if len(m):
                m.columns = ["Month"] + list(m.columns[1:])
                m["Month"] = [month_label(x) for x in m.Month]
            with card("Spent and funds received by month" if can_funds else "Spending by month", "Last 12 months · whole project", m if len(m) else None, "spf"):
                if len(m):
                    c_grouped(m, "Month", [c for c in ["Spent", "Funds received"] if c in m.columns])
                else:
                    st.caption("Nothing to show yet.")
        with c2:
            stk = pd.DataFrame()
            if len(sel):
                topc = top_other(sel, "Category", "Total", 5).Category.tolist()
                s2 = sel.assign(Month=sel.Date.str[:7], Cat=sel.Category.where(sel.Category.isin(topc), "Other"))
                stk = s2.pivot_table(index="Month", columns="Cat", values="Total", aggfunc="sum", fill_value=0).sort_index().tail(12)
                stk.index = [month_label(x) for x in stk.index]
                stk = stk.reset_index().rename(columns={"index": "Month"})
            with card("Spending by category and month", "Stacked · top 5 categories, the rest together as Other", stk if len(stk) else None, "stk"):
                if len(stk):
                    c_grouped(stk, "Month", [c for c in stk.columns if c != "Month"], stacked=True)
                else:
                    st.caption("No spending in this selection.")
        c1, c2 = st.columns(2)
        with c1:
            items = sel.groupby("Item", as_index=False).Total.sum().sort_values("Total", ascending=False).head(10)
            with card("Biggest items", "Top 10 items by amount in the selection", items, "items"):
                c_bar(items, "Item", "Total", unit="Frw")
        with c2:
            avg = sel.groupby("Category", as_index=False).Total.mean().sort_values("Total", ascending=False).head(10)
            avg["Total"] = avg.Total.round(0)
            with card("Average transaction by category", "Frw for one entry · top 10", avg, "avg"):
                c_bar(avg, "Category", "Total", unit="Frw", color="#2f6fb0")
        if field == "All fields" and len(sites) > 1:
            per = sites[["id", "name", "budget"]].rename(columns={"name": "Field"}).copy()
            per["Funds received"] = per.id.map(fnd.groupby("site_id").Amount.sum()).fillna(0)
            per["Spent"] = per.id.map(exp.groupby("site_id").Total.sum()).fillna(0)
            per = per.sort_values("Spent", ascending=False).head(10)
            cols_ = ["Funds received", "Spent"] if can_funds else ["Spent"]
            with card("Funds received and spent by field" if can_funds else "Spending by field", "Compare the fields side by side", per[["Field"] + cols_], "perfield"):
                c_grouped(per, "Field", cols_)

    with t_mat:
        dash_materials_tab(bal, mv, reqs)

    with t_time:
        c1, c2 = st.columns(2)
        with c1:
            tm = pd.DataFrame({"Month": [month_label(m) for m in period], "Spent": mon_sum})
            with card("Spending over time", f"Month by month · {labels[i0]} to {labels[i1]}", tm, "time"):
                c_lines(tm, "Month", ["Spent"], area=True)
        with c2:
            wd = pd.DataFrame()
            if len(sel):
                order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                wd = sel.assign(Day=pd.to_datetime(sel.Date, errors="coerce").dt.day_name()).groupby("Day").Total.sum().reindex(order).fillna(0).reset_index()
            with card("Spending by day of the week", "Which days the money goes out", wd if len(wd) else None, "wday"):
                if len(wd):
                    c_bar(wd, "Day", "Total", horizontal=False, unit="Frw")
                else:
                    st.caption("Nothing to show yet.")
        hm = pd.DataFrame()
        if len(sel):
            hm = sel.assign(Month=sel.Date.str[:7]).groupby(["Month", "Category"], as_index=False).Total.sum().sort_values("Month")
            hm = hm[hm.Month.isin(sorted(hm.Month.unique())[-12:])]
            hm["Month"] = [month_label(x) for x in hm.Month]
        with card("Heat map: category by month", "Darker means more money", hm if len(hm) else None, "heat"):
            if len(hm):
                c_heat(hm, "Month", "Category", "Total")
            else:
                st.caption("Nothing to show yet.")
        c1, c2 = st.columns(2)
        with c1:
            cnt = pd.DataFrame({"Month": [month_label(m) for m in period], "Transactions": mon_cnt})
            with card("Number of transactions by month", "How busy each month was", cnt, "cnt"):
                c_bar(cnt, "Month", "Transactions", horizontal=False, unit="entries", color="#2f6fb0")
        with c2:
            lastd = pd.DataFrame()
            if len(exp):
                lastd = exp.groupby("Field").Date.max().reset_index()
                lastd["Days"] = [(date.today() - date.fromisoformat(d)).days for d in lastd.Date]
            with card("Days since the last entry", "By field · a high number means nobody is recording", lastd[["Field", "Days"]] if len(lastd) else None, "since"):
                if len(lastd):
                    c_bar(lastd[["Field", "Days"]], "Field", "Days", unit="Days", color="#8e5ea2")
                else:
                    st.caption("Nothing to show yet.")

    with t_money:
        c1, c2 = st.columns(2)
        bizl = led[led.Kind == "Business"] if len(led) else led
        with c1:
            mu = bizl.groupby("Category", as_index=False).Amount.sum().sort_values("Amount", ascending=False).head(10) if len(bizl) else pd.DataFrame()
            with card("Money used by category", "Business spending · top 10", mu if len(mu) else None, "mu"):
                if len(mu):
                    c_bar(mu, "Category", "Amount", unit="Frw", color="#8e5ea2")
                else:
                    st.caption("Nothing recorded yet.")
        with c2:
            kd = led.groupby("Kind", as_index=False).Amount.sum() if len(led) else pd.DataFrame()
            with card("Business and personal", "Split of the money used", kd if len(kd) else None, "kind"):
                if len(kd):
                    c_donut(kd, "Kind", "Amount", colors=["#0b6a35", "#e0922f"])
                else:
                    st.caption("Nothing recorded yet.")
        c1, c2 = st.columns(2)
        with c1:
            ml = month_series(led, "Date", "Amount")
            mdf = ml.sort_index().tail(12).rename("Money used").reset_index().rename(columns={"M": "Month"}) if len(ml) else pd.DataFrame()
            if len(mdf):
                mdf["Month"] = [month_label(x) for x in mdf.Month]
            with card("Money used by month", "Last 12 months", mdf if len(mdf) else None, "mum"):
                if len(mdf):
                    c_lines(mdf, "Month", ["Money used"], area=True)
                else:
                    st.caption("Nothing recorded yet.")
        with c2:
            ms_ = mr.groupby("Status", as_index=False).size().rename(columns={"size": "Requests"}) if len(mr) else pd.DataFrame()
            with card("Money requests by status", "Requests sent by the field managers", ms_ if len(ms_) else None, "mrs"):
                if len(ms_):
                    c_donut(ms_, "Status", "Requests")
                else:
                    st.caption("No money requests yet.")
        if admin:
            c1, c2 = st.columns(2)
            with c1:
                tx = taxes.groupby("Tax", as_index=False).Amount.sum().sort_values("Amount", ascending=False) if len(taxes) else pd.DataFrame()
                with card("Taxes paid by type", "All recorded tax payments", tx if len(tx) else None, "tax"):
                    if len(tx):
                        c_bar(tx, "Tax", "Amount", unit="Frw", color="#2f6fb0")
                    else:
                        st.caption("No tax payments yet.")
            with c2:
                db = debts.groupby("Direction", as_index=False).Balance.sum() if len(debts) else pd.DataFrame()
                with card("Open debts", "What we owe and what is owed to us", db if len(db) else None, "debt"):
                    if len(db):
                        c_donut(db, "Direction", "Balance", colors=["#c9453b", "#0b6a35"])
                    else:
                        st.caption("No open debts.")

    with t_act:
        c1, c2 = st.columns(2)
        with c1:
            rec = dash_recent(exp, fnd, mv, mr)
            with card("Recent updates", "The latest things recorded", rec if len(rec) else None, "recent"):
                if len(rec):
                    wide(st.dataframe, pretty(rec, ["Amount"]), hide_index=True)
                else:
                    st.caption("Nothing recorded yet.")
        with c2:
            dl = dash_deadlines(mr, reqs, debts)
            with card("Upcoming deadlines", "Next 30 days · requests and debts", dl if len(dl) else None, "dead"):
                if len(dl):
                    wide(st.dataframe, dl, hide_index=True)
                else:
                    st.caption("Nothing due soon.")


# ---------- CEO: material stock ----------
def stock_sheet(mv, reqs):
    """One row per field and material: date, category, item, in stock, received, used, requested, by."""
    bal = stock_balances(mv)
    mats = df("SELECT id, COALESCE(category, 'Uncategorised') AS category FROM materials")
    cat_of = dict(zip(mats.id, mats.category))
    req = pd.Series(dtype=float)
    if len(reqs):
        r = reqs[reqs.Status != "Rejected"]
        req = r.groupby(["site_id", "material_id"]).Requested.sum()
    last = mv.sort_values(["Date"], ascending=False, kind="stable").drop_duplicates(["site_id", "material_id"]) if len(mv) else mv
    last_d = {(int(a), int(b)): (d, by) for a, b, d, by in zip(last.site_id, last.material_id, last.Date, last["Entered by"])} if len(last) else {}
    keys = {(int(a), int(b)) for a, b in zip(bal.site_id, bal.material_id)} if len(bal) else set()
    keys |= {(int(a), int(b)) for a, b in req.index}
    names = {}
    for src in (bal, reqs):
        if len(src):
            for a, b, f, m, u in zip(src.site_id, src.material_id, src.Field, src.Material, src.Unit):
                names[(int(a), int(b))] = (f, m, u)
    brow = {(int(a), int(b)): (x, y, z) for a, b, x, y, z in zip(bal.site_id, bal.material_id, bal.Balance, bal.Received, bal.Used)} if len(bal) else {}
    rows = []
    for key in sorted(keys, key=lambda k: (names[k][0], names[k][1])):
        f, m, u = names[key]
        b, rcv, used = brow.get(key, (0.0, 0.0, 0.0))
        d, by = last_d.get(key, ("", ""))
        rows.append({"Date": d, "Field": f, "Category": cat_of.get(key[1], "Uncategorised"), "Item": m, "Unit": u or "",
                     "In stock": float(b), "Received": float(rcv), "Used": float(used), "Requested": float(req.get(key, 0.0)), "By": by})
    return pd.DataFrame(rows, columns=["Date", "Field", "Category", "Item", "Unit", "In stock", "Received", "Used", "Requested", "By"])


def stock_page():
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    ids = [int(i) for i in sites.id]
    field = st.session_state.get("ceo_field", "All fields")
    mv, reqs = load_moves(ids), load_mat_requests(ids)
    bal = stock_balances(mv)
    recent = str(date.today() - timedelta(days=30))
    st.markdown(f"**Field:** {field}")
    k = st.columns(4)
    k[0].metric("Materials in stock", int((bal.Balance > 1e-9).sum()) if len(bal) else 0)
    k[1].metric("Out of stock", int((bal.Balance <= 1e-9).sum()) if len(bal) else 0)
    k[2].metric("Received (30 days)", int(((mv.Type == "Received") & (mv.Date >= recent)).sum()) if len(mv) else 0)
    k[3].metric("Used (30 days)", int(((mv.Type == "Used") & (mv.Date >= recent)).sum()) if len(mv) else 0)

    st.subheader("Stock balance")
    sheet = stock_sheet(mv, reqs)
    if sheet.empty:
        st.info("No materials have been recorded yet.")
    else:
        a, b = st.columns([2, 3])
        cat = a.selectbox("Category", ["All categories"] + sorted(set(sheet.Category)), key="cs_cat")
        with b:
            q = search_box("cs_q", "Search material", "Type part of a material name…")
        view = sheet if cat == "All categories" else sheet[sheet.Category == cat]
        view = match(view, ["Item", "By"], q)
        cols = ["Date"] + (["Field"] if field == "All fields" else []) + ["Category", "Item", "Unit", "In stock", "Received", "Used", "Requested", "By"]
        shown = view[cols].reset_index(drop=True)
        shown.insert(0, "No", range(1, len(shown) + 1))
        st.caption(f"{len(shown)} materials")
        wide(st.dataframe, pretty(shown, qty_cols=["In stock", "Received", "Used", "Requested"]), hide_index=True)
        st.download_button(":material/download: Download Excel", to_excel({"Stock balance": shown}),
                           f"material_stock_{date.today()}.xlsx", mime=XLSX, key="dl_ceo_stock")
        if has(CEO, ACCOUNTANT, STORE):
            with st.expander("Set the category of a material"):
                st.caption("Categories are the same as the cost workbook. Add a new category in Fields & categories.")
                mats = df("SELECT id, name, unit, COALESCE(category, '') AS category FROM materials ORDER BY name")
                opts = categories_df().name.tolist()
                tbl = mats.rename(columns={"name": "Item", "unit": "Unit", "category": "Category"}).drop(columns="id")
                tbl["Category"] = tbl["Category"].where(tbl["Category"] != "", None)
                ed = wide(st.data_editor, tbl, hide_index=True, key=f"{K('mc')}_{len(mats)}", disabled=["Item", "Unit"], column_config={
                    "Category": st.column_config.SelectboxColumn("Category", options=opts, required=False)})
                if st.button("Save categories", key="mc_save"):
                    with conn() as c:
                        for i, cat_ in zip(mats.id, ed["Category"]):
                            c.execute("UPDATE materials SET category=? WHERE id=?", (cat_ if isinstance(cat_, str) and cat_ else None, int(i)))
                    done("Categories saved.")

    st.subheader("Movements")
    m1, m2 = st.columns([2, 3])
    kind = m1.radio("Show", ["All", "Received", "Used", "Count"], horizontal=True, key="cs_kind")
    with m2:
        q2 = search_box("cs_q2", "Search material, note or person", "Type part of a name…")
    mvs = mv if kind == "All" else mv[mv.Type == kind]
    mvs = match(mvs, ["Material", "Detail", "Entered by"], q2)
    st.caption(f"{len(mvs)} movements" + (" (showing the latest 300)" if len(mvs) > 300 else ""))
    mcols = ["Date", "Field", "Material", "Unit", "Type", "Qty", "Detail", "Entered by"]
    shown = mvs.head(300)[mcols].reset_index(drop=True)
    shown.insert(0, "No", range(1, len(shown) + 1))
    wide(st.dataframe, pretty(shown, qty_cols=["Qty"]), hide_index=True)
    if len(shown):
        st.download_button(":material/download: Download Excel ", to_excel({"Movements": shown}), f"material_movements_{date.today()}.xlsx",
                           mime=XLSX, key="dl_ceo_mv")


# ---------- CEO: material requests (he approves and adds; he does not request) ----------
def matreq_all(reqs):
    if reqs.empty:
        st.caption("No requests yet.")
    else:
        c1, c2 = st.columns([2, 3])
        status = c1.radio("Show", ["All", "Pending", "Approved", "Delivered", "Rejected"], horizontal=True, key="cr_st")
        with c2:
            q = search_box("cr_q", "Search material, field or person", "Type part of a name…")
        view = reqs if status == "All" else reqs[reqs.Status == status]
        view = match(view, ["Material", "Field", "Requested by", "Decided by"], q)
        cols = ["batch", "Date", "Field", "Material", "Unit", "Requested", "Approved", "Delivered", "Status", "Needed by", "Requested by", "Decided by", "Reason"]
        shown = view[cols].rename(columns={"batch": "Request #"}).reset_index(drop=True)
        shown.insert(0, "No", range(1, len(shown) + 1))
        st.caption(f"{len(shown)} lines")
        wide(st.dataframe, pretty(shown, status_col="Status", qty_cols=["Requested", "Approved", "Delivered"]), hide_index=True)
        if len(shown):
            st.download_button(":material/download: Download Excel", to_excel({"Material requests": shown}),
                               f"material_requests_{date.today()}.xlsx", mime=XLSX, key="dl_ceo_req")


def matreq_ceo():
    user, uid = st.session_state.user, st.session_state.uid
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    ids = [int(i) for i in sites.id]
    reqs = load_mat_requests(ids)
    waiting = int(reqs[reqs.Status == "Pending"].batch.nunique()) if len(reqs) else 0
    t1, t2, t3 = st.tabs([f"Waiting for approval ({waiting})", "Add materials (Excel sheet)", "All requests"])
    with t1:
        section_approve(reqs, sites.iloc[:1], user, True)  # the Field drop-down at the top already chooses the field
    with t2:
        st.caption("You do not send requests. Add the materials directly in the sheet of their category: the same table as the workbook.")
        with st.expander("Add rows", expanded=True):
            ceo_add_cost_rows(sites, "cmx")
        exp = load_expenses(ids)
        st.subheader("Category sheets")
        a, b = st.columns([2, 3])
        cats = categories_df().name.tolist()
        cat = a.selectbox("Category", ["All categories"] + cats, key="cm_cat")
        with b:
            q = search_box("cm_q", "Search item", "Type part of an item name…")
        view = exp if cat == "All categories" else exp[exp.Category == cat]
        view = cost_frame(match(view, ["Item"], q))
        extras = (["Category"] if cat == "All categories" else []) + (["Field"] if len(sites) > 1 else []) + ["Entered by"]
        ticked = sheet_view(view, "cmv", extras, removable=True)
        c1, c2 = st.columns(2)
        with c1:
            if len(view):
                download_workbook(view, "materials", "cm_wb")
        with c2:
            if ticked and st.button("Delete ticked rows", key="cm_del"):
                with conn() as c:
                    for rid in ticked:
                        c.execute("DELETE FROM expenses WHERE id=?", (rid,))
                done(f"{len(ticked)} row(s) deleted.")
    with t3:
        matreq_all(reqs)


# ---------- CEO: expenses ----------
def expenses_page():
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    ids = [int(i) for i in sites.id]
    with st.expander("Add rows (Excel sheet)", expanded=True):
        st.caption("Choose the category, then write the rows: Date, Item, Qty, P/U. The total is Qty x P/U.")
        ceo_add_cost_rows(sites, "cxe")
    exp = load_expenses(ids)
    st.subheader("Category sheets")
    a, b, c, d = st.columns([2, 2, 1.5, 1.5])
    cat = a.selectbox("Category", ["All categories"] + categories_df().name.tolist(), key="ce_cat")
    with b:
        q = search_box("ce_q", "Search item or person", "Type part of an item name…")
    lo = date.fromisoformat(exp.Date.min()) if len(exp) else date.today()
    hi = max(date.fromisoformat(exp.Date.max()), date.today()) if len(exp) else date.today()
    fk = st.session_state.get("ceo_field", "All fields")
    d_from, d_to = c.date_input("From", lo, key=f"ce_from_{fk}"), d.date_input("To", hi, key=f"ce_to_{fk}")
    view = exp[(exp.Date >= str(d_from)) & (exp.Date <= str(d_to))]
    if cat != "All categories":
        view = view[view.Category == cat]
    view = cost_frame(match(view, ["Item", "Entered by"], q))
    extras = (["Category"] if cat == "All categories" else []) + (["Field"] if len(sites) > 1 else []) + ["Entered by"]
    ticked = sheet_view(view, "cev", extras, removable=True)
    c1, c2 = st.columns(2)
    with c1:
        download_workbook(view, "expenses", "ce_wb")
    with c2:
        if ticked and st.button("Delete ticked rows", key="ce_del"):
            if has(CEO):
                with conn() as c_:
                    for rid in ticked:
                        c_.execute("DELETE FROM expenses WHERE id=?", (rid,))
                n_del = len(ticked)
            else:
                n_del = delete_expenses(ticked, st.session_state.user, st.session_state.uid, has(ACCOUNTANT))
            done(f"{n_del} row(s) deleted." if n_del else "Nothing deleted: you can only delete your own entries of today.")


# ---------- CEO: money requests ----------
def money_pay_block(reqs, user):
    appr = reqs[(reqs.Status == "Approved") & (reqs["Requested by"] != user)]
    if appr.empty:
        st.success("No approved request is waiting for payment.")
    else:
        st.caption("Tick Paid when the money has been given. It is then added to the person's money used.")
        tbl = pd.DataFrame({"Request #": appr.id.values, "By": appr["Requested by"].values, "Field": appr.Field.values,
                            "Category": appr.Category.values, "Purpose": appr.Purpose.values, "Approved": appr.Approved.values, "Paid": False})
        ed = wide(st.data_editor, tbl, hide_index=True, key=f"{K('mx')}_{len(appr)}_{int(appr.id.iloc[0])}",
                  disabled=["Request #", "By", "Field", "Category", "Purpose", "Approved"], column_config={"Paid": st.column_config.CheckboxColumn()})
        if st.button("Record payment", type="primary", key="cmx_go"):
            pids = [int(i) for i, p in zip(appr.id, ed["Paid"]) if p]
            if not pids:
                st.info("Tick the requests that were paid.")
            else:
                done(f"{pay_money_requests(pids, user)} payment(s) recorded.")


def money_all(reqs):
    if reqs.empty:
        return st.caption("No money requests yet.")
    a, b, c = st.columns([2, 2, 3])
    status = a.radio("Show", ["All", "Pending", "Approved", "Paid", "Rejected"], horizontal=True, key="cmr_st")
    cat = b.selectbox("Category", ["All categories"] + categories_df().name.tolist(), key="cmr_cat")
    with c:
        q = search_box("cmr_q", "Search purpose, category or person", "Type part of a word…")
    view = reqs if status == "All" else reqs[reqs.Status == status]
    view = view if cat == "All categories" else view[view.Category == cat]
    view = match(view, ["Purpose", "Category", "Requested by", "Field"], q)
    sheet = view.assign(Item=view.Purpose, Qty=1.0, **{"P/U": view.Requested, "Total": view.Requested})
    extras = ["Category", "Field", "Approved", "Status", "Needed by", "Requested by", "Decided by", "Reason", "Paid on"]
    t = sheet_number(sheet, CORE + extras)
    st.markdown(f"**{len(t)} requests · Grand total {money(pd.to_numeric(t.Total).sum())} Frw**")
    wide(st.dataframe, pretty(t, ["P/U", "Total", "Approved"], "Status", qty_cols=["Qty"]), hide_index=True)
    download_sheet(sheet, extras, "Money requests", "cmr_dl")


def moneyreq_ceo():
    user = st.session_state.user
    field = st.session_state.get("ceo_field", "All fields")
    reqs = load_money_requests()
    if field != "All fields":
        reqs = reqs[reqs.Field == field]
    reqs = reqs.reset_index(drop=True)
    pending = reqs[reqs.Status == "Pending"]
    t1, t2, t3 = st.tabs([f"Waiting for your decision ({len(pending)})", "Approved: record the payment", "All requests"])
    with t1:
        if pending.empty:
            st.success("No pending money requests.")
        else:
            tbl = pd.DataFrame({"Request #": pending.id.values, "By": pending["Requested by"].values, "Field": pending.Field.values,
                                "Category": pending.Category.values, "Purpose": pending.Purpose.values, "Requested": pending.Requested.values,
                                "Needed by": pending["Needed by"].values, "Decision": "Approve", "Amount to approve": pending.Requested.values,
                                "Reason if rejected": ""})
            ed = wide(st.data_editor, tbl, hide_index=True, key=f"mrd{K('d')}_{abs(hash(tuple(int(i) for i in pending.id)))}",
                      disabled=["Request #", "By", "Field", "Category", "Purpose", "Requested", "Needed by"], column_config={
                          "Decision": st.column_config.SelectboxColumn(options=["Approve", "Reject", "Decide later"], required=True),
                          "Amount to approve": st.column_config.NumberColumn(min_value=0, step=1000)})
            if st.button(":material/check: Confirm decisions", type="primary", key="cmrd_go"):
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
    with t2:
        money_pay_block(reqs, user)
    with t3:
        money_all(reqs)


# ---------- CEO: money used ----------
def moneyused_form(sites, field, user):
    with st.expander("Record money I used (Excel sheet)", expanded=True):
        st.caption("Money you spent yourself: materials, fuel, food, personal use. Personal entries are visible only to you.")
        a, b, c = st.columns(3)
        with a:
            cat, is_new = category_pick(K("mu_c"))
        kind = b.selectbox("Kind", ["Business", "Personal"], key=K("mu_k"))
        fc = field_choices(sites)
        names = list(fc)
        default = names.index(field) if field in names else 0
        fname = c.selectbox("Field", names, index=default, key=K("mu_f"))
        ed = sheet_editor("mu_g", rows=4)
        ok, bad = parse_sheet(ed)
        sheet_preview(ok)
        for msg in bad:
            st.warning(msg)
        if st.button("Save money used", type="primary", key="mu_save"):
            if not cat:
                st.warning("Write the name of the new category.")
            elif bad:
                st.error("Please correct the lines marked above.")
            elif not ok:
                st.info("Nothing to save yet.")
            else:
                now = datetime.now().isoformat(timespec="seconds")
                with conn() as c_:
                    if is_new:
                        ensure_named(c_, "categories", cat)
                    bulk_insert(c_, "money_ledger", ["spent_on", "person", "site_id", "kind", "category", "amount", "note", "qty", "unit_price",
                                                     "entered_by", "entered_at"],
                                [(str(e["date"]), user, fc[fname], kind, cat, e["total"], e["item"], e["qty"], e["price"], user, now) for e in ok])
                done(f"{len(ok)} row(s) saved.")


def moneyused_page():
    user = st.session_state.user
    field = st.session_state.get("ceo_field", "All fields")
    sites = visible_sites()
    if has(CEO):
        moneyused_form(sites, field, user)
    st.subheader("Money used")
    led = load_ledger()
    if field != "All fields":
        led = led[led.Field == field]
    if led.empty:
        return st.caption("Nothing recorded yet.")
    raw = df("SELECT id, qty, unit_price FROM money_ledger")
    led = led.merge(raw, on="id", how="left").reset_index(drop=True)
    a, b, c = st.columns([2, 2, 3])
    kind = a.radio("Show", ["All", "Business", "Personal"], horizontal=True, key="mu_show")
    cat = b.selectbox("Category", ["All categories"] + sorted(set(led.Category)), key="mu_cat")
    with c:
        q = search_box("mu_q", "Search item, person or field", "Type part of a word…")
    view = led if kind == "All" else led[led.Kind == kind]
    view = view if cat == "All categories" else view[view.Category == cat]
    view = match(view, ["Note", "Person", "Field"], q)
    k = st.columns(3)
    k[0].metric("Total", money(view.Amount.sum()))
    k[1].metric("Business", money(view[view.Kind == "Business"].Amount.sum()))
    k[2].metric("Personal", money(view[view.Kind == "Personal"].Amount.sum()))
    sheet = view.assign(Item=view.Note.fillna(""), Qty=view.qty.fillna(1.0), **{"P/U": view.unit_price.fillna(view.Amount), "Total": view.Amount})
    extras = (["Category"] if cat == "All categories" else []) + ["Kind", "Person", "Field"]
    mine = sheet[(sheet.entered_by == user) & sheet.request_id.isna()]
    ticked = sheet_view(sheet, "muv", extras, removable=bool(len(mine)))
    own = set(mine.id.astype(int))
    c1, c2 = st.columns(2)
    with c1:
        download_workbook(sheet, "money_used", "mu_wb")
    with c2:
        ticked = [i for i in ticked if i in own]
        if ticked and st.button("Delete ticked rows (my own)", key="mu_del"):
            with conn() as c_:
                for i in ticked:
                    c_.execute("DELETE FROM money_ledger WHERE id=? AND entered_by=? AND request_id IS NULL", (i, user))
            done(f"{len(ticked)} row(s) deleted.")


# ---------- CEO: funds received ----------
def funds_page():
    user = st.session_state.user
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    ids = [int(i) for i in sites.id]
    with st.expander("Record funds received", expanded=True):
        a, b, c = st.columns(3)
        with a:
            site_id, fname = field_target(sites, K("cf_f"))
        amount = b.number_input("Amount (Frw)", min_value=1, step=1000, value=None, placeholder="Enter amount", key=K("cf_a"))
        day = c.date_input("Date received", date.today(), max_value=date.today(), key=K("cf_d"))
        d, e = st.columns(2)
        source = d.text_input("Received from", placeholder="For example the client name", key=K("cf_s"))
        note = e.text_input("Note (optional)", key=K("cf_n"))
        if st.button("Save funds", type="primary", key="cf_save"):
            if not amount:
                st.warning("Enter the amount.")
            else:
                run("INSERT INTO funds(site_id, amount, received_on, source, note, entered_by) VALUES(?,?,?,?,?,?)",
                    (site_id, int(amount), str(day), source.strip(), note.strip(), user))
                done(f"{money(amount)} Frw recorded for {fname}.")
    st.subheader("Funds received")
    q = search_box("cf_q", "Search field, source or note", "Type part of a name…")
    fnd = match(load_funds(ids), ["Field", "Source", "Note"], q)
    sheet = fnd.assign(Item=fnd.Source.fillna("").where(fnd.Source.fillna("") != "", "Funds received"), Qty=1.0,
                       **{"P/U": fnd.Amount, "Total": fnd.Amount})
    extras = (["Field"] if len(sites) > 1 else []) + ["Note", "Entered by"]
    ticked = sheet_view(sheet, "cfv", extras, removable=True)
    c1, c2 = st.columns(2)
    with c1:
        download_sheet(sheet, extras, "Funds received", "cf_dl")
    with c2:
        if ticked and st.button("Delete ticked records", key="cf_del"):
            with conn() as c_:
                for rid in ticked:
                    c_.execute("DELETE FROM funds WHERE id=?", (rid,))
            done(f"{len(ticked)} record(s) deleted.")


# ---------- CEO: reports ----------
def reports_page():
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    ids = [int(i) for i in sites.id]
    field = st.session_state.get("ceo_field", "All fields")
    exp = load_expenses(ids)
    if exp.empty:
        return st.info("No spending recorded yet.")
    lo, hi = date.fromisoformat(exp.Date.min()), max(date.fromisoformat(exp.Date.max()), date.today())
    a, b = st.columns(2)
    d_from, d_to = a.date_input("From", lo, key=f"cr_from_{field}"), b.date_input("To", hi, key=f"cr_to_{field}")
    sel = exp[(exp.Date >= str(d_from)) & (exp.Date <= str(d_to))]
    if sel.empty:
        return st.info("No spending in this period.")
    st.subheader("Summary")
    by_cat = sel.groupby("Category").agg(Amount=("Total", "sum"), Transactions=("Total", "size")).reset_index().sort_values("Amount", ascending=False)
    by_cat["Share %"] = (by_cat.Amount / by_cat.Amount.sum() * 100).round(1)
    by_month = sel.assign(Month=sel.Date.str[:7]).groupby("Month").Total.sum().reset_index().rename(columns={"Total": "Amount"})
    st.markdown(f"**{len(sel)} transactions · Total {money(sel.Total.sum())} Frw**")
    s1, s2 = st.columns([3, 2])
    with s1:
        wide(st.dataframe, pretty(by_cat, ["Amount"]), hide_index=True)
    with s2:
        c_donut(top_other(sel, "Category", "Total"), "Category", "Total")
    sheets = {"By category": by_cat, "By month": by_month}
    per = sites.rename(columns={"name": "Field"})[["id", "Field"]].copy()
    per["Spent"] = per.id.map(exp.groupby("site_id").Total.sum()).fillna(0).astype(int)
    money_cols = ["Spent"]
    if has(*FUNDS_VISIBLE):
        fnd = load_funds(ids)
        per.insert(1, "Funds received", per.id.map(fnd.groupby("site_id").Amount.sum()).fillna(0).astype(int))
        per["Balance"] = per["Funds received"] - per["Spent"]
        money_cols = ["Funds received", "Spent", "Balance"]
    sheets["By field" if not has(*FUNDS_VISIBLE) else "Funds and balance"] = per.drop(columns="id")
    if len(sites) > 1:
        st.markdown("**By field**" if not has(*FUNDS_VISIBLE) else "**Funds and balance by field**")
        wide(st.dataframe, pretty(per.drop(columns="id"), money_cols), hide_index=True)
    st.download_button(":material/download: Download the summary (Excel)", to_excel(sheets), f"summary_{norm(field) or 'all'}_{date.today()}.xlsx",
                       mime=XLSX, key="dl_cr_sum")

    st.subheader("Category report")
    st.caption("Choose a category to see its sheet: No, Date, Item, Qty, P/U, Total.")
    cat = st.selectbox("Category", ["All categories"] + sorted(set(sel.Category)), key="cr_cat")
    view = sel if cat == "All categories" else sel[sel.Category == cat]
    view = cost_frame(view)
    extras = (["Category"] if cat == "All categories" else []) + (["Field"] if len(sites) > 1 else [])
    sheet_view(view, "crv", extras)
    c1, c2 = st.columns(2)
    with c1:
        download_sheet(view, extras, cat if cat != "All categories" else "All categories", "cr_dl", "Download this sheet (Excel)")
    with c2:
        download_workbook(cost_frame(sel), "report_all_categories", "cr_wb", "Download all categories (one sheet each)")


def money_new_block(sites, reqs, user):
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


def sheet_show(frame, extras, name, key):
    """A read-only table in the workbook layout (No, Date, Item, Qty, P/U, Total + extras) with its download bar."""
    cols = CORE + [c for c in extras if c in frame.columns]
    t = sheet_number(frame, cols)
    st.markdown(f"**{len(t)} rows · Grand total {money(pd.to_numeric(t.Total).sum())} Frw**")
    wide(st.dataframe, pretty(t.head(300), ["P/U", "Total"], "Status" if "Status" in t.columns else None, qty_cols=["Qty"]), hide_index=True)
    if len(t):
        download_sheet(frame, extras, name, key)


def matreq_page():
    """Material requests for the Accountant, Store manager and Field manager (the CEO has his own page)."""
    if has(CEO):
        return matreq_ceo()
    user, uid = st.session_state.user, st.session_state.uid
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    reqs = load_mat_requests([int(i) for i in sites.id])
    pend = reqs[(reqs.Status == "Pending") & (reqs["Requested by"] != user)] if len(reqs) else reqs
    to_deliver = reqs[reqs.Status == "Approved"] if len(reqs) else reqs
    names = []
    if has(ACCOUNTANT):
        names.append(f"Waiting for approval ({int(pend.batch.nunique()) if len(pend) else 0})")
    if has(STORE):
        names.append(f"Record delivery ({len(to_deliver)})")
    names += ["New request", "All requests"]
    tabs = st.tabs(names)
    i = 0
    if has(ACCOUNTANT):
        with tabs[i]:
            section_approve(reqs, sites.iloc[:1], user, False)
        i += 1
    if has(STORE):
        with tabs[i]:
            section_delivery(reqs, user, uid)
        i += 1
    with tabs[i]:
        section_new(sites, user)
        section_mine(reqs, user)
    with tabs[i + 1]:
        matreq_all(reqs)


def moneyreq_page():
    """Money requests for the Accountant, Store manager and Field manager (the CEO has his own page)."""
    if has(CEO):
        return moneyreq_ceo()
    user = st.session_state.user
    field = st.session_state.get("ceo_field", "All fields")
    sites = visible_sites()
    reqs = load_money_requests()
    if field != "All fields":
        reqs = reqs[reqs.Field == field]
    reqs = reqs.reset_index(drop=True)
    pay = reqs[(reqs.Status == "Approved") & (reqs["Requested by"] != user)] if has(ACCOUNTANT) else reqs.iloc[0:0]
    names = ["New request"] + ([f"Approved: record the payment ({len(pay)})"] if has(ACCOUNTANT) else []) + ["All requests"]
    tabs = st.tabs(names)
    with tabs[0]:
        money_new_block(sites, reqs, user)
    if has(ACCOUNTANT):
        with tabs[1]:
            money_pay_block(reqs, user)
    with tabs[-1]:
        money_all(reqs)


def store_dashboard():
    """Dashboard of the Store manager: stock, deliveries and movements, in the same style as the other dashboards."""
    user, field = st.session_state.user, st.session_state.get("ceo_field", "All fields")
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    ids = [int(i) for i in sites.id]
    mv, reqs = load_moves(ids), load_mat_requests(ids)
    bal = stock_balances(mv)
    st.markdown(DASH_CSS, unsafe_allow_html=True)
    st.markdown(f'<div class="dh">Dashboard</div><div class="dsub">{html.escape("All fields" if field == "All fields" else field)}'
                f' · materials and stock · updates automatically when anything is recorded</div>', unsafe_allow_html=True)
    months = []
    cur = date.today().replace(day=1)
    for _ in range(6):
        months.insert(0, str(cur)[:7])
        cur = (cur - timedelta(days=1)).replace(day=1)
    labs = [month_label(m, False) for m in months]
    rec_m = monthly_vals(mv[mv.Type == "Received"] if len(mv) else mv, "Qty", months, "count")
    use_m = monthly_vals(mv[mv.Type == "Used"] if len(mv) else mv, "Qty", months, "count")
    since = str(date.today() - timedelta(days=30))
    in_n = int((bal.Balance > 1e-9).sum()) if len(bal) else 0
    out_n = int((bal.Balance <= 1e-9).sum()) if len(bal) else 0
    to_deliver = int((reqs.Status == "Approved").sum()) if len(reqs) else 0
    pending = int(reqs[reqs.Status == "Pending"].batch.nunique()) if len(reqs) else 0
    rec30 = int(((mv.Type == "Received") & (mv.Date >= since)).sum()) if len(mv) else 0
    use30 = int(((mv.Type == "Used") & (mv.Date >= since)).sum()) if len(mv) else 0
    st.markdown('<div class="dsec">Stock today</div><div class="dcap">Material lines in the store</div>', unsafe_allow_html=True)
    kpi_grid([
        kpi("Materials in stock", in_n, "material lines with a balance", "green"),
        kpi("Out of stock", out_n, "material lines with no balance", "red" if out_n else "green"),
        kpi("Received (30 days)", rec30, "receiving entries", "blue", spark_svg(rec_m, labs, "#2f6fb0"),
            trend(rec_m[-1], rec_m[-2], labs[-2], lambda v: f"{v:,.0f}")),
        kpi("Used (30 days)", use30, "usage entries", "amber", spark_svg(use_m, labs, "#e0922f"),
            trend(use_m[-1], use_m[-2], labs[-2], lambda v: f"{v:,.0f}")),
        kpi("Deliveries to record", to_deliver, "approved lines waiting for you", "amber" if to_deliver else "green"),
        kpi("Requests waiting approval", pending, "sent, not yet decided", "grey"),
    ])
    says = []
    empty = lambda f: f.iloc[0:0]
    for lvl, text in dash_alerts(sites, empty(load_expenses(ids)), empty(load_funds(ids)), bal, empty(load_money_requests()), reqs, empty(load_debts(field))):
        says.append((RED if lvl == "High" else AMBER if lvl == "Medium" else "#2f6fb0", html.escape(text)))
    if len(mv):
        gap = (date.today() - date.fromisoformat(str(mv.Date.max())[:10])).days
        if gap >= 7:
            says.append((AMBER, f"No stock movement has been recorded for {gap} days."))
    st.markdown('<div class="dsec">What the data says</div>', unsafe_allow_html=True)
    insights_html(says[:8])
    st.markdown('<div class="dsec">Analysis</div><div class="dcap">Hover a chart for the exact value. Every chart has a Download data button.</div>', unsafe_allow_html=True)
    t_mat, t_act = st.tabs(["Materials", "Activity"])
    with t_mat:
        dash_materials_tab(bal, mv, reqs)
    with t_act:
        mvs = mv.head(30)[["Date", "Field", "Material", "Unit", "Type", "Qty", "Detail", "Entered by"]].reset_index(drop=True) if len(mv) else pd.DataFrame()
        with card("Recent movements", "The latest 30 entries", mvs if len(mvs) else None, "smv"):
            if len(mvs):
                mvs.insert(0, "No", range(1, len(mvs) + 1))
                wide(st.dataframe, pretty(mvs, qty_cols=["Qty"]), hide_index=True)
            else:
                st.caption("Nothing recorded yet.")


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
RENDER = {"dashboard": page_dashboard, "expenses": page_expenses, "funds": page_funds, "import": page_import,
          "setup": page_setup, "reports": page_reports, "users": page_users, "stock": page_stock,
          "receive": page_receive, "use": page_use, "count": page_count, "matreq": page_matreq,
          "analytics": page_analytics, "moneyreq": page_moneyreq, "moneyused": page_moneyused, "taxdebt": page_taxdebt}


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
    page = RENDER[sec]
    if sec in NEW_RENDER:  # the new design, the same for every role: Field drop-down on top, Excel-style tables
        page = NEW_RENDER[sec]
        st.session_state.ceo_scope = field_bar()
    page()
    with st.sidebar:
        user_box()


if __name__ == "__main__":
    main()
