"""The history of a job: who did what and when."""

from datetime import datetime

import streamlit as st

from inchars.db import df


def now():
    return datetime.now().isoformat(timespec="seconds")


def log_event(c, job_id, event, detail=""):
    c.execute("INSERT INTO job_events(job_id, happened_at, by_user, event, detail) VALUES(?,?,?,?,?)",
              (int(job_id), now(), st.session_state.user, event, detail))


def load_events(job_id):
    return df('SELECT happened_at AS "When", by_user AS "By", event AS "What", detail AS "Detail" FROM job_events WHERE job_id=? ORDER BY id DESC', (int(job_id),))
