"""People: accounts, roles, sending email, which fields a person may see, and the login screen."""

import base64
import io
import os
import secrets
import smtplib
import streamlit as st
import time
from datetime import date, datetime, timedelta
from email.message import EmailMessage

from inchars.database import IS_PG, IntegrityErrors, bulk_insert, conn, df, run
from inchars.settings import BG_IMAGE, CEO, EMAIL_RE, LOGIN_CSS, LOGO, SEE_ALL, check_pw, hash_pw


# ======================================================================
# from accounts.py
# ======================================================================

def first_manager():
    """The very first Store manager account comes from the app secrets (never from the code)."""
    try:
        s = dict(st.secrets["first_manager"])
    except Exception:
        s = {}
    return (s.get("username") or os.getenv("FIRST_MANAGER_USER"), s.get("password") or os.getenv("FIRST_MANAGER_PASSWORD"),
            s.get("email") or os.getenv("FIRST_MANAGER_EMAIL"))


def users_exist():
    return int(df("SELECT COUNT(*) AS n FROM users")["n"][0]) > 0


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


def send_email(to, subject, body, cfg=None):
    cfg = cfg or smtp_conf()
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


def set_user_sites(uid, site_ids):
    with conn() as c:
        c.execute("DELETE FROM user_sites WHERE user_id=?", (uid,))
        if site_ids:
            bulk_insert(c, "user_sites", ["user_id", "site_id"], [(uid, s) for s in site_ids])


# ======================================================================
# from data/scope.py
# ======================================================================

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


def no_field_message():
    if has(*SEE_ALL):
        st.info("There is no field yet. Create the first one in Fields & categories.")
    else:
        st.info("No field is linked to your account yet. Please ask the CEO to give you access to a field.")


# ======================================================================
# from login.py
# ======================================================================

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


def _logo_uri():
    try:
        with open(LOGO, "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode()
    except Exception:
        return ""


LOGO_URI = _logo_uri()


LEFT_HTML = ('<div class="left-panel"><div class="overlay">' + (f'<img class="logo" src="{LOGO_URI}">' if LOGO_URI else "")
             + '<h1>Inchars PD Group Ltd</h1><p>Construction Management System</p></div></div>')
