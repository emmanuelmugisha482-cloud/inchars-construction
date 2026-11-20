"""Job panel, 'Update' part: the next step of the job as big buttons, with choices to tap."""

from datetime import date

import streamlit as st

from inchars.accounts import has
from inchars.components.common import K, done
from inchars.config import CEO
from inchars.services.config import NEED_REASONS, PERMIT_STAGES
from inchars.services.jobs import assign_job, change_status, edit_job, load_engineers, set_stage
from inchars.services.rules import can
from inchars.services.ui import due_pick, pick


def _do(err, ok):
    if err:
        st.error(err)
    else:
        done(ok)


def update_section(j, p):
    jid, k = int(j.id), f"{p}{int(j.id)}_"
    shown = False
    if can("assign", j):
        shown = True
        st.markdown("**Give the job to an engineer**" if not j.assigned_to else "**Change the engineer**")
        engineers = load_engineers()
        if not engineers:
            st.info("There is no engineer yet. Add a user with the Engineer role in Users.")
        else:
            who = pick("Engineer", engineers, K(k + "eng"), default=j.assigned_to if j.assigned_to in engineers else None)
            due = due_pick(K(k + "due"), date.fromisoformat(j.due_on) if j.due_on else None)
            if st.button("Assign", type="primary", key=K(k + "assign"), disabled=not who):
                _do(assign_job(jid, who, due), f"{j.job_no} was given to {who}. The engineer and the customer were told.")
    if can("start", j):
        shown = True
        if st.button(":material/play_arrow: Start work", type="primary", key=K(k + "start")):
            _do(change_status(jid, "start"), "Work started. The customer was told.")
    if can("stage", j):
        shown = True
        st.markdown("**Permit: where is the file?**")
        stage = pick("Stage", PERMIT_STAGES, K(k + "stage"), default=j.stage if j.stage in PERMIT_STAGES else None)
        ref = st.text_input("Government reference number", value=j.gov_ref or "", key=K(k + "ref"))
        if st.button("Save the stage", key=K(k + "stageb"), disabled=not stage):
            _do(set_stage(jid, stage, ref), "Stage saved. The customer was told.")
    if can("resume", j):
        shown = True
        note = st.text_input("Note (optional)", key=K(k + "resn"))
        if st.button(":material/play_arrow: The customer answered: continue the work", type="primary", key=K(k + "resume")):
            _do(change_status(jid, "resume", note), "Work continues. The engineer was told.")
    if can("ready", j):
        shown = True
        note = st.text_input("Note for the Receptionist (optional)", key=K(k + "readyn"))
        if st.button(":material/task_alt: Mark as ready for the customer", type="primary", key=K(k + "ready")):
            _do(change_status(jid, "ready", note), "The CEO, the Receptionist and the customer were told.")
        st.caption("Attaching the finished PDF is optional: use the Documents part.")
    if can("wait", j):
        shown = True
        with st.expander("Need something from the customer?"):
            reasons = pick("What is needed", NEED_REASONS, K(k + "need"), multi=True)
            extra = st.text_input("More details (optional)", key=K(k + "needx"))
            text = "; ".join(list(reasons) + ([extra.strip()] if extra.strip() else []))
            if st.button("Ask the customer", key=K(k + "wait"), disabled=not text):
                _do(change_status(jid, "wait", text), "The customer and the Receptionist were told.")
    if can("collect", j):
        shown = True
        st.markdown("**The customer collects the document**")
        override, note = False, ""
        if j.balance > 0:
            st.warning(f"Not paid in full: {j.balance:,.0f} Frw is still to pay. Record the payment in the Money part first.")
            if has(CEO):
                override = st.checkbox("Release without full payment", key=K(k + "ov"))
                note = st.text_input("Reason", key=K(k + "ovr")) if override else ""
        else:
            st.success("Paid in full. The document can be handed over.")
            note = st.text_input("Note (optional)", key=K(k + "cn"))
        if st.button(":material/done_all: Handed to the customer", type="primary", key=K(k + "collect"), disabled=(j.balance > 0 and not override)):
            _do(change_status(jid, "collect", note, override), "The job is closed as collected. The customer was thanked.")
    if can("reopen", j):
        shown = True
        why = st.text_input("Why is the job reopened", key=K(k + "why"))
        if st.button("Reopen the job", key=K(k + "reopen"), disabled=not why.strip()):
            _do(change_status(jid, "reopen", why), "The job was reopened.")
    if can("edit", j) or can("cancel", j):
        with st.expander("More: change the details, cancel"):
            if can("edit", j):
                title = st.text_input("Title", value=j.title, key=K(k + "et"))
                desc = st.text_area("Description", value=j.description or "", key=K(k + "ed"))
                a, b = st.columns(2)
                fee = a.number_input("Fee (Frw)", min_value=0, step=1000, value=int(j.fee), key=K(k + "ef"))
                due = b.date_input("Promised date", date.fromisoformat(j.due_on) if j.due_on else date.today(), key=K(k + "eD"))
                if st.button("Save the changes", key=K(k + "eb")):
                    _do(edit_job(jid, title, desc, fee, due), "Changes saved.")
            if can("cancel", j):
                why = st.text_input("Reason for cancelling", key=K(k + "cr"))
                if st.button("Cancel this job", key=K(k + "cc"), disabled=not why.strip()):
                    _do(change_status(jid, "cancel", why), "The job was cancelled.")
    if not shown and not (can("edit", j) or can("cancel", j)):
        st.caption("Nothing for you to do on this job now.")
