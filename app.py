from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Optional
from datetime import datetime
import os, uuid

from generate_report_json import build_pdf_from_payload  # you create this next

app = FastAPI()

# Allow your WordPress domain to POST here
ALLOWED_ORIGINS = [
    "https://queensparkfitness.com",      
    "https://www.queensparkfitness.com",  
]
app.add_middleware(
    CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

class Submission(BaseModel):
    email: str
    submittedAt: str
    answers: Dict[str, list]            # pillar -> list[{qIndex, text, value}]
    wildcards: Dict[str, str]
    meta: Dict
    importance: Optional[Dict[str, int]] = None  # pillar -> 1..4 (optional)

@app.post("/generate")
def generate(sub: Submission):
    try:
        report_name = f"Life_Alignment_Report_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.pdf"
        out_path = os.path.join("/tmp", report_name)

        # Build the PDF from the payload (no Excel needed)
        build_pdf_from_payload(sub.dict(), out_path)

        # MVP: return a simple success. (Your page already shows “Thanks!” on 200 OK.)
        # Later: upload to S3/Cloudflare R2 and return a download_url, or email it.
        return {"ok": True, "file": report_name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
