# app.py — FastAPI entrypoint for Life Alignment API
# Tolerant /generate endpoint: accepts new per-subtheme ranks or older schemas.

import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# === adjust these imports to match your repo names ===
# This function must accept a normalized dict and return a path to the PDF.
from generate_report_json import build_pdf_report  # <-- you already have this working
# Your existing helper used earlier to send the PDF via Gmail App Password.
from mailer import send_email_with_attachment        # <-- you already have this working

# -------------------------------------------------------------------
# CORS: ALLOW YOUR WORDPRESS DOMAIN(S)
# -------------------------------------------------------------------
ALLOWED_ORIGINS = [
    "https://queensparkfitness.com",
    "https://www.queensparkfitness.com",
]

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["*"],
)


def _normalize_ranks(payload: dict) -> dict:
    """
    We now expect per-subtheme ranks, e.g.:
      {
        "health": [1,2,3,4],
        "wealth": [3,1,4,2],
        ...
      }
    But older front-ends might send a different shape. Be generous:

    Priority order:
      1) payload["importance_subthemes"] (explicit new name)
      2) payload["importance"]          (what our new front-end currently sends)
      3) fabricate neutral ranks [1,2,3,4] for each pillar (fallback)
    """
    explicit = payload.get("importance_subthemes")
    if isinstance(explicit, dict):
        return explicit

    newshape = payload.get("importance")
    if isinstance(newshape, dict):
        return newshape

    # Fallback: give each pillar a 1..4 list so the report code can proceed
    return {k: [1, 2, 3, 4] for k in ("health", "wealth", "self", "social")}


@app.post("/generate")
async def generate(request: Request):
    # 1) Parse JSON safely
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Bad JSON")

    # 2) Required email
    email = (data.get("email") or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="Missing email")

    # 3) Ratings, wildcards, meta (all optional but expected structures)
    answers   = data.get("answers")   or {}
    wildcards = data.get("wildcards") or {}
    meta      = data.get("meta")      or {}

    # 4) Normalize ranks so report builder can rely on a single shape
    importance_subthemes = _normalize_ranks(data)

    # 5) Build the normalized payload for your PDF generator
    normalized = {
        "email": email,
        "answers": answers,                        # { pillarKey: [{qIndex,text,value}, ...] }
        "wildcards": wildcards,                    # { id: "text", ... }
        "importance_subthemes": importance_subthemes,  # { pillarKey: [r1,r2,r3,r4] }
        "meta": meta,
    }

    # 6) Build the PDF
    try:
        pdf_path = build_pdf_report(normalized)  # must return a filesystem path
    except Exception as e:
        print("ERROR while building report:", repr(e))
        raise HTTPException(status_code=500, detail="Report generation failed")

    # 7) Email the PDF
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
        # We’ll still return 200, as the report was created; email can be re-sent.
        print("ERROR while emailing:", repr(e))

    return {"ok": True}
