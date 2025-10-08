# app.py — Life Alignment API (full replacement)
# - CORS for your WP site
# - Accepts both old (pillar-level) and new (subtheme-level) payloads
# - Dynamically resolves report builder from generate_report_json.py
# - Calls builder robustly (1-arg or 2-arg styles)
# - Emails the PDF via Gmail SMTP using env SMTP_USER / SMTP_PASS

import os
import json
import ssl
import smtplib
import importlib
import inspect
import tempfile
from email.message import EmailMessage
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

# ----------------------------
# Config from environment
# ----------------------------
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASS = os.getenv("SMTP_PASS", "").strip()
REPORT_FUNC = os.getenv("REPORT_FUNC", "").strip()

ALLOWED_ORIGINS = [
    "https://queensparkfitness.com",
    "https://www.queensparkfitness.com",
]

# ----------------------------
# FastAPI app + CORS
# ----------------------------
app = FastAPI(title="Life Alignment API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS or ["*"],  # fall back if needed during testing
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------
# Email helper (Gmail SMTP)
# ----------------------------
def send_email_with_attachment(
    to_email: str,
    subject: str,
    body_text: str,
    attachment_path: str,
    from_email: Optional[str] = None,
) -> None:
    """
    Sends an email with a single PDF attachment using Gmail SMTP.
    Requires SMTP_USER/SMTP_PASS env vars (App Password for Gmail).
    """
    from_email = from_email or SMTP_USER
    if not (SMTP_USER and SMTP_PASS):
        raise RuntimeError("SMTP_USER/SMTP_PASS are not configured.")

    msg = EmailMessage()
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body_text)

    # Attach PDF
    with open(attachment_path, "rb") as f:
        data = f.read()
    filename = os.path.basename(attachment_path) or "Life_Alignment_Report.pdf"
    msg.add_attachment(
        data,
        maintype="application",
        subtype="pdf",
        filename=filename,
    )

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)


# ----------------------------
# Resolve the report builder
# ----------------------------
def _import_report_module():
    """
    Import your local report builder module.
    """
    return importlib.import_module("generate_report_json")


def _resolve_report_builder():
    """
    Pick the report builder function from generate_report_json.py.

    Priority:
    1) REPORT_FUNC env (exact name)
    2) First match in a list of known names
    """
    module = _import_report_module()

    # If env var is set, try that first
    if REPORT_FUNC:
        fn = getattr(module, REPORT_FUNC, None)
        if callable(fn):
            app.logger if hasattr(app, "logger") else None
            print(f"[report] Using REPORT_FUNC override: {REPORT_FUNC}()")
            return fn
        else:
            print(f"[report] REPORT_FUNC='{REPORT_FUNC}' not found/callable.")

    # Otherwise try known candidates
    candidates = [
        "build_pdf_report",
        "build_pdf_from_payload",     # <- your current one
        "generate_pdf_report",
        "create_pdf_from_payload",
        "create_pdf",
        "build_report",
        "generate_report",
    ]

    for name in candidates:
        fn = getattr(module, name, None)
        if callable(fn):
            print(f"[report] Using discovered builder: {name}()")
            return fn

    raise ImportError(
        "No suitable report builder found in generate_report_json.py. "
        f"Expected one of: {', '.join(candidates)}"
    )


BUILD_REPORT = _resolve_report_builder()

# ----------------------------
# Robust builder invocation
# ----------------------------
def _call_report_builder(builder, payload: Dict[str, Any]) -> str:
    """
    Call the report builder regardless of signature:
    - 2-arg: builder(payload, out_pdf) -> writes to out_pdf, returns None
    - 1-arg: builder(payload) -> returns path to the PDF
    Also supports a fallback if the builder writes to a known default path.
    Returns the absolute path to the PDF file.
    """
    sig = inspect.signature(builder)
    params = [
        p for p in sig.parameters.values()
        if p.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]

    # 2-arg style -> provide a temp filename
    if len(params) >= 2:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            out_pdf = tmp.name
        builder(payload, out_pdf)
        if os.path.exists(out_pdf):
            return out_pdf
        raise RuntimeError("Report builder (2-arg) did not write the expected PDF.")

    # 1-arg style -> expect a returned path
    result = builder(payload)
    if isinstance(result, str) and os.path.exists(result):
        return result

    # Fallback: some builders always use a default path
    default_path = "/tmp/Life_Alignment_Report.pdf"
    if os.path.exists(default_path):
        return default_path

    raise RuntimeError("Report builder did not produce a PDF path.")


# ----------------------------
# Payload normaliser (tolerant)
# ----------------------------
def _normalise_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Accepts older pillar-level schema and newer subtheme-level schema.
    Just passes through what exists; the report builder already expects
    the same keys you used before. If you need deeper transforms, add here.
    """
    # Example light-touch: ensure email is present and sane
    email = (data.get("email") or "").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=422, detail="Valid email is required.")

    # Nothing else enforced strictly; your report code uses its own parsing.
    return data


# ----------------------------
# Routes
# ----------------------------
@app.get("/")
def root():
    return {"ok": True, "service": "life-alignment-api"}

@app.post("/generate")
async def generate_report(request: Request):
    """
    Accepts JSON payload posted by your Elementor form, builds the PDF report,
    and emails it as an attachment to the user.
    """
    try:
        payload = await request.json()
            import json, sys
    print("\n==== PAYLOAD DEBUG ====\n", json.dumps(payload, indent=2), file=sys.stdout)

    except Exception:
        # Occasionally WP/Elementor can send urlencoded; try to decode gracefully
        body = await request.body()
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid request body.")

    try:
        data = _normalise_payload(payload)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid payload: {e}")

    user_email = (data.get("email") or "").strip()
    if not user_email:
        raise HTTPException(status_code=422, detail="Email address is required.")

    # Build PDF via whichever style the report builder uses
    try:
        pdf_path = _call_report_builder(BUILD_REPORT, data)
    except Exception as e:
        print(f"ERROR while building report: {e}")
        raise HTTPException(status_code=500, detail=f"Report build failed: {e}")

    # Email it
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

    try:
        send_email_with_attachment(
            to_email=user_email,
            subject=subject,
            body_text=body,
            attachment_path=pdf_path,
            from_email=SMTP_USER or "no-reply@life-alignment",
        )
    except Exception as e:
        print(f"ERROR while emailing: {e}")
        raise HTTPException(status_code=500, detail=f"Email send failed: {e}")

    # Cleanup temp file if we created one under /tmp
    try:
        if pdf_path.startswith("/tmp/") and os.path.exists(pdf_path):
            os.remove(pdf_path)
    except Exception:
        pass

    return {"ok": True, "message": "Report generated and emailed."}
