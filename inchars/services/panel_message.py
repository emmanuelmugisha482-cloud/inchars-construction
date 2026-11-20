"""Job panel, 'Message' part: tell the customer or the office workers something about this job."""

import streamlit as st

from inchars.components.common import K, done
from inchars.services.config import MESSAGE_TEMPLATES
from inchars.services.notices import send_message
from inchars.services.ui import pick


def message_section(j, p):
    jid, k = int(j.id), f"{p}{int(j.id)}_"
    options = ["Customer", "CEO", "Receptionist"] + (["Engineer"] if j.assigned_to else [])
    to = pick("Send to", options, K(k + "to"), multi=True)
    if "Customer" in to and not j.customer_email:
        st.warning("This customer has no email address, so he or she cannot be told. Add the email address above first.")
    tpl = pick("Ready-made message", list(MESSAGE_TEMPLATES), K(k + "tpl"), default="Write my own") or "Write my own"
    text = st.text_area("Message", value=MESSAGE_TEMPLATES[tpl].format(job_no=j.job_no), key=K(k + "msg" + tpl))
    if st.button("Send the message" if to else "Save in the history only (nobody is notified)", type="primary", key=K(k + "send")):
        err, info = send_message(jid, to, text)
        if err:
            st.error(err)
        elif info and "NOT told" in info:
            st.warning(info)
        else:
            done("Message sent." if to else "Saved in the history.")
