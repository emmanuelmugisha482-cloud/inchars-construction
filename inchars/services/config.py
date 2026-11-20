"""Settings of the services side: the services offered, the statuses, the choices that can be tapped."""

SERVICES = ["Consultancy", "House design", "Building permit", "Other"]
PERMIT = "Building permit"
JOB_STATUSES = ["New", "Assigned", "In progress", "Waiting for customer", "Ready", "Collected", "Cancelled"]
OPEN_STATUSES = ["New", "Assigned", "In progress", "Waiting for customer", "Ready"]
PERMIT_STAGES = ["Documents collected", "Submitted to government", "Under review", "Corrections requested", "Approved"]
PAY_METHODS = ["Cash", "Mobile money", "Bank transfer", "Other"]
MAX_FILE_MB = 15  # per file; files are kept in the database (see files.py to move them to an online file store)

QUICK_DAYS = [("3 days", 3), ("1 week", 7), ("2 weeks", 14), ("1 month", 30)]  # promised date: tap one
NEED_REASONS = ["Copy of ID card", "Land title / UPI", "Site plan or map", "Payment", "Other documents"]  # what the customer must bring: tap

# ready-made messages: tap one, change it if needed, send. {job_no} is replaced by the job number.
MESSAGE_TEMPLATES = {
    "Documents needed": "Please bring the documents we asked for so that we can continue with your job {job_no}.",
    "Document ready": "Your document for job {job_no} is ready. Please come to our office with your job number.",
    "Payment reminder": "A reminder: there is a balance to pay on your job {job_no}. Please pass by our office.",
    "Please call us": "Please call or visit our office about your job {job_no}. Thank you.",
    "Write my own": "",
}
