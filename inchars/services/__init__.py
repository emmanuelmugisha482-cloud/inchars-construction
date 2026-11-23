"""Services: jobs for customers (consultancy, house design, building permits) and the notifications around them.

One file per thing (its data and its screen together):
  config.py     settings and email texts         core.py     rules, customers, list of jobs, history (no screens)
  workflow.py   the steps of a job (no screens) notify.py   Inbox and emails
  page_jobs.py  the Jobs page and its list      new_job.py  registering a job
  panel.py      an opened job (Work, Details, History)   money.py  payments   files.py  documents
  dashboard.py  dashboards of the services      ui.py       small screen helpers

The page and each of its parts are loaded one by one (ui.run_section): if one file is broken only its part shows a message.
The rest of the app only meets this folder in three places: the menu (router.py), the Services tab of the dashboard, and the unread number on the menu."""
