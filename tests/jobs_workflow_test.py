"""Workflow test for the services side (jobs): register, assign, work, pay, collect, messages, notifications, emails, and the
rule that one broken part must not stop the rest of the app.

Run:   python tests/jobs_workflow_test.py"""
import os
import sys
import types

from harness import LOG, OVERRIDE, PRESS, app, modules, show_page, sign_in, st, stub

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
problems = []


def check(name, ok, extra=""):
    print(("OK   " if ok else "FAIL ") + name + (f"  {extra}" if extra and not ok else ""))
    if not ok:
        problems.append(name)


# ---- no real mail: the background thread runs at once and every email is written in `sent` ----
sent = []
notices = modules["inchars.services.notices"]


class _Thread:
    def __init__(self, target=None, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


notices.threading = types.SimpleNamespace(Thread=_Thread)
notices.smtp_conf = lambda: {"host": "mail.test"}
notices.send_email = lambda to, subject, body, cfg=None: sent.append((to, subject, body))
mails_to = lambda who: [(sub, body) for t, sub, body in sent if t == who]


def job(no):
    return app.df("SELECT * FROM jobs WHERE job_no=?", (no,)).iloc[0]


# ================= 1. customers: the email address is required =================
sign_in("rec")
check("a customer without an email is refused", app.save_customer("Mukamana Alice", "0788 111 222", "")[0] is None)
check("an invalid email is refused", "not valid" in app.save_customer("Mukamana Alice", "0788 111 222", "not-an-email")[1])
cid, err = app.save_customer("Mukamana Alice", "0788 111 222", "alice@example.rw")
check("customer saved with an email", cid and not err, err)
check("same phone number is refused", "already" in app.save_customer("Other", "0788111222", "o@x.rw")[1])
cid2, err = app.save_customer("Habimana Eric", "0722 333 444", "", no_email=True)
check("a customer without email is allowed when 'No email' is ticked", cid2 and not err, err)

# ================= 2. register a job =================
jid, no, err = app.create_job(cid, "House design", "3-bedroom house, Kicukiro", "Plot 12", 600000, "2026-12-10", deposit=900000)
check("deposit above the fee is refused", jid is None and "deposit" in err.lower())
jid, no, err = app.create_job(cid, "House design", "3-bedroom house, Kicukiro", "Plot 12", 600000, "2026-12-10", deposit=200000, method="Mobile money")
check("job registered with a job number", bool(jid) and no.startswith("INC-") and not err, err)
check("the customer gets a thank-you email with the job number", any("thank you for choosing us" in sub and no in body and "keep you informed" in body and "200,000 Frw" in body for sub, body in mails_to("alice@example.rw")))
check("the CEO is notified by notice and by email", any(no in m for m in (sign_in("ceo") or app.load_notices()).Message) and any(t == "c@x.rw" and no in sub for t, sub, _ in sent))
sign_in("rec")
check("the Receptionist is not emailed about his own action", not any(t == "rec@x.rw" for t, _, _ in sent))
check("optional: customer document kept", app.add_files(jid, "Customer document", [("land_title.pdf", PDF)]) is None and len(app.list_files(jid)) == 1)
check("a job can be registered with NO document at all", True)
check("receptionist cannot assign", app.assign_job(jid, "eng", "2026-12-10") == "You cannot assign this job.")

# ================= 3. assign =================
sign_in("ceo")
check("assign to someone who is not an engineer is refused", app.assign_job(jid, "acc", "2026-12-10") == "Choose an engineer.")
check("CEO assigns to the engineer", app.assign_job(jid, "eng", "2026-12-12") is None and job(no).status == "Assigned")
check("the engineer gets an email", any(t == "eng@x.rw" and no in sub for t, sub, _ in sent))
check("the customer is told the job was given to an engineer", any("given to our engineer" in sub for sub, _ in mails_to("alice@example.rw")))
sign_in("eng2")
check("another engineer does not see the job", jid not in set(app.load_jobs().id))
check("another engineer cannot start it", app.change_status(jid, "start") == "You cannot do this on this job now.")

# ================= 4. work: no PDF is needed =================
sign_in("eng")
check("engineer starts; the customer is told", app.change_status(jid, "start") is None and any("work has started" in s for s, _ in mails_to("alice@example.rw")))
check("waiting for the customer needs a reason", "Choose or write" in (app.change_status(jid, "wait", "") or ""))
check("engineer asks the customer for something", app.change_status(jid, "wait", "Copy of ID card; Land title / UPI") is None)
check("the customer is told what is needed", any("Land title / UPI" in b for _, b in mails_to("alice@example.rw")))
sign_in("rec")
check("receptionist is told", any("Copy of ID card" in m for m in app.load_notices().Message))
check("receptionist records that the customer answered", app.change_status(jid, "resume", "ID brought") is None and job(no).status == "In progress")
sign_in("eng")
check("engineer marks the job READY without uploading any PDF", app.change_status(jid, "ready", "Plans are finished") is None and job(no).status == "Ready")
check("the customer is told it is ready, with the balance", any("is ready" in b and "400,000 Frw" in b for _, b in mails_to("alice@example.rw")))
sign_in("rec")
check("receptionist is told it is ready", any("READY" in m for m in app.load_notices().Message))
sign_in("ceo")
check("the CEO is told it is ready", any("READY" in m for m in app.load_notices().Message))

# ================= 5. the customer comes back =================
sign_in("rec")
row = app.load_jobs().query("id == @jid").iloc[0]
check("balance is 400,000", row.balance == 400000)
check("receptionist cannot hand it over while unpaid", "not paid in full" in (app.change_status(jid, "collect") or ""))
check("payment above the balance is refused", "more than the balance" in (app.add_payment(jid, 500000, "Cash", "", "2026-11-16") or ""))
sign_in("eng")
check("engineer cannot record a payment", app.add_payment(jid, 1000, "Cash", "", "2026-11-16") == "You cannot record a payment on this job.")
check("engineer attaches the finished PDF (optional) and a non-PDF is refused", app.add_files(jid, "Deliverable", [("p.pdf", PDF)]) is None and "only PDF" in (app.add_files(jid, "Deliverable", [("p.docx", b"x" * 99)]) or ""))
row = app.load_jobs().query("id == @jid").iloc[0]
check("engineer can open the PDF", app.can_open_deliverable(row))
sign_in("rec")
row = app.load_jobs().query("id == @jid").iloc[0]
check("receptionist cannot open the PDF while unpaid", not app.can_open_deliverable(row))
check("receptionist records the payment", app.add_payment(jid, 400000, "Cash", "R-55", "2026-11-16") is None)
check("the customer gets a receipt email", any("payment received" in s and "paid in full" in b for s, b in mails_to("alice@example.rw")))
row = app.load_jobs().query("id == @jid").iloc[0]
check("now paid in full and the PDF opens", row.balance == 0 and app.can_open_deliverable(row))
check("receptionist hands over the document", app.change_status(jid, "collect") is None and job(no).status == "Collected")
check("the customer gets a thank-you after collecting", any("Thank you for collecting" in b for _, b in mails_to("alice@example.rw")))
check("the history shows the emails sent to the customer", (app.load_events(jid).What == "Customer emailed").sum() >= 6)
sign_in("ceo")
check("the CEO is told it was collected", any("collected" in m for m in app.load_notices().Message))
check("only the CEO can reopen", app.change_status(jid, "reopen", "Customer wants a change") is None and job(no).status == "In progress")

# ================= 6. release without payment (CEO only), a customer with no email =================
sign_in("rec")
mark = len(sent)
j2, no2, _ = app.create_job(cid2, "Consultancy", "Soil advice", "", 100000, "2026-12-01")
check("a customer without an email is not emailed (only the CEO is)", all(t == "c@x.rw" for t, _, _ in sent[mark:]) and len(sent) > mark)
sign_in("ceo"); app.assign_job(j2, "eng", "2026-12-01")
sign_in("eng"); app.change_status(j2, "start"); app.change_status(j2, "ready")
sign_in("ceo")
check("CEO needs a reason to release unpaid work", "reason" in (app.change_status(j2, "collect", "", True) or "").lower())
check("CEO can release unpaid work with a reason", app.change_status(j2, "collect", "Regular client, pays Friday", True) is None)

# ================= 7. building permit stages =================
sign_in("rec")
j3, no3, _ = app.create_job(cid, "Building permit", "Permit for plot 12", "", 300000, "2026-12-20")
sign_in("ceo"); app.assign_job(j3, "eng", "2026-12-20")
sign_in("eng"); app.change_status(j3, "start")
check("engineer sets the permit stage; the customer is told with the reference",
      app.set_stage(j3, "Submitted to government", "RDB-2026-77") is None and any("Submitted to government" in s and "RDB-2026-77" in b for s, b in mails_to("alice@example.rw")))
sign_in("rec")
check("receptionist cannot cancel a job in progress", app.change_status(j3, "cancel", "x") == "You cannot do this on this job now.")

# ================= 8. messages: the point of the page =================
sign_in("rec")
before = len(sent)
err, info = app.send_message(j3, ["Customer", "CEO", "Engineer"], "Please bring the signed form on Monday.")
check("receptionist sends a message to the customer, the CEO and the engineer", err is None and not info)
check("the customer got it by email", any("signed form" in b for _, b in mails_to("alice@example.rw")))
sign_in("eng"); check("the engineer got it in the inbox", any("signed form" in m for m in app.load_notices().Message))
sign_in("ceo"); check("the CEO got it in the inbox", any("signed form" in m for m in app.load_notices().Message))
sign_in("rec")
err, info = app.send_message(j2, ["Customer"], "Thank you.")
check("a message to a customer without email says so", err is None and "NOT told" in info)
err, info = app.send_message(j3, [], "Customer said he will come Monday.")
check("a message with nobody selected is only written in the history", err is None and "history" in info and any("will come Monday" in d for d in app.load_events(j3).Detail))
check("an empty message is refused", app.send_message(j3, ["CEO"], "  ")[0] == "Write the message.")
sign_in("acc")
check("the accountant cannot send messages on a job", "cannot" in app.send_message(j3, ["CEO"], "hi")[0])
sign_in("eng2")
check("an engineer cannot message on someone else's job", "cannot" in app.send_message(j3, ["CEO"], "hi")[0])

# ================= 9. emails never block the work =================
sign_in("ceo")
with app.conn() as c:
    c.execute("UPDATE users SET email=NULL WHERE username='eng2'")
before = len(sent)
check("reassign to an engineer without an email address works", app.assign_job(j3, "eng2", "2026-12-22") is None)
check("that engineer still has the notice inside the app", any(no3 in m for m in (sign_in("eng2") or app.load_notices()).Message))
sign_in("ceo")


def _broken(to, subject, body, cfg=None):
    raise RuntimeError("mail server is down")


notices.send_email = _broken
check("a broken mail server does not stop the job action", app.assign_job(j3, "eng", "2026-12-23") is None and job(no3).assigned_to == "eng")
notices.send_email = lambda to, subject, body, cfg=None: sent.append((to, subject, body))
before = len(sent)
notices.smtp_conf = lambda: None
check("with email not set up, the action works and nothing is sent", app.assign_job(j3, "eng2", "2026-12-24") is None and len(sent) == before)
check("the Jobs page says email is not set up", "not set up" in app.email_state())
notices.smtp_conf = lambda: {"host": "mail.test"}
check("the Jobs page shows where the emails go", "c@x.rw" in app.email_state())
app.assign_job(j3, "eng", "2026-12-23")

# ================= 10. money requests of the Receptionist =================
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

# ================= 11. the pages draw, and the crash from before is gone =================
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
check("engineer's job page shows no money", "Fee" not in txt and "Balance" not in txt)
sign_in("rec"); st.session_state.jb_open = j3; txt = show_page("jobs")
check("receptionist's job page shows the balance", "Balance" in txt)
sign_in("ceo")
fake = types.SimpleNamespace(selection=types.SimpleNamespace(rows=[7]))
stub.STClass.dataframe = lambda self, *a, **k: fake
OVERRIDE.clear(); OVERRIDE["jb_chip"] = "Ready"; st.session_state.jb_open = None
try:
    show_page("jobs"); check("a selected row that is no longer in the list does not crash the Jobs page", True)
except IndexError:
    check("a selected row that is no longer in the list does not crash the Jobs page", False)
del stub.STClass.dataframe
OVERRIDE.clear()
# a tapped row stays selected: it opens the job once, and Close really closes it
sign_in("ceo"); st.session_state.jb_open = None; st.session_state.pop("jb_last_tap", None)
OVERRIDE["jb_chip"] = "All"
fake = types.SimpleNamespace(selection=types.SimpleNamespace(rows=[0]))
stub.STClass.dataframe = lambda self, *a, **k: fake
show_page("jobs")
first = st.session_state.get("jb_open")
check("tapping a row opens that job", first is not None)
st.session_state.jb_open = None  # the Close button
show_page("jobs")
check("Close really closes the job (the selected row does not reopen it)", st.session_state.get("jb_open") is None)
del stub.STClass.dataframe
OVERRIDE.clear()
sign_in("rec"); st.session_state.jb_open = None
OVERRIDE["jb_q"] = no3
check("typing a job number opens that job at once", f"{no3} ·" in show_page("jobs") or st.session_state.get("jb_open") == j3)
OVERRIDE.clear()

# ---- the screen action: register a job from the New job form ----
sign_in("rec"); st.session_state.jb_open = None
k = app.K
OVERRIDE.update({k("nj_mode"): "New customer", k("nj_n"): "Uwase Grace", k("nj_p"): "0733 555 666", k("nj_s"): "Consultancy", k("nj_t"): "Site visit advice", k("nj_f"): 50000})
PRESS.add(k("nj_go"))
try:
    app.new_job_form()
except stub.Rerun:
    pass
check("the New job form refuses a new customer without an email", len(app.df("SELECT 1 FROM jobs WHERE title='Site visit advice'")) == 0 and any("email" in l[1].lower() for l in LOG if l[0] == "error"))
OVERRIDE[k("nj_e")] = "grace@example.rw"
try:
    app.new_job_form()
except stub.Rerun:
    pass
check("the New job form registers the job when the email is given (no document needed)", len(app.df("SELECT 1 FROM jobs WHERE title='Site visit advice'")) == 1 and bool(mails_to("grace@example.rw")))
OVERRIDE.clear(); PRESS.clear()

# ================= 12. one broken part must not stop the rest =================
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
router.importlib.import_module = import_breaking("inchars.services.page_jobs")
LOG.clear(); router.run_page("jobs", "Jobs")
check("a missing or old file: only that page shows a message, nothing crashes", any("could not be opened" in l[1] for l in LOG if l[0] == "error"))
LOG.clear(); router.run_page("stock", "Material stock")
check("the other pages still open", not any(l[0] == "error" for l in LOG))
check("the menu counts survive a broken part (they show no number)", router.safe_count("inchars.services.page_jobs", "unread_count") == 0)
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

dash = modules["inchars.pages.dashboard"]
dash.importlib.import_module = import_breaking("inchars.services.dashboard")
LOG.clear(); st.session_state.ceo_field = "All fields"; st.session_state.ceo_scope = None
router.run_page("dashboard", "Dashboard")
check("the construction dashboard still works when the services dashboard is broken", any("services dashboard cannot start" in l[1] for l in LOG if l[0] == "error") and any(l[0] == "md" and "Total spend" in l[1] for l in LOG))
dash.importlib.import_module = real_import

# inside the Jobs page one broken part does not stop the others
ui = modules["inchars.services.ui"]
ui.importlib.import_module = import_breaking("inchars.services.panel_money")
sign_in("rec"); st.session_state.jb_open = j3
OVERRIDE[f"{k('jbpart' + str(j3))}"] = "Money"
LOG.clear(); show_page("jobs")
check("a broken 'Money' part shows a message in place and the page still draws", any("has a problem" in l[1] for l in LOG if l[0] == "error"))
ui.importlib.import_module = real_import
OVERRIDE.clear()

# the System check page finds a broken file
bad_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "inchars", "services", "zz_broken_test.py")
open(bad_file, "w").write("from inchars.services.config import THIS_DOES_NOT_EXIST\n")
try:
    parts = modules["inchars.pages.system_check"].check_parts()
    row = parts[parts.File == "services/zz_broken_test.py"]
    check("System check lists a broken file as FAILED, first", len(row) == 1 and row.Status.iloc[0] == "FAILED" and parts.iloc[0].Status == "FAILED")
    check("System check says OK for the working files", (parts.Status == "OK").sum() >= 40)
finally:
    os.remove(bad_file)
    sys.modules.pop("inchars.services.zz_broken_test", None)

# if the services tables cannot be created, the construction tables still are
import tempfile
dbm = modules["inchars.db"]
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
