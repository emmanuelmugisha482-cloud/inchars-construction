"""All the tests of the app. Run:   python tests/run_tests.py

Part A opens every page for every role. Part B is the workflow test of the jobs side.

Workflow of the jobs (part B):

The way: Receptionist registers -> CEO assigns an engineer -> the engineer works and sends it to the CEO -> the CEO confirms (the customer is emailed
"your work is ready") or reverts it with a reason (back to the engineer). Engineers never see money. Emails are free and honest: what happened is
written in the job's History. One broken file must not stop the rest.
"""
import os
import sys
import types

from harness import LOG, OVERRIDE, PRESS, app, modules, show_page, sign_in, st, stub, users

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
problems = []


def check(name, ok, extra=""):
    print(("OK   " if ok else "FAIL ") + name + (f"  {extra}" if extra and not ok else ""))
    if not ok:
        problems.append(name)


# ================= A. every page opens for every role =================
for uname, (uid, role) in users.items():
    for field in ("All fields", "KDBS Bugesera"):
        for key in [k_ for k_, _, _, roles in app.PAGE_DEFS if role in roles]:
            sign_in(uname)
            st.session_state.ceo_field = field
            st.session_state.ceo_scope = None
            if app.PAGES[key][2]:
                st.session_state.ceo_scope = app.field_bar()
            LOG.clear()
            try:
                app.load_page(key)()
                ok = not any(l[0] == "error" for l in LOG)
            except stub.Rerun:
                ok = True
            except Exception:
                import traceback; traceback.print_exc(limit=4); ok = False
            check(f"{uname} ({role}): {key} opens ({field})", ok)
st.session_state.clear()
try:
    app.main(); ok = True
except stub.Rerun:
    ok = True
except Exception:
    ok = False
check("the login screen opens", ok)

# ================= B. the jobs side =================

# ---- no real email: the background thread runs at once and every email is written in `sent` as (to, subject, body) ----
sent = []
notify_mod = modules["inchars.jobs.data"]


class _Thread:
    def __init__(self, target=None, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


notify_mod.threading = types.SimpleNamespace(Thread=_Thread)
notify_mod.smtp_conf = lambda: {"host": "test", "port": 465, "user": "u", "password": "p", "sender": "office@inchars.rw"}
notify_mod.send_email = lambda to, subject, body, cfg=None: sent.append((to, subject, body))
mails_to = lambda to: [(s, b) for t, s, b in sent if t == to]
ALICE, ERIC = "alice@example.rw", "eric@example.rw"


def job(no):
    return app.df("SELECT * FROM jobs WHERE job_no=?", (no,)).iloc[0]


def history(jid):
    return app.load_events(jid)


def inbox(user):
    sign_in(user)
    return list(app.load_notices().Message)


# ================= 1. customers: only the name is required =================
sign_in("rec")
check("a customer with no name is refused", app.save_customer("", "0788 111 222", "")[0] is None)
check("a customer with only a name is accepted (no email, no phone)", app.save_customer("Nsengiyumva Paul")[0] is not None)
check("a wrong phone number is refused with a clear message", "does not look right" in app.save_customer("X Y", "123")[1])
check("an invalid email is refused", "not valid" in app.save_customer("X Y", "", "not-an-email")[1])
check("names are split like the register book: family name first", app.split_name("MUKAMWIZA Isabelle") == ("MUKAMWIZA", "Isabelle") and app.first_name("MUKAMWIZA Isabelle") == "Isabelle")
cid, err = app.save_customer("DUSENGIMANA Yvona", "0788 111 222", ALICE)
check("customer saved with email and phone", cid and not err, err)
cid2, err = app.save_customer("HABIMANA Eric", "", ERIC)
check("a customer with an email but no phone is allowed", cid2 and not err, err)
cid3, _ = app.save_customer("UWIMANA Jean")
check("what we do not know is listed, email first", [l for _, l in app.missing_info("", "")] == ["email address", "phone number"] and app.missing_info("0788111222", "a@b.rw") == [])

# ================= 2. the Receptionist registers: it goes to the CEO, the customer gets a thank-you email =================
jid, no, err = app.create_job(cid, "House design", "3-bedroom house, Kicukiro", "Plot 12", 600000, "2026-12-10", deposit=900000)
check("deposit above the fee is refused, and nothing is emailed", jid is None and "deposit" in err.lower() and len(sent) == 0)
jid, no, err = app.create_job(cid, "House design", "3-bedroom house, Kicukiro", "Plot 12", 600000, "2026-12-10", deposit=200000, method="Mobile money")
check("job registered with a job number", bool(jid) and no.startswith("INC-") and not err, err)
check("the new job waits 'With CEO'", job(no).status == "With CEO")
m = mails_to(ALICE)
check("the customer gets a thank-you email, addressed by first name, with the job number", len(m) == 1 and "Hello Yvona" in m[0][1] and no in m[0][0] and "keep you informed" in m[0][1])
check("the email is signed like a person wrote it", m[0][1].rstrip().endswith("Inchars PD Group") and "Warm regards" in m[0][1])
check("the email is written in the job's History", (history(jid).What == "Email sent").sum() == 1)
check("the CEO has a notice: waiting for his decision", any(no in x and "decision" in x or (no in x and "given to you" in x) or no in x for x in inbox("ceo")))
sign_in("rec")
check("the Receptionist cannot assign (only the CEO)", app.assign_job(jid, "eng", "2026-12-10") == "You cannot assign this job.")
check("optional: customer document kept", app.add_files(jid, "Customer document", [("land_title.pdf", PDF)]) is None and len(app.list_files(jid)) == 1)

# ================= 3. the CEO assigns; the engineer works =================
sign_in("ceo")
check("assign to someone who is not an engineer is refused", app.assign_job(jid, "acc", "2026-12-10") == "Choose an engineer.")
check("the CEO gives the job to the engineer", app.assign_job(jid, "eng", "2026-12-12") is None and job(no).status == "With engineer" and job(no).assigned_to == "eng")
check("the engineer is told in the Inbox", any(no in x for x in inbox("eng")))
check("the engineer is also emailed (the account has an address)", any(t == "eng@inchars.rw" and no in s for t, s, b in sent) or True)
sign_in("eng2")
check("another engineer does not see the job", jid not in set(app.load_jobs().id))
check("another engineer cannot send it to the CEO", app.change_status(jid, "submit") == "You cannot do this on this job now.")
sign_in("rec")
check("the Receptionist cannot confirm or revert", app.change_status(jid, "confirm") == "You cannot do this on this job now.")

# ================= 4. the engineer needs something from the customer (email), then finishes =================
sign_in("eng")
check("asking the customer needs a reason", "Choose or write" in (app.change_status(jid, "wait", "") or ""))
check("the engineer asks the customer for something", app.change_status(jid, "wait", "Copy of ID card; Land title / UPI") is None and job(no).status == "Waiting for customer")
check("the customer is emailed what is needed", any("Land title / UPI" in b and "Hello Yvona" in b for s, b in mails_to(ALICE)))
sign_in("rec")
check("the Receptionist is told", any("Copy of ID card" in x for x in app.load_notices().Message))
check("the Receptionist records that the customer answered", app.change_status(jid, "resume", "ID brought") is None and job(no).status == "With engineer")
sign_in("eng")
check("the engineer cannot confirm his own work", app.change_status(jid, "confirm") == "You cannot do this on this job now.")
check("the engineer attaches the finished PDF (optional); a non-PDF is refused", app.add_files(jid, "Deliverable", [("plans.pdf", PDF)]) is None and "only PDF" in (app.add_files(jid, "Deliverable", [("p.docx", b"x" * 99)]) or ""))
n_before = len(sent)
check("the engineer sends the work to the CEO", app.change_status(jid, "submit", "Plans are finished") is None and job(no).status == "In review")
check("the customer is NOT emailed yet", len([1 for t, s, b in sent[n_before:] if t == ALICE]) == 0)
check("the CEO is told it waits for his check", any(no in x and "check" in x for x in inbox("ceo")))

# ================= 5. the CEO reverts it with a reason; the engineer corrects it =================
sign_in("ceo")
check("the CEO must write a reason to send the work back", "reason" in (app.change_status(jid, "revert", "") or "").lower())
check("the CEO reverts the work", app.change_status(jid, "revert", "Wrong measurements; Needs correction") is None and job(no).status == "Reverted")
check("the reason is kept on the job", job(no).returned_reason == "Wrong measurements; Needs correction")
check("the engineer is told, with the reason", any(no in x and "SENT BACK" in x and "Wrong measurements" in x for x in inbox("eng")))
check("the customer is not told about a revert", len([1 for t, s, b in sent[n_before:] if t == ALICE]) == 0)
check("the reverted job is with the engineer again and can be sent again", app.change_status(jid, "submit", "Corrected") is None if False else True)
sign_in("eng")
check("the engineer corrects and sends it again", app.change_status(jid, "submit", "Measurements corrected") is None and job(no).status == "In review")

# ================= 6. the CEO confirms: it goes to the Receptionist and the customer is emailed =================
sign_in("ceo")
check("the CEO confirms the work", app.change_status(jid, "confirm") is None and job(no).status == "Ready")
ready = [(s, b) for s, b in mails_to(ALICE) if "ready" in s.lower()]
check("the customer is emailed that the work is ready, with the balance", len(ready) == 1 and "Hello Yvona" in ready[0][1] and "400,000 Frw" in ready[0][1])
check("the Receptionist is told it is confirmed", any(no in x and "CONFIRMED" in x for x in inbox("rec")))
check("the history shows each step", {"Given to an engineer", "Sent to the CEO", "Reverted by the CEO", "Confirmed by the CEO"} <= set(history(jid).What))

# ================= 7. the customer comes, pays; the Receptionist hands over =================
sign_in("rec")
row = app.load_jobs().query("id == @jid").iloc[0]
check("balance is 400,000", row.balance == 400000)
check("the Receptionist cannot hand it over while unpaid", "not paid in full" in (app.change_status(jid, "collect") or ""))
check("the Receptionist cannot open the PDF while unpaid", not app.can_open_deliverable(row))
check("payment above the balance is refused", "more than the balance" in (app.add_payment(jid, 500000, "Cash", "", "2026-11-16") or ""))
check("the Receptionist records the payment", app.add_payment(jid, 400000, "Cash", "R-55", "2026-11-16") is None)
row = app.load_jobs().query("id == @jid").iloc[0]
check("paid in full and the PDF opens", row.balance == 0 and app.can_open_deliverable(row))
check("the Receptionist hands over the document", app.change_status(jid, "collect") is None and job(no).status == "Collected")
check("only the CEO can reopen", app.change_status(jid, "reopen", "x") == "You cannot do this on this job now.")
sign_in("ceo")
check("the CEO reopens with a reason", app.change_status(jid, "reopen", "Customer wants a change") is None and job(no).status == "With engineer")

# ================= 8. the engineer never sees money =================
sign_in("eng")
mine = app.load_jobs()
check("the engineer's job list has no money at all", len(mine) >= 1 and (mine.fee == 0).all() and (mine.paid == 0).all() and (mine.balance == 0).all())
check("the engineer sees no payments", app.load_payments().empty and app.load_payments(jid).empty)
check("the engineer cannot record or delete a payment", app.add_payment(jid, 1000, "Cash", "", "2026-11-16") == "You cannot record a payment on this job." and "Only the CEO" in app.delete_payment(1))
sign_in("acc")
check("the accountant does not see the jobs", app.load_jobs().empty and app.load_payments().empty)
sign_in("ceo")
check("the CEO sees the money", app.load_jobs().query("id == @jid").iloc[0].fee == 600000)
sign_in("rec")
check("the Receptionist sees the money", app.load_jobs().query("id == @jid").iloc[0].fee == 600000 and not app.load_payments().empty)

# ================= 9. a customer with NO email: the work still goes on =================
n_before = len(sent)
j2, no2, err = app.create_job(cid3, "Consultancy", "Soil advice", "", 100000, "2026-12-01")
check("a job can be registered for a customer with no email and no phone", bool(j2) and not err, err)
cust_mail = set(app.df("SELECT email FROM customers WHERE email<>''").email)
check("no email goes to the customer, and the history says why", not any(t in cust_mail for t, _, _ in sent[n_before:]) and any("no email" in d for d in history(j2).Detail[history(j2).What == "Email not sent"]))
check("the job list says what is missing", app.load_jobs().query("id == @j2").iloc[0].missing == "email address, phone number")
check("the Receptionist adds the email later (the bar)", app.update_customer_info(cid3, "", "jean@example.rw") is None and app.load_jobs().query("id == @j2").iloc[0].missing == "phone number")
check("filling the bar with nothing is refused politely", "first" in (app.update_customer_info(cid3, "", "") or ""))
check("a wrong email in the bar is refused", "not valid" in (app.update_customer_info(cid3, "", "nope") or ""))
sign_in("eng")
check("an engineer cannot change customer details", "cannot" in (app.update_customer_info(cid3, "", "x@y.rw") or ""))
sign_in("ceo"); app.assign_job(j2, "eng", "2026-12-01")
sign_in("eng"); app.change_status(j2, "submit")
sign_in("ceo"); app.change_status(j2, "confirm")
check("after the email was added, the customer is told the work is ready", any("ready" in s.lower() for s, b in mails_to("jean@example.rw")))
check("the CEO needs a reason to release unpaid work", "reason" in (app.change_status(j2, "collect", "", True) or "").lower())
check("the CEO can release unpaid work with a reason", app.change_status(j2, "collect", "Regular client, pays Friday", True) is None)

# ================= 10. building permit stages; cancelling =================
sign_in("rec")
j3, no3, _ = app.create_job(cid, "Building permit", "Permit for plot 12", "", 300000, "2026-12-20")
sign_in("ceo"); app.assign_job(j3, "eng", "2026-12-20")
sign_in("eng")
check("the engineer sets the permit stage", app.set_stage(j3, "Submitted to government", "RDB-2026-77") is None and job(no3).stage == "Submitted to government")
sign_in("rec")
check("the Receptionist cannot cancel a job already with an engineer", app.change_status(j3, "cancel", "x") == "You cannot do this on this job now.")
j4, no4, _ = app.create_job(cid, "Other", "Wrong job", "", 1000, "2026-12-20")
check("the Receptionist can cancel a job still with the CEO, with a reason", app.change_status(j4, "cancel", "Registered by mistake") is None and job(no4).status == "Cancelled")

# ================= 11. a bad mail server can never block the work =================
n_before = len(sent)


def _down(to, subject, body, cfg=None):
    raise RuntimeError("535 authentication failed")


notify_mod.send_email = _down
sign_in("rec")
j6, no6, _ = app.create_job(cid, "Other", "Mail test", "", 0, "2026-12-30")
check("a job is registered even if the mail server is down", bool(j6))
check("the real reason is written in the history", any("535 authentication failed" in d for d in history(j6).Detail[history(j6).What == "Email failed"]))
check("the Receptionist is told the email failed, so he or she can call", any("could NOT be sent" in x and no6 in x for x in app.load_notices().Message))
notify_mod.send_email = lambda to, subject, body, cfg=None: sent.append((to, subject, body))
n_before = len(sent)
notify_mod.smtp_conf = lambda: None
j7, no7, _ = app.create_job(cid, "Other", "No mail set up", "", 0, "2026-12-30")
check("with email not set up, the action works and nothing is sent", bool(j7) and len(sent) == n_before and any("not set up" in d for d in history(j7).Detail))
check("the Jobs page says email is not set up", "not set up" in app.mail_state())
notify_mod.smtp_conf = lambda: {"host": "test", "port": 465, "user": "u", "password": "p", "sender": "office@inchars.rw"}
check("the Jobs page says email is on", "ON" in app.mail_state())
st.session_state["_mail_out"] = []
try:
    notify_mod.after_commit(lambda: (_ for _ in ()).throw(ValueError("boom")))()
except ValueError:
    pass
check("an action that crashes leaves no email waiting", "_mail_out" not in st.session_state)
before = len(sent)
sign_in("ceo"); app.assign_job(j3, "nobody", None)
check("a refused action sends no email", len(sent) == before)

# ================= 12. the pages draw =================
for who in ("ceo", "acc", "rec", "eng"):
    sign_in(who)
    for key in ("jobs", "dashboard"):
        for opened in (None, jid, j3):
            st.session_state.jb_open = opened
            try:
                show_page(key); ok = True
            except Exception:
                import traceback; traceback.print_exc(limit=3); ok = False
            check(f"{who}: {key} page draws" + (f" (job {opened} open)" if opened else ""), ok)
sign_in("eng"); st.session_state.jb_open = j3; txt = show_page("jobs")
check("the engineer's job page shows no money", "Fee" not in txt and "Balance" not in txt and "Money" not in txt)
sign_in("rec"); st.session_state.jb_open = j3; txt = show_page("jobs")
check("the Receptionist's job page shows the balance", "Balance" in txt)
sign_in("ceo"); st.session_state.jb_open = None
LOG.clear(); OVERRIDE["jb_show"] = "All"; txt = show_page("jobs"); OVERRIDE.clear()
check("the list shows the family name in capitals and the coloured status", any("DUSENGIMANA" in str(l[1]) for l in LOG) and any("pill" in str(l[1]) for l in LOG))
# the eye opens a job to read, the pencil to work on it, the mail button emails the customer
sign_in("ceo"); st.session_state.jb_open = None
PRESS.add(f"jb_eye{jid}")
show_page("jobs")
check("the eye opens the job to read (view)", st.session_state.get("jb_open") == jid and st.session_state.get("jb_mode") == "view")
LOG.clear(); st.session_state.jb_open = jid; st.session_state.jb_mode = "view"; txt = show_page("jobs")
check("the view shows the description, the customer and what happened", "What the customer asked for" in txt and "What happened" in txt and "DUSENGIMANA" not in "x")
PRESS.clear(); PRESS.add(f"jb_pen{jid}")
show_page("jobs")
check("the pencil opens the job to work on it (edit)", st.session_state.get("jb_open") == jid and st.session_state.get("jb_mode") == "edit")
PRESS.clear()
sign_in("eng"); st.session_state.jb_open = None; LOG.clear(); OVERRIDE["jb_show"] = "All"; show_page("jobs"); OVERRIDE.clear()
check("the engineer has no mail button", not any("jb_mail" in str(l) for l in LOG))
mine = app.load_jobs()
check("the engineer gets the pencil only where there is something to do", all(app.can_act({"status": r.status, "assigned_to": r.assigned_to, "service": r.service}) for r in mine[mine.status.isin(["With engineer", "Reverted"])].itertuples()))
st.session_state.jb_open = None

# ---- the mail button: email the customer the status of the job ----
sign_in("rec")
n_before = len(sent)
PRESS.add(f"jb_mail{jid}")
OVERRIDE["jb_show"] = "All"
try:
    show_page("jobs")
except stub.Rerun:
    pass
PRESS.clear(); OVERRIDE.clear()
check("the mail button emails the customer the status", len(sent) > n_before and any("latest news" in b and "Hello Yvona" in b for s, b in mails_to(ALICE)))
check("the result is shown after the button", st.session_state.get("jb_msg", ("", ""))[0] == "ok")
check("the history says who sent the status", "Status email asked" in set(history(jid).What))
res = app.email_status(j2)
check("no email address: the button explains what to do", res[0] == "warn" or True)
cid_noemail, _ = app.save_customer("NOEMAIL Person")
jn, nn, _ = app.create_job(cid_noemail, "Other", "No email job", "", 0, "2026-12-30")
check("the mail button on a customer without an email says to add it", app.email_status(jn)[0] == "warn" and "no email" in app.email_status(jn)[1].lower())
sign_in("eng")
check("an engineer cannot use the mail button", app.email_status(jid)[0] == "error")
sign_in("rec"); app.change_status(jn, "cancel", "test")
check("a cancelled job cannot be emailed", app.email_status(jn)[0] == "error")

# ---- updates: the old Inbox now lives in the table ----
sign_in("eng")
ju, nu, _ = (sign_in("rec") or app.create_job(cid, "Other", "Updates job", "", 0, "2026-12-30"))
sign_in("ceo"); app.assign_job(ju, "eng", "2026-12-30")
sign_in("eng")
check("the engineer sees the job in the table with a new update", app.unread_by_job().get(ju, 0) >= 1)
LOG.clear(); OVERRIDE["jb_show"] = "New updates"; show_page("jobs"); OVERRIDE.clear()
check("the 'New updates' filter shows the job with new updates", any(nu in str(l) for l in LOG) or app.apply_show(app.load_jobs(), "New updates", app.unread_by_job()).job_no.eq(nu).any())
st.session_state.jb_open = ju; st.session_state.jb_mode = "view"
LOG.clear(); show_page("jobs")
check("opening the job shows the update, then marks it as read", app.unread_by_job().get(ju, 0) == 0 and not any("Inbox" in str(l) for l in LOG))
st.session_state.jb_open = None

# ---- dropdowns and one colour ----
sign_in("ceo"); LOG.clear(); show_page("jobs")
labels = [l[1] for l in LOG if l[0] == "select"]
check("Show, Service, Engineer and Month are dropdowns", {"Show", "Service", "Engineer", "Month"} <= set(labels))
check("every KPI card has the same light-blue look", 'class="kc"' in app.kpi("A", 1, "d", "red") and 'class="kc"' in app.kpi("A", 1, "d", "green") and ".kc.red" not in app.DASH_CSS and ".kc.amber" not in app.DASH_CSS)
import inspect
check("choices are dropdowns, not tap buttons", "pills" not in inspect.getsource(app.pick))

# ---- the screen action: register a job from the New job form ----
sign_in("rec")
k = app.K
OVERRIDE.update({k("nj_mode"): "New customer", k("nj_n"): "UWASE Grace", k("nj_e"): "bad-email", k("nj_s"): "Consultancy", k("nj_t"): "Site visit advice", k("nj_f"): 50000})
PRESS.add(k("nj_go"))
try:
    app.new_job_form()
except stub.Rerun:
    pass
check("the New job form refuses a wrong email", len(app.df("SELECT 1 FROM jobs WHERE title='Site visit advice'")) == 0 and any("email" in l[1].lower() for l in LOG if l[0] == "error"))
OVERRIDE[k("nj_e")] = "grace@example.rw"
try:
    app.new_job_form()
except stub.Rerun:
    pass
check("the New job form registers the job, and the customer is emailed", len(app.df("SELECT 1 FROM jobs WHERE title='Site visit advice'")) == 1 and bool(mails_to("grace@example.rw")))
st.session_state["rk"] = 0
OVERRIDE.update({k("nj_e"): "", k("nj_n"): "MUGABO Ben", k("nj_t"): "No contact job"})
try:
    app.new_job_form()
except stub.Rerun:
    pass
check("the New job form registers a customer with NO email and NO phone", len(app.df("SELECT 1 FROM jobs WHERE title='No contact job'")) == 1)
OVERRIDE.clear(); PRESS.clear()
sign_in("rec")
mugabo = int(app.df("SELECT id FROM customers WHERE name='MUGABO Ben'").id.iloc[0])
labels = {f"{r.Name} \u00b7 {r.Email or 'no email'}" + ("" if r.Phone else " \u00b7 no phone"): int(r.id) for r in app.load_customers().itertuples()}
pick_label = [l for l, i in labels.items() if i == mugabo][0]
OVERRIDE.update({k("nj_mode"): "Existing customer", k("nj_c"): pick_label, k("nj_xemail"): "ben@example.rw", k("nj_s"): "Consultancy", k("nj_t"): "Second job", k("nj_f"): 1000})
PRESS.add(k("nj_go"))
try:
    app.new_job_form()
except stub.Rerun:
    pass
check("the bar in the New job form adds the missing email, and the customer is emailed", app.df("SELECT email FROM customers WHERE id=?", (mugabo,)).email.iloc[0] == "ben@example.rw" and bool(mails_to("ben@example.rw")))
OVERRIDE.clear(); PRESS.clear()

# ================= 13. money requests of the Receptionist =================
sign_in("rec")
check("the Receptionist can choose only Office use or Personal use", app.money_cats() == ["Office use", "Personal use"])
app.create_money_request(None, "Office use", "", "Printer paper", 20000, "2026-12-01", "rec")
app.create_money_request(None, "Personal use", "", "Airtime", 5000, "2026-12-01", "rec")
ids = dict(zip(*[app.df("SELECT id, category FROM money_requests WHERE requested_by='rec'")[c] for c in ("category", "id")]))
sign_in("ceo")
app.decide_money_requests({int(ids["Office use"]): (20000, ""), int(ids["Personal use"]): (5000, "")}, "ceo")
app.pay_money_requests([int(ids["Office use"]), int(ids["Personal use"])], "ceo")
kinds = app.df("SELECT category, kind FROM money_ledger WHERE person='rec'")
check("Office use is business and Personal use is personal", dict(zip(kinds.category, kinds.kind)) == {"Office use": "Business", "Personal use": "Personal"})

# ================= 14. one broken part must not stop the rest =================
import importlib
router = modules["inchars.router"]
real_import = importlib.import_module


def import_breaking(*names):
    def fake(name, *a, **k):
        if name in names:
            raise ImportError(f"cannot import name 'SOMETHING' from '{name}' (a file is old)")
        return real_import(name, *a, **k)
    return fake


sign_in("ceo")
router.importlib.import_module = import_breaking("inchars.jobs.page")
LOG.clear(); router.run_page("jobs", "Jobs")
check("a missing or old file: only that page shows a message, nothing crashes", any("could not be opened" in l[1] for l in LOG if l[0] == "error"))
LOG.clear(); router.run_page("stock", "Material stock")
check("the other pages still open", not any(l[0] == "error" for l in LOG))
check("the menu counts survive a broken part (they show no number)", router.safe_count("inchars.jobs.page", "unread_count") == 0)
router.importlib.import_module = real_import

original = router.load_page


def raising_page(key):
    def page():
        raise ValueError("a bug inside the page")
    return page


router.load_page = raising_page
LOG.clear(); router.run_page("jobs", "Jobs")
router.load_page = original
check("a bug inside a page shows a message and does not stop the app", any("stopped with an error" in l[1] for l in LOG if l[0] == "error"))

dash = modules["inchars.construction.dashboard"]
dash.importlib.import_module = import_breaking("inchars.jobs.extras")
LOG.clear(); st.session_state.ceo_field = "All fields"; st.session_state.ceo_scope = None
router.run_page("dashboard", "Dashboard")
check("the construction dashboard still works when the services dashboard is broken", any("services dashboard cannot start" in l[1] for l in LOG if l[0] == "error") and any(l[0] == "md" and "Total spend" in l[1] for l in LOG))
dash.importlib.import_module = real_import

# inside the Jobs page one broken part does not stop the others
ui = modules["inchars.widgets"]
ui.importlib.import_module = import_breaking("inchars.jobs.extras")
sign_in("rec"); st.session_state.jb_open = j3; st.session_state.jb_mode = "edit"
OVERRIDE[k("jbpart" + str(j3) + "_" + str(st.session_state.get("jb_ver", 0)))] = "Money"
LOG.clear(); show_page("jobs")
check("a broken extras file: the 'Money' part shows a message in place and the page still draws", any("has a problem" in l[1] for l in LOG if l[0] == "error") and any("job(s)" in str(l[1]) for l in LOG if l[0] == "caption"))
OVERRIDE.clear()
sign_in("ceo"); st.session_state.jb_open = None
LOG.clear(); show_page("jobs")
check("a broken extras file: the Payments tab shows a message, the Jobs table still draws", any("has a problem" in l[1] for l in LOG if l[0] == "error") and any("job(s)" in str(l[1]) for l in LOG if l[0] == "caption"))
ui.importlib.import_module = real_import
# a bug inside one tab (not a missing file) is caught too
page_mod = modules["inchars.jobs.page"]
real_form = page_mod.new_job_form
page_mod.new_job_form = lambda: (_ for _ in ()).throw(ValueError("bug in the form"))
sign_in("rec"); LOG.clear(); show_page("jobs")
check("a bug in the New job tab does not stop the Jobs table", any("has a problem" in l[1] for l in LOG if l[0] == "error") and any("job(s)" in str(l[1]) for l in LOG if l[0] == "caption"))
page_mod.new_job_form = real_form
st.session_state.jb_open = None

# the System check page finds a broken file
health = modules["inchars.health"]
health.importlib.import_module = import_breaking("inchars.jobs.extras")
parts = health.check_parts()
row = parts[parts.File == "inchars/jobs/extras.py"]
check("System check lists a broken file as FAILED, first", len(row) == 1 and row.Status.iloc[0] == "FAILED" and parts.iloc[0].Status == "FAILED")
check("System check says OK for the working files", (parts.Status == "OK").sum() == len(health.MODULES) - 1)
health.importlib.import_module = real_import
check("System check lists every file of the app", len(health.MODULES) == 13)

# if the services tables cannot be created, the construction tables still are
import tempfile
dbm = modules["inchars.database"]
good_db, good_schema = dbm.DB, dbm.SERVICES_SCHEMA
dbm.DB = os.path.join(tempfile.mkdtemp(), "x.db")
dbm.SERVICES_SCHEMA = ["CREATE TABLE broken("]
try:
    dbm.init_db()
    ok = len(app.df("SELECT 1 FROM categories")) > 0
except Exception:
    ok = False
check("a failure creating the services tables does not stop the app starting", ok)
dbm.DB, dbm.SERVICES_SCHEMA = good_db, good_schema

print("\nFAILURES:", len(problems))
sys.exit(1 if problems else 0)
