# Inchars PD Group - Construction Management System

Cost, funds and field management for Inchars PD Group Ltd. One system for all fields (construction sites).
Built with Streamlit. Data is stored in PostgreSQL (Neon). Without `DATABASE_URL` it uses a local
`construction.db` file, which is only meant for testing on one computer.

## Who can do what

A person can have **several roles** and gets the pages of every role they have. The CEO changes roles and fields in
**Users** (click a name, or choose it from the list); the person gets the new pages the next time they open a page.
Accountant, Store manager and Field manager work only on the field(s) the CEO gives them. Receptionist and Engineer work on services and need no field. Funds received are visible to the CEO only.

| Role | Main pages |
|---|---|
| CEO (sees all fields) | Dashboard of all fields (with a field drop-down and all charts), Material stock, Material requests (approve), Money requests (approve), Money used (own table), Funds received, Expenses, Taxes & debts, Import, Fields & categories, Reports, Users |
| Accountant | Dashboard (no funds), Expenses, Material requests (approve), Money requests (record payments), Taxes & debts, Import, Categories, Reports |
| Field manager | Dashboard (no funds), Expenses, Material stock (view), Requests, Reports |
| Store manager | Dashboard (material stock), Materials received, Materials used, Stock count, Requests |
| Receptionist | Dashboard (jobs), Jobs (register jobs, customers, payments, hand over documents), Money requests, Money used |
| Engineer | Dashboard (his or her jobs), Jobs (the jobs given to him or her), Money requests, Money used |

Every user can also ask for materials and for money. Charts are on each person's Dashboard and follow their access.

## Rules

- **Material requests** go to the Accountant and the CEO at the same time. Whoever is available approves first.
  Nobody except the CEO decides their own request. The Store manager records the delivery, which adds to the stock.
- **Money requests** go to the CEO. After approval the CEO or the Accountant records the payment (not for their own
  request), and the amount appears in the person's Money used.
- **Money used**: the CEO records money he spent himself (materials, fuel, food...), marked Business or Personal.
  Personal entries are visible only to the CEO.
- **New materials and categories** are typed straight into the tables (New material, New category columns).
- **Taxes & debts** (CEO and Accountant): tax payments, debts owed and owed to the company, with payments and balances.

## The new design (all roles)

The same design now runs for the CEO, Accountant, Store manager and Field manager. Each role sees only what it is allowed to see.

- **Field drop-down** at the top of every data page (All fields, then each field the person has). A person with one field sees its name.
  The choice is remembered between pages and narrows the whole page.
- **Excel logic everywhere.** Tables use the cost workbook layout: `No | Date | Item | Qty | P/U | Total`, a Grand total line and a
  download bar. Extra columns (field, category, status, person) come after those six. Taxes and debts follow it too. Downloads keep the
  workbook layout (SUMMARY sheet + one sheet per category, real Excel dates), so they can be imported again in **Import**.
- **Categories are the same for everybody** (the workbook categories): expenses, material requests, money requests, money used.
  A new category can be added from any add form (**+ New category…**) or in Fields & categories.
- **Dashboard** (replaces Analytics for everybody) is built like the KDBS Excel dashboard: filters (Category, From month, To month,
  Show Amount / Transactions), KPI cards with a short description, a "What the data says" box, and charts in cards with the values
  written on them. Hover for exact values; click a bar in the category ranking to filter the whole page; every chart has a
  Download data (CSV) button.
  - CEO: everything, including funds received, balance and shortfall.
  - Accountant: spending, approvals to give, payments to make, debts. No funds.
  - Field manager: spending of his fields, his own requests, stock. No funds.
  - Store manager: stock, deliveries to record, received and used.
- **Add rows** forms show Date, Item, Qty, P/U and the Total of each row live, with the Total at the bottom.
- **Material stock**: Date, Category, Item, In stock, Received, Used, Requested, By (plus Movements). The CEO, Accountant and Store
  manager can set the category of a material.
- **Material requests** by role: the CEO approves and adds materials directly in the category sheets (he does not request); the
  Accountant approves and requests; the Store manager records deliveries and requests; the Field manager requests.
- **Money requests** by role: the CEO decides and records payments; the Accountant requests and records payments; the Store manager
  and Field manager request. **Money used**: only the CEO records his own; others see what was paid to them.
- **Reports**: summary first, then a category drop-down that shows that category's sheet. Funds appear for the CEO only.

## Services: jobs for customers (consultancy, house design, building permits)

The **Jobs** page is for the Receptionist, the CEO and the Engineers. Everybody is kept informed by the updates on each job row and by **email** (free). The Accountant
does not use this page. **Money of the jobs is seen by the CEO and the Receptionist only**: an Engineer never receives fees or payments.

**The way of a job**

1. The **Receptionist** registers the job. It goes to the CEO (**With CEO**). The customer gets a thank-you email.
2. The **CEO** gives it to an engineer (**With engineer**). The engineer is told.
3. The **Engineer** does the work. If something is needed from the customer, he or she asks (**Waiting for customer**, the customer gets an email). When finished
   (the finished PDF can be attached, optional) the engineer sends it to the CEO (**In review**).
4. The **CEO** checks it:
   - **Confirm**: the job becomes **Ready**, goes to the Receptionist, and the customer gets the email *your work is ready*.
   - **Revert** with a reason: the job becomes **Reverted** and goes back to the engineer, who corrects it and sends it again. The customer is not told.
5. The **Receptionist** hands over the document when paid in full (the CEO can release with a reason): **Collected**.

**One table does everything** (like a register book): date, family name, first name, service, engineer, a coloured status label, and three buttons on each row:
- the **eye** opens the job to read: what the customer asked for, the customer's details, the updates, and everything that happened;
- the **pencil** opens it to work on it: confirm or revert the work, give it to an engineer, add the customer's information, change the details, attach files, see the money
  (CEO, Receptionist) - an engineer gets the pencil only on jobs he or she has something to do on;
- the **mail** button emails the customer the status of the job in words (CEO and Receptionist).

The old Inbox is part of the table: a job with news for you shows "N new" under its status, the **Show** drop-down has "New updates", and opening the job shows the
news and marks it as read. Filters are drop-downs: Show (Open, Needs me, New updates, With CEO, In review, Reverted, Ready, Waiting, Missing info, All), Service, Engineer, Month.

**Customers need only a name** (family name first, for example *MUKAMWIZA Isabelle*). The email and the phone are optional. What we do not know shows as
"no email" in the list, and an **"Information we do not have"** bar (in the opened job and in New job) lets you add it when the customer comes back. It never blocks anything.

**Emails to the customer:** three go by themselves (registered, we need something from you, your work is ready), and one when the CEO or Receptionist presses the mail button (the status of the job in words). The words are in `inchars/jobs/data.py` (`EMAILS` and `STATUS_LINES`).
Staff get an update on the job's row in the table and an email too (when their account has an email address). What happened to each email is written in the job's **History**
("Email sent", "Email not sent: no address", or "Email failed" with the real reason); if an email to a customer fails, the Receptionist gets a notice to call.
An email problem never stops a job action.

### Email (set up once)

In Streamlit **Settings > Secrets**:

    [smtp]
    host = "smtp.gmail.com"
    port = 465
    user = "youroffice@gmail.com"
    password = "the 16-letter App Password"
    sender = "Inchars PD Group <youroffice@gmail.com>"

For Gmail you must turn on 2-step verification and create an **App Password** (your normal password will not work). Then reboot the app, open **System check**
(CEO) and press **Send a test email**: if it fails, the real reason is shown. The same settings serve the login screen's password reset.

**Rules**
- The customer's papers are handed over only when the job is paid in full. The CEO can release without full payment, but must write the reason.
- Every change is written in the job's History. Statuses: With CEO, With engineer, Waiting for customer, In review, Reverted, Ready, Collected, Cancelled.
- Files are kept in the database, up to 15 MB each. If they become big or many, only the file functions in `inchars/jobs/extras.py` need to change.
- Receptionist and Engineer money requests have two categories: **Office use** and **Personal use**.
- Jobs made before this update are moved automatically (New or Returned -> With CEO; Assigned or In progress -> With engineer).

## If something breaks (it cannot stop everything)

The app is **14 code files in 5 folders**, grouped so that one area failing does not touch the others:

- Every page is loaded only when it is opened. If a file is broken or out of date, **only the pages of that file** show a message; the menu and all the other files keep
  working. A bug inside a page (or inside one tab or part of it) shows a message in place and nothing else stops.
- So you can repair one file online while everybody keeps working in the others. The CEO's **System check** page tries to start every file and lists the ones that fail, with
  the reason (it works even when other files are broken). After an update, open it first. The usual cause is a file that was not uploaded or replaced.
- If the services tables cannot be created, the construction pages still start.

## Where to find what (code map)

```
app.py                         the start file
inchars/                       the base, used by everything (keep these small and stable)
    settings.py                roles, constants, colours (CSS), password helpers
    database.py                connection, tables, saving
    accounts.py                accounts, roles, sending email, who sees which field, the login screen
    data.py                    construction data: expenses and funds, materials and stock, money requests and ledger
    widgets.py                 shared screen parts: KPI cards (one light-blue look), charts, cost sheets, menu, drop-downs, run_section
    router.py                  the page list for each role, the menu, main()
    health.py                  System check (kept on its own on purpose)
inchars/construction/          the construction pages, one file per area
    dashboard.py               Dashboard
    materials.py               Material stock, Materials received / used / Stock count, Material requests
    money.py                   Expenses, Funds received, Money requests, Money used, Taxes & debts
    admin.py                   Users, Fields & categories, Import from Excel, Reports
inchars/jobs/                  the services (jobs) side
    data.py                    no screens: settings and email texts, rules, customers, the steps of a job, updates, emails
    page.py                    the Jobs table (eye, pencil, mail), opening a job, registering a job
    extras.py                  documents, payments, and the dashboard of the services
tests/                         harness.py + stub_streamlit.py (a stand-in for the browser) and run_tests.py
assets/logo.png                the logo
```

| If this is wrong... | Look in |
|---|---|
| Login screen, password reset email | `inchars/accounts.py` |
| Who can see what (`has()`), which fields a person has | `inchars/accounts.py` |
| Menu per role, page list, `main()` | `inchars/router.py` |
| Database connection, tables | `inchars/database.py` |
| Roles, categories, constants, colours, the KPI look | `inchars/settings.py` |
| Charts, KPI cards, Excel-style tables, the drop-downs and menu | `inchars/widgets.py` |
| Dashboard | `inchars/construction/dashboard.py` |
| Material pages | `inchars/construction/materials.py` (data: `inchars/data.py`) |
| Expenses, funds, money requests, money used, taxes and debts | `inchars/construction/money.py` (data: `inchars/data.py`) |
| Users, Fields & categories, Import from Excel, Reports | `inchars/construction/admin.py` |
| The Jobs table, the eye / pencil / mail, registering a job | `inchars/jobs/page.py` |
| Who can do what on a job, the steps, the emails and their words | `inchars/jobs/data.py` |
| Documents, payments, services dashboard | `inchars/jobs/extras.py` |
| Which part of the app is broken | the **System check** page, `inchars/health.py` |

### Checking a change

After editing, run `python tests/run_tests.py`: it opens every page for every role and runs the whole job flow (rules, emails, updates, money hidden from engineers, and that one
broken file does not stop the rest). It uses a temporary database, needs only `pandas`, takes a few seconds and prints OK or FAIL for each check. If a page shows an error online,
the traceback in Streamlit **Manage app** gives the file and line; fix it there, then run the tests before uploading.

## Put it online (free)

Use a NEW Neon project, a NEW GitHub repository and a NEW Streamlit app. Do not reuse the stock system's.

1. **Neon:** create a project (for example `inchars-construction`), press **Connect** and copy the connection string.
2. **GitHub:** create a Public repository and upload every file and folder of this project (replace the whole `inchars` folder when updating)
   (`app.py`, `requirements.txt`, `README.md`, `.gitignore`, the `inchars` folder with all its sub-folders, the `tests` folder,
   the `assets` folder and the `.streamlit` folder). The `inchars` folder is the program: without it the app will not start.
3. **Streamlit Community Cloud:** Create app, "Yup, I have an app", pick the repository, branch `main`, file
   `app.py`. Choose an App URL such as `inchars-construction`. In **Advanced settings > Secrets** paste your
   filled-in `inchars_secrets_to_paste.txt`. Press **Deploy**.
4. **First login:** use the `[first_manager]` user name and password. You are the CEO. Then:
   - **Fields & categories:** create each field, or use **Import from Excel** to load the KDBS cost workbook into a new field.
   - **Users:** add the Accountant, Field managers and Store managers and give each the field(s) they work on.
   - The Store manager adds the materials (name and unit) in **Materials received**, then records deliveries and usage.
5. After the first login, delete the `[first_manager]` section from the secrets.

## Notes

- If red boxes saying "Failed to fetch dynamically imported module" appear, the browser could not download part of the
  page. Refresh with Ctrl+Shift+R. If it continues, use Manage app > Reboot app, or try another browser. It happens
  most after the app wakes from sleep or right after an update.

- Never upload secrets or `construction.db` to GitHub.
- Replace `assets/logo.png` to change the logo.
- Free Streamlit apps sleep after a period without visitors; the first visitor presses "wake up".
