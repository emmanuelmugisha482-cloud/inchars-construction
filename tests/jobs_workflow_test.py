"""Workflow test for the services side (jobs): register, assign, work, upload, pay, collect, with the rules and notifications.

Run:   python tests/jobs_workflow_test.py"""
import sys
import types

from harness import LOG, OVERRIDE, PRESS, app, modules, show_page, sign_in, st, stub

# emails: no real mail is sent. The background thread runs at once, and every email is written in `sent`.
sent = []
jobs_mod = modules["inchars.data.jobs"]


class _Thread:
    def __init__(self, target=None, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


jobs_mod.threading = types.SimpleNamespace(Thread=_Thread)
jobs_mod.smtp_conf = lambda: {"host": "mail.test"}
jobs_mod.send_email = lambda to, subject, body, cfg=None: sent.append((to, subject, body))

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
problems = []


def check(name, ok, extra=""):
    print(("OK   " if ok else "FAIL ") + name + (f"  {extra}" if extra and not ok else ""))
    if not ok:
        problems.append(name)


def job(no):
    return app.df("SELECT * FROM jobs WHERE job_no=?", (no,)).iloc[0]


# ---- 1. the Receptionist registers a job ----
sign_in("rec")
cid, err = app.save_customer("Mukamana Alice", "0788 111 222", "", "")
check("receptionist adds a customer", cid and not err, err)
dup, err = app.save_customer("Someone else", "0788111222", "", "")
check("same phone number is refused", dup is None and "already" in err)
no, err = app.create_job(cid, "House design", "3-bedroom house, Kicukiro", "", 600000, "2026-12-10", deposit=900000)
check("deposit above the fee is refused", no is None, err)
no, err = app.create_job(cid, "House design", "3-bedroom house, Kicukiro", "Plot 12", 600000, "2026-12-10", deposit=200000, method="Mobile money",
                         files=[("land_title.pdf", PDF)])
check("job registered with a job number", bool(no) and no.startswith("INC-") and not err, err)
j = job(no); jid = int(j.id)
check("new job has status New and the deposit is recorded", j.status == "New" and int(app.load_payments(jid).amount.sum()) == 200000)
check("customer document was kept", len(app.list_files(jid)) == 1 and app.read_file(int(app.list_files(jid).id.iloc[0])) == PDF)
check("receptionist cannot assign", app.assign_job(jid, "eng", "2026-12-10") == "You cannot assign this job.")
sign_in("ceo")
check("CEO also gets the new job by email", any(t == "c@x.rw" and "INC-" in sub and "needs an engineer" in sub for t, sub, _ in sent), sent)
check("the Receptionist is not emailed about his own action", not any(t == "rec@x.rw" for t, _, _ in sent))
check("CEO has a notification for the new job", app.unread_count() >= 1 and any(no in m for m in app.load_notices().Message))

# ---- 2. the CEO assigns it ----
check("assign to someone who is not an engineer is refused", app.assign_job(jid, "acc", "2026-12-10") == "Choose an engineer.")
check("CEO assigns to the engineer", app.assign_job(jid, "eng", "2026-12-12") is None and job(no).status == "Assigned")
check("the engineer gets the assignment by email", any(t == "eng@x.rw" and no in sub for t, sub, _ in sent))
sign_in("eng")
check("engineer is notified", any(no in m for m in app.load_notices().Message))
check("engineer sees the job", jid in set(app.load_jobs().id))
sign_in("eng2")
check("another engineer does not see it", jid not in set(app.load_jobs().id))
check("another engineer cannot start it", app.change_status(jid, "start") == "You cannot do this on this job now.")

# ---- 3. the engineer works ----
sign_in("eng")
check("engineer cannot mark ready without a PDF", "Upload the finished PDF" in (app.change_status(jid, "start") or app.change_status(jid, "ready") or ""))
check("job is now in progress", job(no).status == "In progress")
check("waiting for the customer needs a note", app.change_status(jid, "wait", "") == "Write what you need from the customer.")
check("engineer asks the customer for something", app.change_status(jid, "wait", "Copy of the ID card") is None)
sign_in("rec")
check("receptionist is told", any("Copy of the ID card" in m for m in app.load_notices().Message))
check("receptionist records that the customer answered", app.change_status(jid, "resume", "ID brought") is None and job(no).status == "In progress")
sign_in("eng")
check("a Word file is refused as the finished document", "only PDF" in (app.add_files(jid, "Deliverable", [("plan.docx", b"x" * 100)]) or ""))
check("a fake PDF is refused", "not a real PDF" in (app.add_files(jid, "Deliverable", [("plan.pdf", b"hello")]) or ""))
check("engineer uploads the finished PDF", app.add_files(jid, "Deliverable", [("house_plan.pdf", PDF)]) is None)
check("engineer marks the job ready", app.change_status(jid, "ready", "Plans attached") is None and job(no).status == "Ready")
sign_in("ceo")
check("CEO is told the job is ready", any("READY" in m for m in app.load_notices().Message))
sign_in("rec")
check("receptionist is told the job is ready", any("READY" in m and "balance to pay" in m for m in app.load_notices().Message))

# ---- 4. the customer comes back ----
jobs = app.load_jobs(); row = jobs[jobs.id == jid].iloc[0]
check("balance is 400,000", row.balance == 400000, row.balance)
check("receptionist cannot open the document while unpaid", not app.can_open_deliverable(row))
check("receptionist cannot hand it over while unpaid", "not paid in full" in (app.change_status(jid, "collect") or ""))
check("payment above the balance is refused", "more than the balance" in (app.add_payment(jid, 500000, "Cash", "", "2026-11-16") or ""))
sign_in("eng")
check("engineer cannot record a payment", app.add_payment(jid, 1000, "Cash", "", "2026-11-16") == "You cannot record a payment on this job.")
check("engineer can open the document", app.can_open_deliverable(app.load_jobs().query("id == @jid").iloc[0]))
sign_in("rec")
check("receptionist records the rest of the payment", app.add_payment(jid, 400000, "Cash", "R-55", "2026-11-16") is None)
row = app.load_jobs().query("id == @jid").iloc[0]
check("now paid in full and the document opens", row.balance == 0 and app.can_open_deliverable(row))
check("receptionist hands over the document", app.change_status(jid, "collect") is None and job(no).status == "Collected")
check("collected date and person are kept", bool(job(no).collected_at) and job(no).collected_by == "rec")
sign_in("ceo")
check("CEO is told it was collected", any("collected" in m for m in app.load_notices().Message))
check("history has the whole story", len(app.load_events(jid)) >= 10, len(app.load_events(jid)))
check("only the CEO can reopen", app.change_status(jid, "reopen", "Customer wants a change") is None and job(no).status == "In progress")

# ---- 5. release without payment (CEO only) ----
sign_in("rec")
cid2, _ = app.save_customer("Habimana Eric", "0722 333 444", "", "")
no2, _ = app.create_job(cid2, "Consultancy", "Soil advice", "", 100000, "2026-12-01")
j2 = int(job(no2).id)
sign_in("ceo"); app.assign_job(j2, "eng", "2026-12-01")
sign_in("eng"); app.change_status(j2, "start"); app.add_files(j2, "Deliverable", [("advice.pdf", PDF)]); app.change_status(j2, "ready")
sign_in("ceo")
check("CEO needs a reason to release unpaid work", "reason" in (app.change_status(j2, "collect", "", True) or "").lower())
check("CEO can release unpaid work with a reason", app.change_status(j2, "collect", "Regular client, pays Friday", True) is None)

# ---- 6. building permit stages ----
sign_in("rec")
no3, _ = app.create_job(cid, "Building permit", "Permit for plot 12", "", 300000, "2026-12-20")
j3 = int(job(no3).id)
check("a non-permit job has no stage action", not app.can("stage", app.load_jobs().query("id == @j2").iloc[0]))
sign_in("ceo"); app.assign_job(j3, "eng", "2026-12-20")
sign_in("eng"); app.change_status(j3, "start")
check("engineer sets the permit stage and reference", app.set_stage(j3, "Submitted to government", "RDB-2026-77") is None and job(no3).gov_ref == "RDB-2026-77")
check("receptionist cancels only a New job", True)
sign_in("rec"); check("receptionist cannot cancel a job in progress", app.change_status(j3, "cancel", "x") == "You cannot do this on this job now.")

# ---- 6b. emails: a person without an address, a failing mail server, and no email set up ----
sign_in("ceo")
app.df("SELECT 1")
with app.conn() as c:
    c.execute("UPDATE users SET email=NULL WHERE username='eng2'")
before = len(sent)
check("reassign to an engineer without an email address works", app.assign_job(j3, "eng2", "2026-12-22") is None)
check("that engineer still has the notification inside the app", any(no3 in m for m in (sign_in("eng2") or app.load_notices()).Message))
check("nobody without an address is emailed, the previous engineer is", not any(t is None for t, _, _ in sent[before:]) and any(t == "eng@x.rw" for t, _, _ in sent[before:]))
sign_in("ceo")


def _broken(to, subject, body, cfg=None):
    raise RuntimeError("mail server is down")


jobs_mod.send_email = _broken
check("a broken mail server does not stop the job action", app.assign_job(j3, "eng", "2026-12-23") is None and job(no3).assigned_to == "eng")
jobs_mod.send_email = lambda to, subject, body, cfg=None: sent.append((to, subject, body))
before = len(sent)
jobs_mod.smtp_conf = lambda: None
check("with email not set up, the action works and nothing is sent", app.assign_job(j3, "eng2", "2026-12-24") is None and len(sent) == before)
check("the Jobs page says email is not set up", "not set up" in app.email_state())
jobs_mod.smtp_conf = lambda: {"host": "mail.test"}
check("the Jobs page shows where the emails go", "c@x.rw" in app.email_state())
app.assign_job(j3, "eng", "2026-12-23")
sign_in("eng")
st.session_state.job_open = None
check("the notifications panel shows the email line", "also sent to eng@x.rw" in show_page("jobs"))

# ---- 7. money and files rules ----
sign_in("acc")
check("accountant sees payments", len(app.load_payments()) >= 2)
check("accountant cannot see files", not app.can("see_files", app.load_jobs().query("id == @jid").iloc[0]))
sign_in("eng2")
check("engineer cannot read other people's payments", len(app.load_payments()) == 0)
sign_in("ceo")
check("CEO can delete a payment", app.delete_payment(int(app.load_payments(jid).id.iloc[0])) is None)

# ---- 8. the pages draw for every role with data ----
for who in ("ceo", "acc", "rec", "eng"):
    sign_in(who)
    for key in ("jobs", "dashboard"):
        for opened in (None, jid, j3):
            st.session_state.job_open = opened
            try:
                txt = show_page(key)
                ok = True
            except Exception as e:
                import traceback; traceback.print_exc(limit=3); ok = False; txt = ""
            check(f"{who}: {key} page draws" + (f" (job {opened} open)" if opened else ""), ok)
sign_in("eng"); st.session_state.job_open = j3; txt = show_page("jobs")
check("engineer's job page shows no fee or balance", "Fee" not in txt and "Balance" not in txt)
sign_in("rec"); st.session_state.job_open = j3; txt = show_page("jobs")
check("receptionist's job page shows the fee and balance", "Fee" in txt and "Balance" in txt)

# ---- 9. a new Engineer user needs no field ----
check("an Engineer can be created without a field", app.add_user_roles("newengineer", [app.ENGINEER], "", "password123") is None)

# ---- 10. a screen action end to end: register a job through the page ----
sign_in("rec"); st.session_state.job_open = None
OVERRIDE.clear(); PRESS.clear()
k = app.K
OVERRIDE.update({k("nj_mode"): "New customer", k("nj_n"): "Uwase Grace", k("nj_p"): "0733 555 666", k("nj_s"): "Consultancy", k("nj_t"): "Site visit advice",
                 k("nj_f"): 50000, k("nj_u"): [types.SimpleNamespace(name="id.pdf", getvalue=lambda: PDF)]})
PRESS.add(k("nj_go"))
try:
    app.new_job_tab()
except stub.Rerun:
    pass
check("job registered from the New job screen", len(app.df("SELECT 1 FROM jobs WHERE title='Site visit advice'")) == 1)

print("\nFAILURES:", len(problems))
sys.exit(1 if problems else 0)
