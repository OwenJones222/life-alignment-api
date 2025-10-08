# app.py — FastAPI for Life Alignment API
# - Tolerant /generate endpoint (per-subtheme ranks or legacy).
# - Robust dynamic resolver for the PDF builder in generate_report_json.py.
# - Built-in Gmail SMTP sending (uses EMAIL_USER + EMAIL_APP_PASSWORD env vars).

import os
import smtplib
import ssl
import inspect
from email.message import EmailMessage
from mimetypes import guess_type

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# ----------------------------
# Import your report builder module
# ----------------------------
import generate_report_json as _report_mod  # your existing file


def _resolve_report_builder():
    """
    Resolve a callable from generate_report_json that builds a PDF and returns its path.

    Resolution order:
      1) Env var REPORT_FUNC if it matches a callable in the module.
      2) Preferred names (common guesses).
      3) Any public callable whose name contains 'report'.
      4) Any public callable that appears to take a single payload parameter.
    """
    preferred = (
        "build_pdf_report",
        "generate_pdf_report",
        "create_pdf_from_payload",
        "create_pdf",
        "build_report",
        "generate_report",
    )

    # 0) Dump available public callables for visibility in Render logs
    public_callables = []
    for name in dir(_report_mod):
        if name.startswith("_"):
            continue
        obj = getattr(_report_mod, name)
        if callable(obj):
            public_callables.append(name)
    print(f"[report] Public callables in generate_report_json: {public_callables}")

    # 1) Explicit env override
    env_name = os.getenv("REPORT_FUNC")
    if env_name:
        fn = getattr(_report_mod, env_name, None)
        if callable(fn):
            print(f"[report] Using REPORT_FUNC override: {env_name}()")
            return fn
        else:
            print(f"[report] REPORT_FUNC='{env_name}' not found/callable.")

    # 2) Preferred names
    for name in preferred:
        fn = getattr(_report_mod, name, None)
        if callable(fn):
            print(f"[report] Using builder function: {name}()")
            return fn

    # 3) Any callable containing 'report'
    for name in public_callables:
        if "report" in name.lower():
            fn = getattr(_report_mod, name, None)
            if callable(fn):
                print(f"[report] Using heuristic (contains 'report'): {name}()")
                return fn

    # 4) Any callable with a 1-parameter signature (payload-like)
    for name in public_callables:
        fn = getattr(_report_mod, name, None)
        if not callable(fn):
            continue
        try:
            sig = inspect.signature(fn)
            # Accept 1 positional OR (**kwargs) style
            params = [p for p in sig.parameters.values()
                      if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_KEYWORD)]
            if len(params) >= 1:
                print(f"[report] Using generic 1-param callable: {name}()")
                return fn
        except Exception:
            continue

    raise ImportError(
        "No suitable report builder found in generate_report_json.py. "
        "Set env REPORT_FUNC to the exact function name, or expose one callable "
        "that accepts the normalized payload and returns a PDF file path. "
        f"Available public callables: {public_callables}"
    )


BUILD_REPORT = _resolve_report_builder()

# ----------------------------
# FastAPI + CORS
# ----------------------------
ALLOWED_ORIGINS = [
    "https://queensparkfitness.com",
    "https://www.queensparkfitness.com",
]
_env_origins = os.getenv("ALLOWED_ORIGINS")
if _env_origins:
    ALLOWED_ORIGINS = [o.strip() for o in _env_origins.split(",") if o.strip()]

app = FastAPI(title="Life Alignment API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["POST", "OPTIONS", "GET"],
    allow_headers=["*"],
)

# ----------------------------
# Email sender (Gmail SMTP)
# ----------------------------
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))  # STARTTLS
EMAIL_USER = os.getenv("EMAIL_USER", "")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "")


def send_email_with_attachment(to: str, subject: str, body: str, attachment_path: str):
    if not EMAIL_USER or not EMAIL_APP_PASSWORD:
        raise RuntimeError("EMAIL_USER or EMAIL_APP_PASSWORD env vars are not set.")

    msg = EmailMessage()
    msg["From"] = EMAIL_USER
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    if attachment_path and os.path.isfile(attachment_path):
        mime_type, _ = guess_type(attachment_path)
        maintype, subtype = (mime_type or "application/pdf").split("/", 1)
        with open(attachment_path, "rb") as f:
            msg.add_attachment(
                f.read(),
                maintype=maintype,
                subtype=subtype,
                filename=os.path.basename(attachment_path),
            )
    else:
        print(f"[email] Warning: attachment not found: {attachment_path}")

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(EMAIL_USER, EMAIL_APP_PASSWORD)
        server.send_message(msg)
        print(f"[email] Sent to {to} with subject: {subject}")

# ----------------------------
# Helpers
# ----------------------------
def _normalize_ranks(payload: dict) -> dict:
    """
    Expect per-subtheme ranks: { pillarKey: [r1,r2,r3,r4] }.
    Accept either:
      - payload["importance_subthemes"] (preferred)
      - payload["importance"] (current FE)
    Otherwise, fabricate neutral ranks [1,2,3,4] for each pillar.
    """
    explicit = payload.get("importance_subthemes")
    if isinstance(explicit, dict):
        return explicit

    newshape = payload.get("importance")
    if isinstance(newshape, dict):
        return newshape

    return {k: [1, 2, 3, 4] for k in ("health", "wealth", "self", "social")}

# ----------------------------
# Routes
# ----------------------------
@app.get("/")
async def root():
    return {
        "ok": True,
        "service": "Life Alignment API",
        "docs": "/docs",
        "generate": "/generate (POST)",
    }


@app.post("/generate")
async def generate(request: Request):
    # 1) Parse JSON
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Bad JSON")

    # 2) Required email
    email = (data.get("email") or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="Missing email")

    # 3) Optional fields
    answers = data.get("answers") or {}
    wildcards = data.get("wildcards") or {}
    meta = data.get("meta") or {}

    # 4) Normalize ranks for the report generator
    importance_subthemes = _normalize_ranks(data)

    normalized = {
        "email": email,
        "answers": answers,                           # { pillarKey: [{qIndex,text,value}, ...] }
        "wildcards": wildcards,                       # { id: "text", ... }
        "importance_subthemes": importance_subthemes, # { pillarKey: [r1,r2,r3,r4] }
        "meta": meta,
    }

    # 5) Build the PDF
    try:
        pdf_path = BUILD_REPORT(normalized)  # must return a file path
        if not pdf_path or not isinstance(pdf_path, str):
            raise RuntimeError("Report builder did not return a file path.")
    except Exception as e:
        print("ERROR while building report:", repr(e))
        raise HTTPException(status_code=500, detail="Report generation failed")

    # 6) Email the PDF (non-fatal on failure)
    try:
        send_email_with_attachment(
            to=email,
            subject="Your Life Alignment Diagnostic Report",
            body=(
                "Hi there,\n\n"
                "Thanks for completing the Life Alignment Diagnostic.\n"
                "Your personalised PDF report is attached.\n\n"
                "Warm regards,\nOwen\n"
            ),
            attachment_path=pdf_path,
        )
    except Exception as e:
        print("ERROR while emailing:", repr(e))

    return {"ok": True}
