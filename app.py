from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Optional
from datetime import datetime
import os, uuid, logging, smtplib

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

from generate_report_json import build_pdf_from_payload

# --------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("life-alignment-api")

# --------------------------------------------------------------------
# FastAPI app + CORS
# --------------------------------------------------------------------
app = FastAPI()

# TEMP for testing; later lock down to your domains:
#   ["https://queensparkfitness.com", "https://www.queensparkfitness.com"]
ALLOWED_ORIGINS = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------------------------
# Models
# --------------------------------------------------------------------
class Submission(BaseModel):
    email: str
    submittedAt: str
    answers: Dict[str, list]            # pillar -> list[{qIndex, text, value}]
    wildcards: Dict[str, str]
    meta: Dict
    importance: Optional[Dict[str, int]] = None  # pillar -> 1..4 (optional)

# --------------------------------------------------------------------
# Email helper
# --------------------------------------------------------------------
def send_email_with_attachment(to_email: str, subject: str, body_text: str, attachment_path: str) -> None:
    """
    Sends an email with a PDF attachment using SMTP.
    Expects environment variables:
      - SMTP_USER (sender address, e.g. your Gmail)
      - SMTP_PASS (Gmail App Password)
      - SMTP_SERVER (optional, default smtp.gmail.com)
      - SMTP_PORT   (optional, default 587)
      - SMTP_CC     (optional, comma-separated list)
    """
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))

    if not smtp_user or not smtp_pass:
        raise RuntimeError("SMTP_USER/SMTP_PASS not configured in environment.")

    msg = MIMEMultipart()
    msg["From"] = smtp_user
    msg["To"] = to_email
    msg["Subject"] = subject

    # Optional CC
    cc_raw = os.getenv("SMTP_CC", "").strip()
    cc_list = [x.strip() for x in cc_raw.split(",") if x.strip()]
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)

    # Body
    msg.attach(MIMEText(body_text, "plain"))

    # Attachment
    if attachment_path and os.path.exists(attachment_path):
        with open(attachment_path, "rb") as f:
            part = MIMEApplication(f.read(), _subtype="pdf")
            part.add_header("Content-Disposition", "attachment", filename=os.path.basename(attachment_path))
            msg.attach(part)
    else:
        log.warning(f"Attachment not found at {attachment_path}; sending without attachment.")

    recipients = [to_email] + cc_list
    log.info(f"SMTP: connecting to {smtp_server}:{smtp_port} as {smtp_user}; to={recipients}")

    with smtplib.SMTP(smtp_server, smtp_port, timeout=20) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, recipients, msg.as_string())

    log.info(f"SMTP: message dispatched to {recipients}")

# --------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------
@app.get("/healthz")
def healthz():
    return {"ok": True, "ts": datetime.utcnow().isoformat()}

@app.post("/generate")
def generate(sub: Submission):
    try:
        report_name = f"Life_Alignment_Report_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.pdf"
        out_path = os.path.join("/tmp", report_name)

        # Build the PDF from the payload (no Excel needed)
        build_pdf_from_payload(sub.dict(), out_path)
        log.info(f"Report built at {out_path} for {sub.email}")

        # --- Covering letter (draft; tweak later) ---
        subject = "Your Life Alignment Diagnostic Report"
        body = (
            "Hi there,\n\n"
            "Thank you for completing the Life Alignment Diagnostic.\n"
            "Attached is your personalised PDF report.\n\n"
            "What to do next:\n"
            "1) Skim the spiderweb chart to see your overall pattern\n"
            "2) Look at each pillar’s bar chart – the ‘Priority Gap’ bars show where focus will pay off fastest\n"
            "3) Start with the largest gap – one meaningful action this week is enough to build momentum\n\n"
            "I’ll follow up with guidance on how to interpret the results and options for next steps.\n\n"
            "Warm regards,\n"
            "Owen Jones\n"
            "—\n"
            "Automated email from your Life Alignment system."
        )

        # Send the email (logs will show success/failure)
        log.info(f"Email: preparing to send report to {sub.email}")
        send_email_with_attachment(sub.email, subject, body, out_path)
        log.info(f"Email: sent report to {sub.email} OK")

        return {"ok": True, "file": report_name, "emailed": True}
    except Exception as e:
        log.exception("Error in /generate")
        raise HTTPException(status_code=500, detail=str(e))

