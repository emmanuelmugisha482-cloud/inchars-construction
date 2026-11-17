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

A separate part of the app, for the Receptionist, Engineer, CEO and Accountant. It is the **Jobs** page.

**The flow**
1. The **Receptionist** registers the job (customer, service, title, fee, promised date, deposit, the customer's documents). The app gives
   a job number such as `INC-2026-0001`; the customer keeps it to come back. The **CEO is notified**.
2. The **CEO** gives the job to an engineer and sets the promised date. The **Engineer is notified**.
3. The **Engineer** starts the work, can ask the customer for something (the Receptionist is notified), sets the permit stage for
   building permits (documents collected, submitted to government with the reference number, under review, corrections requested,
   approved), uploads the finished **PDF** and marks the job **Ready**. The **CEO and the Receptionist are notified**.
4. When the customer returns, the **Receptionist** finds the job by number, name or phone. The finished document can be opened (and
   printed from the PDF viewer, Ctrl+P) **only when the job is paid in full**. The Receptionist records any payment, hands over the
   document and marks the job **Collected**. The CEO can release a document without full payment, but must write the reason.

**Rules**
- Deposits and part payments are allowed. A payment cannot be more than the balance. Only the CEO can delete a payment.
- An Engineer sees only his or her own jobs and no money. The Accountant sees jobs and payments but not the documents.
- Every change is written in the job's **History** (who, when, what). Notifications appear on the Jobs page, with a number on the menu,
  and are **also sent by email** to every person who has an email address (never to the person who did the action).
  Emails use the same email settings as the password reset. In Streamlit **Settings > Secrets** keep this block (the app address is
  optional; it is added to the email):

      app_url = "https://your-app.streamlit.app"

      [smtp]
      host = "smtp.gmail.com"
      port = 465
      user = "your.address@gmail.com"
      password = "the app password"
      sender = "Inchars PD Group <your.address@gmail.com>"

  Emails are sent in the background. If the mail server fails, the job action still works and the notice stays on the Jobs page.
  Each person adds his or her email address at the first login (or the CEO in Users).
- Statuses: New, Assigned, In progress, Waiting for customer, Ready, Collected, Cancelled. Only the CEO can reopen a closed job.
- Files are kept in the database, up to 15 MB each (PDF, JPG, PNG for the customer's documents; PDF only for the finished document).
  If the files become big or many, only `inchars/storage.py` needs to change to use an online file store.
- The CEO and Accountant dashboards have a **Services** tab; the Receptionist and Engineer dashboards show their own jobs.

## Where to find what (code map)

`app.py` is only the start file. All the code is in the `inchars/` folder, split by job, so a problem can be found quickly.

| If this is wrong... | Look in |
|---|---|
| Login screen, password reset email | `inchars/login.py`, `inchars/accounts.py` |
| Who can see what (roles, `has()`), which fields a person has | `inchars/accounts.py`, `inchars/data/scope.py` |
| Menu per role, page list, `main()` | `inchars/router.py` |
| Database connection, tables, saving | `inchars/db.py` |
| Roles, default categories, constants | `inchars/config.py` |
| Colours and look (CSS) | `inchars/styles.py` |
| Dashboard (cards, charts, "What the data says") | `inchars/pages/dashboard.py` |
| A chart looks wrong | `inchars/components/charts.py` |
| KPI cards | `inchars/components/kpi.py` |
| Excel-style tables, the add-rows form, Excel downloads | `inchars/components/sheets.py` |
| Field drop-down, side menu | `inchars/components/layout.py` |
| Expenses / Funds received | `inchars/pages/expenses.py`, `inchars/pages/funds.py` (data: `inchars/data/expenses.py`) |
| Material stock, requests, deliveries | `inchars/pages/stock.py`, `inchars/pages/store.py`, `inchars/pages/material_requests.py` (data: `inchars/data/materials.py`) |
| Money requests, money used, taxes and debts | `inchars/pages/money_requests.py`, `money_used.py`, `taxes_debts.py` (data: `inchars/data/money.py`) |
| Reports | `inchars/pages/reports.py` |
| Jobs for customers: the page, one job, customers, payments | `inchars/pages/jobs.py`, `job_detail.py`, `customers.py`, `job_payments.py` |
| Jobs dashboards (Receptionist, Engineer, Services tab) | `inchars/pages/jobs_dashboard.py` |
| Jobs rules (who can do what), notifications, payments | `inchars/data/jobs.py` |
| Where uploaded files are kept | `inchars/storage.py` |
| Import from Excel | `inchars/pages/import_costs.py` |
| Fields & categories, Users | `inchars/pages/setup.py`, `inchars/pages/users.py` |

Rule of thumb: a page file draws the page, a file in `data/` reads and writes the database, a file in `components/` is a piece used
by several pages. Files only import from the layers below them, so changing a page cannot break another page.

### Checking a change

After editing, run `python tests/smoke_test.py` (every page for every role) and `python tests/jobs_workflow_test.py` (the whole job
flow with its rules and notifications). They run on a temporary database and print OK or FAIL for each check, in a few seconds,
without a browser. They need only `pandas`. If a page shows an error online, the traceback in Streamlit
**Manage app** gives the file and line; fix it there, then run the smoke test before uploading.

## Put it online (free)

Use a NEW Neon project, a NEW GitHub repository and a NEW Streamlit app. Do not reuse the stock system's.

1. **Neon:** create a project (for example `inchars-construction`), press **Connect** and copy the connection string.
2. **GitHub:** create a Public repository and upload every file and folder of this project
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
