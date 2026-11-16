"""The login screen and the first-login email prompt."""

import base64
import io
import os

import streamlit as st

from inchars.accounts import (
    authenticate,
    clear_fails,
    first_manager,
    is_locked,
    load_roles,
    note_fail,
    request_reset,
    reset_password,
    set_email,
    users_exist,
)
from inchars.config import BG_IMAGE, EMAIL_RE, LOGO
from inchars.db import IS_PG
from inchars.styles import LOGIN_CSS


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
