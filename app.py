# app.py — FastAPI for Life Alignment API
# - Tolerant /generate endpoint (new per-subtheme ranks or legacy).
# - Dynamically resolves your PDF builder function from generate_report_json.py.

import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# ----------------------------
# Import your report builder module and email helper
# ----------------------------
import generate_report_json as _report_mod  # your existing file
from mailer import send_email_with_attachment  # your existing helper

def _resolve_report_builder():
    """
    Find a callable in generate_report_json that builds a PDF and returns the path.
    Tries multiple common names so we don't break if the function name differs.
    """
    candidates = (
        "build_pdf_report",
        "generate_pdf_report",
        "create_pdf_from_payload",
        "create_pdf",
        "build_report",
        "generate_report",
    )
    for name in candidates:
        fn = getattr(_report_mod, name, None)
        if callable(fn):
            print(f"[report] Using builder function: {name}()")
            return fn
    raise ImportError(
        "No suitable report builder found in generate_report_json.py. "
        "Expected one of: " + ", ".join(candidates)
    )

BUILD_REPORT = _resolve_report_builder()

# ----------------------------
# FastAPI + CORS
# ----------------------------
ALLOWED_ORIGINS = [
    "https://queensparkfitness.com",
    "https://www.queensparkfitness.com",
]
# Optionally allow a comma-separated env var to override (e.g., in Render)
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
# Helpers
# ----------------------------
def _normalize_ranks(payload: dict) -> dict:
    """
    We expect per-subtheme ranks: { pillarKey: [r1,r2,r3,r4] }.
    Accepts either of:
      - payload["importance_subthemes"] (preferred name)
      - payload["importance"]          (what the current FE sends)
    Otherwise, fabricate neutral ranks [1,2,3,4] for each pillar.
    """
    explicit = payload.get("importance_subthemes")
    if isinstance(explicit, dict):
        return explicit

    newshape = payload.get("importance")
    if isinstance(newshape, dict):
        return newshape

    # Fallback: neutral ranks (keeps report logic running)
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

    # 3) Pull optional fields
    answers   = data.get("answers")   or {}
    wildcards = data.get("wildcards") or {}
    meta      = data.get("meta")      or {}

    # 4) Normalize ranks for the report generator
    importance_subthemes = _normalize_ranks(data)

    normalized = {
        "email": email,
        "answers": answers,                         # { pillarKey: [{qIndex,text,value}, ...] }
        "wildcards": wildcards,                     # { id: "text", ... }
        "importance_subthemes": importance_subthemes,  # { pillarKey: [r1,r2,r3,r4] }
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
        # Still return 200; the PDF exists and can be re-sent.
        print("ERROR while emailing:", repr(e))

    return {"ok": True}
