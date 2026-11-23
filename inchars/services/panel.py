"""One job opened: the header and its parts (Work, Details, Files, Money, History). Each part runs on its own, so a problem in one
part shows a message in place and the others keep working. Work, Details and History are in this file; Files is in files.py and Money in money.py.
The Engineer never sees money: the Money part does not exist for him or her."""

from datetime import date

import streamlit as st

from inchars.accounts import has
from inchars.components.common import K, done, wide
from inchars.components.kpi import kpi, kpi_grid
from inchars.config import CEO
from inchars.services.config import NEED_REASONS, PERMIT_STAGES, REVERT_REASONS
from inchars.services.core import can, load_engineers, load_events, load_jobs, missing_info, sees_money, update_customer_info
from inchars.services.ui import due_pick, pick, run_section
from inchars.services.workflow import assign_job, change_status, edit_job, set_stage

TONE = {"With CEO": "amber", "With engineer": "blue", "Waiting for customer": "amber", "In review": "amber", "Reverted": "red", "Ready": "green", "Collected": "grey", "Cancelled": "grey"}


def _do(err, ok):
    if err:
        st.error(err)
    else:
        done(ok)


def _reasons(label, options, k):
    """Tap the reasons and/or write one. Returns the text (empty when nothing was chosen)."""
    chosen = pick(label, options, K(k + "r"), multi=True)
    extra = st.text_input("More details (optional)", key=K(k + "rx"))
    return "; ".join(list(chosen) + ([extra.strip()] if extra.strip() else []))


# ---------------------------------------------------------------- the 'information we do not have' bar
def missing_bar(j, p):
    """Shows what we still do not know about this customer. Nothing is required: fill it in when the customer is here, or skip it."""
    lacking = missing_info(j.phone, j.customer_email)
    if not lacking or not can("set_contact", j):
        return
    names = " and ".join(label for _, label in lacking)
    with st.container(border=True):
        st.markdown(f"**Information we do not have: {names}.**")
        st.caption("Not required. Add it now if the customer is here, or leave it. An email address lets us keep the customer informed by email.")
        cols = st.columns(len(lacking) + 1)
        vals = {}
        for (col, label), box in zip(lacking, cols):
            vals[col] = box.text_input(label.capitalize(), key=K(f"{p}mi{col}{int(j.id)}"), placeholder="0788 123 456" if col == "phone" else "name@example.com")
        if cols[-1].button("Save", key=K(f"{p}mis{int(j.id)}")):
            _do(update_customer_info(int(j.customer_id), vals.get("phone", ""), vals.get("email", "")), "Saved. Thank you.")


# ---------------------------------------------------------------- Work: what this person can do now
def work_section(j, p):
    jid, k = int(j.id), f"{p}{int(j.id)}_"
    shown = False
    if j.status == "Reverted" and j.returned_reason:
        st.warning(f"The CEO sent this work back: {j.returned_reason}")
    if can("confirm", j):  # the CEO checks the finished work
        shown = True
        st.markdown(f"**{j.assigned_to} finished this work. Please check it.**")
        st.caption("Open the Files part to read what the engineer attached.")
        if st.button(":material/check_circle: Confirm: the work is good", type="primary", key=K(k + "confirm")):
            _do(change_status(jid, "confirm"), "Confirmed. The Receptionist was told and the customer was emailed that the work is ready.")
        with st.expander("Send it back to the engineer (with a reason)"):
            why = _reasons("What is wrong", REVERT_REASONS, k + "rev")
            if st.button("Send back", key=K(k + "revert"), disabled=not why):
                _do(change_status(jid, "revert", why), "Sent back. The engineer was told.")
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
                _do(assign_job(jid, who, due), f"{j.job_no} was given to {who}, who was told.")
    if can("submit", j):
        shown = True
        st.markdown("**When the work is finished**")
        st.caption("You can attach the finished PDF in the Files part (optional). Then send the work to the CEO.")
        note = st.text_input("Note for the CEO (optional)", key=K(k + "subn"))
        if st.button(":material/send: Send to the CEO", type="primary", key=K(k + "submit")):
            _do(change_status(jid, "submit", note), "Sent to the CEO.")
    if can("stage", j):
        shown = True
        with st.expander("Building permit: where is the file?"):
            stage = pick("Stage", PERMIT_STAGES, K(k + "stage"), default=j.stage if j.stage in PERMIT_STAGES else None)
            ref = st.text_input("Government reference number", value=j.gov_ref or "", key=K(k + "ref"))
            if st.button("Save the stage", key=K(k + "stageb"), disabled=not stage):
                _do(set_stage(jid, stage, ref), "Stage saved.")
    if can("resume", j):
        shown = True
        note = st.text_input("Note (optional)", key=K(k + "resn"))
        if st.button(":material/play_arrow: The customer answered: continue the work", type="primary", key=K(k + "resume")):
            _do(change_status(jid, "resume", note), "The work continues. The engineer was told.")
    if can("wait", j):
        shown = True
        with st.expander("Need something from the customer?"):
            text = _reasons("What is needed", NEED_REASONS, k + "need")
            if st.button("Ask the customer", key=K(k + "wait"), disabled=not text):
                _do(change_status(jid, "wait", text), "The customer was emailed (if there is an address) and the Receptionist was told.")
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
            _do(change_status(jid, "collect", note, override), "Done. The job is closed.")
    if can("reopen", j):
        shown = True
        why = st.text_input("Why is the job reopened", key=K(k + "why"))
        if st.button("Reopen the job", key=K(k + "reopen"), disabled=not why.strip()):
            _do(change_status(jid, "reopen", why), "The job was reopened.")
    if not shown:
        st.caption("Nothing for you to do on this job now.")


# ---------------------------------------------------------------- Details: change the job, cancel it
def details_section(j, p):
    jid, k = int(j.id), f"{p}{int(j.id)}_"
    if can("edit", j):
        title = st.text_input("Title", value=j.title, key=K(k + "et"))
        desc = st.text_area("Description", value=j.description or "", key=K(k + "ed"))
        a, b = st.columns(2)
        fee = a.number_input("Fee (Frw)", min_value=0, step=1000, value=int(j.fee), key=K(k + "ef"))
        due = b.date_input("Promised date", date.fromisoformat(j.due_on) if j.due_on else date.today(), key=K(k + "eD"))
        if st.button("Save the changes", type="primary", key=K(k + "eb")):
            _do(edit_job(jid, title, desc, fee, due), "Saved.")
    if can("cancel", j):
        with st.expander("Cancel this job"):
            why = st.text_input("Reason for cancelling", key=K(k + "cr"))
            if st.button("Cancel the job", key=K(k + "cc"), disabled=not why.strip()):
                _do(change_status(jid, "cancel", why), "The job was cancelled.")


# ---------------------------------------------------------------- History
def history_section(j, p):
    ev = load_events(int(j.id))
    if ev.empty:
        st.caption("Nothing yet.")
    else:
        wide(st.dataframe, ev, hide_index=True)


# ---------------------------------------------------------------- the opened job
def job_panel(job_id, p="jb"):
    """p is a short prefix so that the same job can be shown in two places without clashing.
    The eye of the list opens the Work part, the pencil opens Details (st.session_state[p + '_part'])."""
    jobs = load_jobs()
    rows = jobs[jobs.id == job_id]
    if rows.empty:
        return st.warning("This job is not available to you.")
    j = rows.iloc[0]
    money = sees_money()
    with st.container(border=True):
        a, b = st.columns([6, 1])
        a.markdown(f"### {j.job_no} · {j.title}")
        if b.button("Close", key=K(f"{p}close{int(j.id)}")):
            st.session_state[p + "_open"] = None
            st.rerun()
        st.caption(f"{j.service} · {j.customer} · {j.customer_email or 'no email'} · {j.phone or 'no phone'}")
        run_section("panel", "missing_bar", j, p)
        cards = [kpi("Status", j.status, ("stage: " + j.stage) if j.stage else "where the job is now", TONE.get(j.status, "grey")),
                 kpi("Engineer", j.assigned_to or "not given yet", "doing the work", "blue" if j.assigned_to else "amber", small=True),
                 kpi("Promised", j.due_on or "-", "overdue" if j.overdue else "date given to the customer", "red" if j.overdue else "grey")]
        if money:
            cards.append(kpi("Balance", f"{j.balance:,.0f}", "paid in full" if j.balance <= 0 else f"Frw to pay · fee {j.fee:,.0f}", "green" if j.balance <= 0 else "red"))
        kpi_grid(cards)
        if j.description:
            st.caption(j.description)
        parts = ["Work"] + (["Details"] if can("edit", j) or can("cancel", j) else []) + ["Files"] + (["Money"] if money else []) + ["History"]
        want = st.session_state.get(p + "_part")
        part = pick("", parts, K(f"{p}part{int(j.id)}_{st.session_state.get(p + '_ver', 0)}"), default=want if want in parts else "Work") or "Work"
        where = {"Work": ("panel", "work_section"), "Details": ("panel", "details_section"), "Files": ("files", "files_section"),
                 "Money": ("money", "money_section"), "History": ("panel", "history_section")}[part]
        run_section(where[0], where[1], j, p)
