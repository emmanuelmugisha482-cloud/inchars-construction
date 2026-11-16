"""Settings and constants: file paths, the four roles, default categories, column lists."""

import os
import re


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the project folder (where app.py is)


DB = os.path.join(BASE_DIR, "construction.db")  # used only when no DATABASE_URL is set (local testing)


BG_IMAGE = os.path.join(BASE_DIR, "assets", "background.jpg")  # optional photo for the login page


LOGO = os.path.join(BASE_DIR, "assets", "logo.png")


CEO, ACCOUNTANT, STORE, FIELD = "CEO", "Accountant", "Store manager", "Field manager"


ROLES = [CEO, ACCOUNTANT, STORE, FIELD]


ALL = tuple(ROLES)


SEE_ALL = (CEO,)  # only the CEO sees every field; everybody else works on the fields given to them


FUNDS_VISIBLE = (CEO,)  # funds received from clients are shown to these roles only


DEFAULT_CATEGORIES = ["Wages & Salaries", "Cements & Steel", "Plumbing, Petrol & Electricity", "Painting & Transport",
                      "Stones, Sand & Bricks", "Parafo (Ceiling) & Water", "Welding & Ironmongery",
                      "Iron Sheets & Metals", "Doors", "Carpentry & Joinery"]


SHEET_TO_CATEGORY = {  # sheet names used in the KDBS cost workbook -> category names
    "wagessalaries": "Wages & Salaries", "cementssteelsbar": "Cements & Steel",
    "plumbingpetrolelectricity": "Plumbing, Petrol & Electricity", "paintingstransport": "Painting & Transport",
    "stonessandconcasebricks": "Stones, Sand & Bricks", "ceilingfixandwaterbill": "Parafo (Ceiling) & Water",
    "weldingironmongary": "Welding & Ironmongery", "ironsheetsothermetals": "Iron Sheets & Metals",
    "doors": "Doors", "carpentryandjoinery": "Carpentry & Joinery"}


ALIASES = {"item": ("nameofitems", "nameofitem", "namesoftransaction", "nameoftransaction", "item", "description"),
           "qty": ("units", "qty", "quantity"), "price": ("priceunitfrw", "punitfrw", "punit", "priceunit", "unitprice", "pu"),
           "total": ("total",), "date": ("date",)}


MONEY_CATEGORIES = ["Fuel", "Food", "Transport", "Materials", "Tools & equipment", "Wages", "Airtime & communication",
                    "Accommodation", "Tax", "Debt payment", "Other"]


TAX_TYPES = ["VAT", "PAYE", "Corporate income tax", "Pension / RSSB", "Withholding tax", "Local taxes and fees", "Other"]


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


EXP_COLS = ["id", "site_id", "Date", "Field", "Category", "Item", "Qty", "Unit price", "Total", "Entered by"]


FUND_COLS = ["id", "site_id", "Date", "Field", "Amount", "Source", "Note", "Entered by"]


MOVE_COLS = ["id", "site_id", "material_id", "Date", "Field", "Material", "Unit", "Type", "Qty", "Detail", "Entered by"]


# Everybody can ask. The Accountant or the CEO approves (whoever acts first), nobody approves their own request
# (the CEO may), and the Store manager records the delivery, which adds the quantity to the stock.
REQ_COLS = ["id", "batch", "site_id", "material_id", "Date", "Field", "Material", "Unit", "Requested", "Approved",
            "Delivered", "Status", "Needed by", "Requested by", "Decided by", "Note", "Reason"]
