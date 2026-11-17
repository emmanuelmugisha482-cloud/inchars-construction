"""Users, roles (has), login attempts, password reset by email."""

from datetime import date, datetime, timedelta
from email.message import EmailMessage
import os
import secrets
import smtplib
import time

import streamlit as st

from inchars.config import CEO, EMAIL_RE
from inchars.db import IntegrityErrors, bulk_insert, conn, df, run
from inchars.security import check_pw, hash_pw


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
