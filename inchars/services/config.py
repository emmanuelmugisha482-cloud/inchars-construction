"""Settings of the services side: the services offered, the statuses, the choices that can be tapped, and the email texts.
Change a word here and it changes everywhere. Nothing else in this file is code."""

SERVICES = ["Consultancy", "House design", "Building permit", "Other"]
PERMIT = "Building permit"
# The way of a job: registered -> With CEO -> (CEO gives it to an engineer) With engineer -> (engineer finishes) In review ->
# the CEO confirms -> Ready (with the Receptionist, the customer is emailed) -> Collected.   Or the CEO reverts it -> Reverted (back to the engineer).
JOB_STATUSES = ["With CEO", "With engineer", "Waiting for customer", "In review", "Reverted", "Ready", "Collected", "Cancelled"]
OPEN_STATUSES = ["With CEO", "With engineer", "Waiting for customer", "In review", "Reverted", "Ready"]
PERMIT_STAGES = ["Documents collected", "Submitted to government", "Under review", "Corrections requested", "Approved"]
PAY_METHODS = ["Cash", "Mobile money", "Bank transfer", "Other"]
MAX_FILE_MB = 15  # per file; files are kept in the database (see files.py to move them to an online file store)

QUICK_DAYS = [("3 days", 3), ("1 week", 7), ("2 weeks", 14), ("1 month", 30)]  # promised date: tap one
NEED_REASONS = ["Copy of ID card", "Land title / UPI", "Site plan or map", "Payment", "Other documents"]  # what the customer must bring: tap
REVERT_REASONS = ["Needs correction", "Information is missing", "Not clear enough", "Wrong measurements", "Other"]  # the CEO reverts the work: tap

# What we may not know about a customer. Nothing here is required: the screens only remind, they never block.
# (column in the customers table, how it is written to the staff). The email comes first: it is how we keep the customer informed.
MISSING_FIELDS = [("email", "email address"), ("phone", "phone number")]

# The emails the customer receives, written like a person would write them. {name} is the customer's first name.
# Each is (subject, text). Change a word here and the emails change.
EMAILS = {
    "registered": ("We have received your request ({job_no})",
                   "Hello {name},\n\nThank you for working with us. We have registered your request ({job_no}) and our team is on it.\n\n"
                   "We will keep you informed by email as it moves forward, and we will write to you whenever we need something else from you.\n\n"
                   "Warm regards,\nInchars PD Group"),
    "need": ("We need something from you ({job_no})",
             "Hello {name},\n\nTo continue with your request ({job_no}), we need the following from you:\n\n{what}\n\n"
             "Please bring it to our office, or reply to this email.\n\nThank you,\nInchars PD Group"),
    "ready": ("Your work is ready ({job_no})",
              "Hello {name},\n\nGood news: your work ({job_no}) is ready. Please come to our office and tell us your job number.{balance}\n\n"
              "Thank you for working with us.\n\nWarm regards,\nInchars PD Group"),
}
