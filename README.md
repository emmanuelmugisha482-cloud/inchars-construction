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

The **Jobs** page is for the Receptionist, Engineer, CEO and Accountant. Its purpose is to keep everybody informed, easily:
the customer, the Receptionist, the CEO and the Engineer.

**The page has four tabs**
- **Jobs**: one search bar (job number, customer, phone, email, title, engineer), tap buttons to filter (Open, Needs me, Ready, Waiting,
  New, No email, All), one table. **Tap a row** to open the job (typing a full job number opens it at once).
- **New job**: tap the service, type the customer (email is required so the customer gets updates, or tick "No email"), the title and
  the fee, tap the promised date (3 days, 1 week...). Documents are optional.
- **Inbox**: the notifications for the signed-in person. Tap one to open its job.
- **Payments** (CEO, Accountant, Receptionist): money received and balances still to pay.

**An opened job has five parts, one tap each:** Update (the next step as big buttons: give to an engineer, start work, need something
from the customer, mark ready, handed to the customer), **Message** (tap who: the customer, CEO, Receptionist, Engineer; tap a
ready-made message or write your own), Documents (optional), Money (tap Full balance / Half / Other), History.

**Who is told, and how**
| When | Office workers (inbox + email) | Customer (email) |
|---|---|---|
| Job registered | CEO | thank-you with the job number |
| Given to an engineer | Engineer | "given to our engineer" |
| Work started | | work has started |
| Waiting for the customer | Receptionist, CEO | what is needed |
| Permit stage changes | CEO, Receptionist (approved / corrections) | the stage and the government reference |
| Ready | CEO, Receptionist | document ready, balance to pay |
| Payment recorded | | receipt |
| Collected | CEO | thank you |
| A message written by a person | the people chosen | if chosen |

A customer without an email address is flagged in red in the list and in the job, where the Receptionist can add the address.

**Rules**
- **No document is required.** The engineer can mark a job ready without uploading anything. The finished PDF can be attached
  (optional); when there is one, the Receptionist can open it only when the job is paid in full.
- The customer's papers are handed over (job marked Collected) only when the job is paid in full. The CEO can release without full
  payment, but must write the reason.
- An Engineer sees only his or her own jobs and no money. The Accountant sees jobs and payments but not the documents.
- Every change is written in the job's History. Statuses: New, Assigned, In progress, Waiting for customer, Ready, Collected, Cancelled.
- Files are kept in the database, up to 15 MB each. If they become big or many, only `inchars/services/files.py` needs to change.
- Emails use the same `[smtp]` settings as the password reset. In Streamlit **Settings > Secrets** keep:

      app_url = "https://your-app.streamlit.app"

      [smtp]
      host = "smtp.gmail.com"
      port = 465
      user = "your.address@gmail.com"
      password = "the app password"
      sender = "Inchars PD Group <your.address@gmail.com>"

  Emails are sent in the background. If the mail server fails, the job action still works and the notice stays in the Inbox.
- Receptionist and Engineer money requests have two categories: **Office use** and **Personal use**.

## If something breaks (it cannot stop everything)

- Each page is loaded only when it is opened. If one page is broken or out of date, **only that page** shows a message; the menu and all
  the other pages keep working. Inside the Jobs page, each part (Update, Message, Documents, Money) also runs on its own.
- The CEO has a **System check** page: it tries to start every file of the app and lists any that fail, with the reason.
  After an update, open it first. The usual cause is a file that was not uploaded or not replaced: upload the whole `inchars` folder again
  and reboot the app.
- If the services tables cannot be created, the construction pages still start.

## Where to find what (code map)

`app.py` is only the start file. All the code is in the `inchars/` folder, split by job, so a problem can be found quickly.

| If this is wrong... | Look in |
|---|---|
| Login screen, password reset email | `inchars/login.py`, `inchars/accounts.py` |
| Who can see what (roles, `has()`), which fields a person has | `inchars/accounts.py`, `inchars/data/scope.py` |
| Menu per role, page list (pages load one by one), `main()` | `inchars/router.py` |
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
| The Jobs page (tabs, search, list, inbox) | `inchars/services/page_jobs.py` |
| One opened job (header and the five parts) | `inchars/services/job_panel.py`, `panel_update.py`, `panel_message.py`, `panel_files.py`, `panel_money.py` |
| Registering a job | `inchars/services/new_job.py` |
| Who is notified and by email (staff and customer), messages | `inchars/services/notices.py` |
| Who can do what on a job | `inchars/services/rules.py` |
| Jobs, customers, payments, history (data) | `inchars/services/jobs.py`, `customers.py`, `payments.py`, `history.py` |
| Where uploaded files are kept | `inchars/services/files.py` |
| Services dashboards (Receptionist, Engineer, Services tab) | `inchars/services/dashboard.py` |
| Services settings (services, statuses, quick choices, message templates) | `inchars/services/config.py` |
| Which part of the app is broken | the **System check** page, `inchars/pages/system_check.py` |
| Import from Excel | `inchars/pages/import_costs.py` |
| Fields & categories, Users | `inchars/pages/setup.py`, `inchars/pages/users.py` |

Rule of thumb: a page file draws the page, a file in `data/` reads and writes the database, a file in `components/` is a piece used
by several pages. Files only import from the layers below them, so changing a page cannot break another page.

### Checking a change

After editing, run `python tests/smoke_test.py` (every page for every role) and `python tests/jobs_workflow_test.py` (the whole job
flow, its rules, notifications and emails, and that one broken part does not stop the rest). They run on a temporary database and print OK or FAIL for each check, in a few seconds,
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
