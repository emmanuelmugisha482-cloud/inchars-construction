"""Small screen helpers: tap-to-choose buttons, quick dates, and a guard so that one broken part does not stop the whole page."""

import importlib
import traceback
from datetime import date, timedelta

import streamlit as st

from inchars.accounts import has
from inchars.config import CEO
from inchars.services.config import QUICK_DAYS


def pick(label, options, key, default=None, multi=False):
    """Choose by tapping a button. One choice (None if nothing is tapped), or several with multi=True (a list)."""
    options = list(options)
    vis, label = ("visible", label) if label else ("collapsed", "Choose")
    try:
        if multi:
            return list(st.pills(label, options, selection_mode="multi", default=default or [], key=key, label_visibility=vis) or [])
        return st.pills(label, options, selection_mode="single", default=default, key=key, label_visibility=vis)
    except (AttributeError, TypeError):  # an older Streamlit without tap buttons: ordinary choice boxes
        if multi:
            return st.multiselect(label, options, default=default or [], key=key, label_visibility=vis)
        return st.radio(label, options, index=options.index(default) if default in options else 0, horizontal=True, key=key, label_visibility=vis)


def due_pick(key, default=None):
    """The promised date: tap '3 days', '1 week'... or pick a date."""
    quick = dict(QUICK_DAYS)
    choice = pick("Promised date", list(quick) + ["Pick a date"], key=key + "q", default="1 week")
    if choice in quick:
        return date.today() + timedelta(days=quick[choice])
    return st.date_input("Date", default or date.today() + timedelta(days=7), min_value=date.today(), key=key + "d")


def problem(name, error):
    """Show that one part has a problem, in place, without stopping the rest of the page."""
    traceback.print_exc()
    st.error(f"This part ({name}) has a problem. The rest of the page still works." + (f"  Details for the CEO: {type(error).__name__}: {error}" if has(CEO) else ""))


def run_section(module, func, *args):
    """Run one part of the page (a function in inchars/services/<module>.py). If it fails, only that part shows an error."""
    try:
        fn = getattr(importlib.import_module("inchars.services." + module), func)
    except Exception as e:  # noqa: BLE001  (a missing or old file)
        return problem(module, e)
    try:
        fn(*args)
    except Exception as e:  # noqa: BLE001
        problem(module, e)
